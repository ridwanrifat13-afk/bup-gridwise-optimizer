"""Deterministic guardrails for LLM output (Problem Statement §08).

LLM output is untrusted. Each raw item is validated and normalised into the exact
directive_interpretation entry shape. Anything invalid is reported as an error so
the caller can retry the LLM or fall back; nothing unsupported ever reaches the optimizer.
"""
import math

DIRECTIVE_TYPES = (
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
)


class GuardrailError(ValueError):
    pass


def _finite(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(str(v).strip().rstrip("%"))
        except (TypeError, ValueError):
            raise GuardrailError(f"{name} is not a number")
    v = float(v)
    if not math.isfinite(v):
        raise GuardrailError(f"{name} is not finite")
    return v


def expand_ranges(ranges) -> list[int]:
    """[{start, end}] -> sorted unique hours; start inclusive, end exclusive, wraps midnight.

    A range with end == start (or end missing) means the single hour `start`.
    """
    if not isinstance(ranges, list) or not ranges:
        raise GuardrailError("no hours given")
    hours: set[int] = set()
    for r in ranges:
        if not isinstance(r, dict):
            raise GuardrailError("hour range must be an object")
        start = _finite(r.get("start"), "start")
        end = r.get("end")
        end = start + 1 if end is None else _finite(end, "end")
        if start != int(start) or end != int(end):
            raise GuardrailError("hours must be whole hours")
        start, end = int(start), int(end)
        if not (0 <= start <= 23 and 0 <= end <= 24):
            raise GuardrailError("hour out of range")
        if end == start:
            end = start + 1
        if end > start:
            hours.update(range(start, end))
        else:  # wraps past midnight, e.g. 22 -> 2
            hours.update(range(start, 24))
            hours.update(range(0, end))
    return sorted(hours)


def _hours_from_item(item: dict) -> list[int]:
    if item.get("hours_ranges"):
        return expand_ranges(item["hours_ranges"])
    hrs = item.get("hours")
    if isinstance(hrs, list) and hrs:
        out = set()
        for h in hrs:
            v = _finite(h, "hour")
            if v != int(v) or not 0 <= v <= 23:
                raise GuardrailError("hour out of range")
            out.add(int(v))
        return sorted(out)
    raise GuardrailError("no hours given")


def no_op_entry(note_index: int, explanation: str) -> dict:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": explanation,
    }


def normalize_item(item: dict, note_index: int, capacity_kwh: float) -> dict:
    """Validate one raw LLM item and return a spec-exact interpretation entry."""
    if not isinstance(item, dict):
        raise GuardrailError("item is not an object")
    t = item.get("directive_type")
    if t not in DIRECTIVE_TYPES:
        raise GuardrailError(f"unsupported directive_type {t!r}")
    explanation = str(item.get("explanation") or "").strip()[:300]

    if t == "no_op":
        return no_op_entry(note_index, explanation or "This note does not affect today's energy schedule.")

    hours = _hours_from_item(item)
    if t == "solar_reduction":
        f = _finite(item.get("factor"), "factor")
        if 1 < f <= 100:  # model gave a percentage of remaining output
            f = f / 100.0
        if not 0 <= f <= 1:
            raise GuardrailError("factor must be within [0, 1]")
        adj = {"hours": hours, "factor": round(f, 6)}
    elif t == "minimum_battery_reserve":
        kwh = item.get("minimum_energy_kwh")
        pct = item.get("reserve_percent_of_capacity")
        if kwh is None and pct is not None:
            p = _finite(pct, "reserve_percent_of_capacity")
            if 0 < p <= 1:
                p *= 100
            kwh = capacity_kwh * p / 100.0
        kwh = _finite(kwh, "minimum_energy_kwh")
        if kwh < 0 or kwh > capacity_kwh + 1e-9:
            raise GuardrailError("reserve must be within [0, capacity]")
        adj = {"hours": hours, "minimum_energy_kwh": round(kwh, 6)}
    elif t == "max_grid_window":
        cap = _finite(item.get("max_grid_kwh"), "max_grid_kwh")
        if cap < 0:
            raise GuardrailError("max_grid_kwh must be non-negative")
        adj = {"hours": hours, "max_grid_kwh": round(cap, 6)}
    else:  # no_charge_window / no_discharge_window
        adj = {"hours": hours}

    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": t,
        "structured_adjustment": adj,
        "explanation": explanation or f"Interpreted as {t}.",
    }


def normalize_all(raw, n_notes: int, capacity_kwh: float) -> tuple[list[dict | None], dict[int, str]]:
    """Map raw LLM output to one entry per note. Returns (entries, errors-by-note)."""
    items = raw.get("directives") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return [None] * n_notes, {i: "LLM output has no directives list" for i in range(n_notes)}

    by_index: dict[int, dict] = {}
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        idx = item.get("note_index", pos)
        if isinstance(idx, bool) or not isinstance(idx, (int, float)) or int(idx) != idx:
            continue
        idx = int(idx)
        if 0 <= idx < n_notes and idx not in by_index:  # duplicates: keep first
            by_index[idx] = item

    entries: list[dict | None] = []
    errors: dict[int, str] = {}
    for i in range(n_notes):
        if i not in by_index:
            entries.append(None)
            errors[i] = "missing interpretation"
            continue
        try:
            entries.append(normalize_item(by_index[i], i, capacity_kwh))
        except GuardrailError as e:
            entries.append(None)
            errors[i] = str(e)
    return entries, errors
