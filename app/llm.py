"""LLM-based operator-note interpreter with guardrail-driven retry and safe fallbacks."""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict

import httpx

from app import config
from app.fallback_parser import parse_note
from app.guardrails import GuardrailError, no_op_entry, normalize_all, normalize_item
from app.prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, user_message

log = logging.getLogger("gridwise.llm")

_client = None
_client_lock = threading.Lock()
_cache: "OrderedDict[str, list[dict]]" = OrderedDict()
_CACHE_MAX = 512

SCHEMA_INSTRUCTION = """
CRITICAL JSON FORMAT REQUIREMENT:
You must output a single valid JSON object with a single top-level key "directives".
"directives" must be an array of objects, one for each input note in exact order:
{
  "directives": [
    {
      "note_index": 0,
      "directive_type": "solar_reduction" | "minimum_battery_reserve" | "no_charge_window" | "no_discharge_window" | "max_grid_window" | "no_op",
      "applies": true | false,
      "hours_ranges": [{"start": int, "end": int}] | null,
      "factor": float | null,
      "minimum_energy_kwh": float | null,
      "reserve_percent_of_capacity": float | null,
      "max_grid_kwh": float | null,
      "explanation": "short explanation"
    }
  ]
}
"""


def _get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from google import genai
                _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _call_openrouter(model: str, notes: list[str], capacity: float, feedback: str | None, timeout_s: float) -> dict:
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://gridwise-optimizer.local",
        "X-Title": "GridWise Energy Optimizer",
    }
    system_content = f"{SYSTEM_PROMPT}\n{SCHEMA_INSTRUCTION}"
    user_content = user_message(notes, capacity, feedback)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 1500,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        if resp.status_code != 200:
            log.warning("OpenRouter call failed (status=%d, text=%s)", resp.status_code, resp.text[:200])
            raise RuntimeError(f"OpenRouter HTTP {resp.status_code}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(cleaned)


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
    if getattr(config, "FORCE_FALLBACK", False):
        log.info("FORCE_FALLBACK is set; using fallback interpreter")
        return None

    if config.OPENROUTER_API_KEY:
        models = [config.OPENROUTER_MODEL]
        if config.OPENROUTER_FALLBACK_MODEL and config.OPENROUTER_FALLBACK_MODEL != config.OPENROUTER_MODEL:
            models.append(config.OPENROUTER_FALLBACK_MODEL)
        for model in models:
            remaining = deadline - time.monotonic()
            if remaining < 1.5:
                break
            try:
                return _call_openrouter(model, notes, capacity, feedback, min(config.LLM_TIMEOUT_SECONDS, remaining))
            except Exception as e:
                log.warning("OpenRouter call failed (model=%s, error=%s)", model, type(e).__name__)
        return None

    if config.GEMINI_API_KEY:
        models = [config.GEMINI_MODEL]
        if config.GEMINI_FALLBACK_MODEL and config.GEMINI_FALLBACK_MODEL != config.GEMINI_MODEL:
            models.append(config.GEMINI_FALLBACK_MODEL)
        for model in models:
            remaining = deadline - time.monotonic()
            if remaining < 1.5:
                break
            try:
                return _call_gemini(model, notes, capacity, feedback, min(config.LLM_TIMEOUT_SECONDS, remaining))
            except Exception as e:
                log.warning("LLM call failed (model=%s, error=%s)", model, type(e).__name__)
        return None

    log.info("No LLM API key configured; using fallback interpreter")
    return None


PROMPT_VERSION = "v1.1"


def interpret_notes(notes: list[str], capacity: float) -> tuple[list[dict], dict]:
    """Return (directive_interpretation entries in note order, metadata)."""
    if getattr(config, "FORCE_FALLBACK", False):
        n = len(notes)
        entries = []
        for i in range(n):
            try:
                entries.append(normalize_item(parse_note(notes[i]), i, capacity))
            except GuardrailError:
                entries.append(no_op_entry(i, "Could not be interpreted safely; treated as not affecting the schedule."))
        return entries, {"source": "fallback"}

    active_model = config.OPENROUTER_MODEL if config.OPENROUTER_API_KEY else config.GEMINI_MODEL
    cache_payload = [notes, capacity, active_model, PROMPT_VERSION]
    key = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode()).hexdigest()
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
