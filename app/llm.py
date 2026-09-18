"""Gemini-based operator-note interpreter with guardrail-driven retry and safe fallbacks."""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict

from app import config
from app.fallback_parser import parse_note
from app.guardrails import GuardrailError, no_op_entry, normalize_all, normalize_item
from app.prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, user_message

log = logging.getLogger("gridwise.llm")

_client = None
_client_lock = threading.Lock()
_cache: "OrderedDict[str, list[dict]]" = OrderedDict()
_CACHE_MAX = 512


def _get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from google import genai
                _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _call_gemini(model: str, notes: list[str], capacity: float, feedback: str | None, timeout_s: float) -> dict:
    from google.genai import types

    cfg = dict(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=0.0,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
    )
    if config.GEMINI_THINKING_BUDGET >= 0 and "2.5" in model:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=config.GEMINI_THINKING_BUDGET)
    resp = _get_client().models.generate_content(
        model=model,
        contents=user_message(notes, capacity, feedback),
        config=types.GenerateContentConfig(**cfg),
    )
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    return json.loads(resp.text)


def _llm_raw(notes: list[str], capacity: float, feedback: str | None, deadline: float) -> dict | None:
    """Try primary model, then fallback model on provider errors. Returns raw JSON or None."""
    if not config.GEMINI_API_KEY:
        log.error("GEMINI_API_KEY is not set; using fallback interpreter")
        return None
    models = [config.GEMINI_MODEL]
    if config.GEMINI_FALLBACK_MODEL and config.GEMINI_FALLBACK_MODEL != config.GEMINI_MODEL:
        models.append(config.GEMINI_FALLBACK_MODEL)
    for model in models:
        remaining = deadline - time.monotonic()
        if remaining < 1.5:
            break
        try:
            return _call_gemini(model, notes, capacity, feedback, min(config.LLM_TIMEOUT_SECONDS, remaining))
        except Exception as e:  # provider/network/JSON errors: never propagate details
            log.warning("LLM call failed (model=%s, error=%s)", model, type(e).__name__)
    return None


def interpret_notes(notes: list[str], capacity: float) -> tuple[list[dict], dict]:
    """Return (directive_interpretation entries in note order, metadata)."""
    key = hashlib.sha256(json.dumps([notes, capacity]).encode()).hexdigest()
    if key in _cache:
        _cache.move_to_end(key)
        return json.loads(json.dumps(_cache[key])), {"source": "cache"}

    deadline = time.monotonic() + config.LLM_TOTAL_BUDGET_SECONDS
    n = len(notes)
    entries: list[dict | None] = [None] * n
    errors: dict[int, str] = {i: "not interpreted" for i in range(n)}
    source = "llm"

    feedback = None
    for attempt in range(2):
        raw = _llm_raw(notes, capacity, feedback, deadline)
        if raw is None:
            break
        new_entries, new_errors = normalize_all(raw, n, capacity)
        for i, e in enumerate(new_entries):
            if entries[i] is None and e is not None:
                entries[i] = e
                errors.pop(i, None)
        for i, msg in new_errors.items():
            if entries[i] is None:
                errors[i] = msg
        if not errors:
            break
        feedback = "; ".join(f"note {i}: {m}" for i, m in sorted(errors.items()))
        log.info("guardrails rejected LLM output (attempt %d): %s", attempt + 1, feedback)

    # Safety net: only for notes the LLM could not produce a valid directive for.
    missing = [i for i in range(n) if entries[i] is None]
    if missing:
        source = "fallback" if len(missing) == n else "llm+fallback"
    for i in missing:
        if entries[i] is None:
            try:
                entries[i] = normalize_item(parse_note(notes[i]), i, capacity)
            except GuardrailError:
                entries[i] = no_op_entry(i, "Could not be interpreted safely; treated as not affecting the schedule.")

    if source == "llm":
        _cache[key] = entries
        if len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return entries, {"source": source}
