"""Rule-based SAFETY NET, used only when the LLM is unavailable or its output fails
guardrails twice. It is not the primary interpreter (the LLM is). It returns a raw
item in the same shape as the LLM output; guardrails validate it afterwards.
When unsure, it returns no_op rather than inventing a constraint.
"""
import re

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
FRACTIONS = [
    (r"one[- ]fifth|a fifth", 0.2), (r"one[- ]quarter|a quarter", 0.25), (r"three[- ]quarters", 0.75),
    (r"one[- ]third|a third", 1 / 3), (r"two[- ]thirds", 2 / 3), (r"\bhalf\b|halve[sd]?\b", 0.5),
]
OTHER_DAY = re.compile(r"\b(tomorrow|next (week|month|day|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
                       r"|yesterday|last (week|month|night)|next semester)\b")
T = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
RANGE = re.compile(T + r"\s*(?:to|until|till|through|thru|-|–|—|and)\s*" + T)
AFTER = re.compile(r"\b(?:after|from|starting(?: at)?|beginning(?: at)?)\s+" + T + r"(?!\s*(?:to|until|till|-|–|and)\s*\d)")
BEFORE = re.compile(r"\b(?:before|until|till)\s+" + T)
AT = re.compile(r"\b(?:at|during the)\s+" + T)


def _normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\bnoon\b|\bmidday\b", "12 pm", t)
    t = re.sub(r"\bmidnight\b", "12 am", t)
    for w, n in WORDS.items():
        t = re.sub(rf"\b{w}\b(?![- ](fifth|quarter|third))", str(n), t)
    return t


def _h24(h: int, ampm: str | None, default_pm: bool) -> int:
    if ampm:
        pm = ampm.startswith("p")
        if h == 12:
            return 12 if pm else 0
        return h + 12 if pm else h
    if h == 24:
        return 24
    if default_pm and 1 <= h <= 7:
        return h + 12
    return h


def _ranges(t: str, solar: bool) -> list[dict]:
    out = []
    for m in RANGE.finditer(t):
        h1, _, ap1, h2, _, ap2 = m.groups()
        h1, h2 = int(h1), int(h2)
        if h1 > 24 or h2 > 24:
            continue
        if ap2 and not ap1:  # "1-3 PM": start inherits unless that breaks ordering (e.g. 11-1 PM)
            ap1 = ap2 if not (ap2.startswith("p") and h1 > h2 and h1 != 12) else "am"
        s = _h24(h1, ap1, default_pm=True)
        e = _h24(h2, ap2, default_pm=True)
        if e == 0 and s > 0:
            e = 24
        out.append({"start": s, "end": e})
    if out:
        return out
    m = AFTER.search(t)
    if m:
        return [{"start": _h24(int(m.group(1)), m.group(3), True), "end": 24}]
    m = BEFORE.search(t)
    if m:
        return [{"start": 0, "end": _h24(int(m.group(1)), m.group(3), False)}]
    m = AT.search(t)
    if m:
        s = _h24(int(m.group(1)), m.group(3), True)
        return [{"start": s, "end": s + 1}]
    if re.search(r"\b(all day|whole day|entire day|24 hours|all hours)\b", t):
        return [{"start": 0, "end": 24}]
    return []


def _solar_factor(t: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t)
    if m:
        p = float(m.group(1)) / 100
        before = t[: m.start()]
        after = t[m.end(): m.end() + 25]
        if re.search(r"\b(by|cut|reduc\w*|lose|loss|lower|decrease\w*|drop of)\s*(about|around|roughly|nearly)?\s*$", before) \
                or re.search(r"^\s*(reduction|drop|decrease|loss|cut|lower)", after):
            return 1 - p
        return p
    for pat, f in FRACTIONS:
        if re.search(pat, t):
            return f
    if re.search(r"\b(offline|no solar|zero|shut ?down|disconnected|unavailable|covered)\b", t):
        return 0.0
    return None


def _number_near(t: str, unit=r"kwh|kw") -> float | None:
    m = re.search(rf"(\d+(?:\.\d+)?)\s*(?:{unit})\b", t)
    return float(m.group(1)) if m else None


def parse_note(note: str) -> dict:
    t = _normalize(note)
    no_op = {"directive_type": "no_op", "applies": False, "explanation": "No schedule-affecting rule detected."}
    if OTHER_DAY.search(t):
        return no_op

    solar = bool(re.search(r"\b(solar|pv|panel|photovoltaic|rooftop)", t))
    ranges = _ranges(t, solar)
    if not ranges:
        return no_op

    negative = bool(re.search(r"\b(not|no|never|avoid|prohibit\w*|block\w*|unavailable|disabled?|forbid\w*|must not|don't|cannot|can't|halt|suspend\w*|pause\w*)\b", t))
    pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t)

    if re.search(r"\b(grid|import\w*|purchas\w*|utility|draw from the grid|buy\w*)\b", t) and \
            re.search(r"\b(exceed|more than|limit\w*|cap\w*|at most|max\w*|below|under|no more)\b", t):
        cap = _number_near(t)
        if cap is not None:
            return {"directive_type": "max_grid_window", "applies": True, "hours_ranges": ranges,
                    "max_grid_kwh": cap, "explanation": "Grid import cap (fallback parser)."}
    if re.search(r"\b(reserve|at least|no less than|(drop|fall|go|dip)s? below|above|minimum|backup)\b", t) and \
            re.search(r"\bbatter|reserve|stored|charge level|state of charge|soc\b", t):
        kwh = _number_near(t, "kwh")
        if kwh is not None:
            return {"directive_type": "minimum_battery_reserve", "applies": True, "hours_ranges": ranges,
                    "minimum_energy_kwh": kwh, "explanation": "Battery reserve (fallback parser)."}
        if pct:
            return {"directive_type": "minimum_battery_reserve", "applies": True, "hours_ranges": ranges,
                    "reserve_percent_of_capacity": float(pct.group(1)), "explanation": "Battery reserve (fallback parser)."}
    if negative and re.search(r"\b(discharg\w*|draw\w* (from|on) the (battery|storage)|use the (battery|storage)|battery (use|output|supply)|battery must not supply|(battery|storage) (must|should|will|may) not (supply|power|feed))", t):
        return {"directive_type": "no_discharge_window", "applies": True, "hours_ranges": ranges,
                "explanation": "Battery discharge blocked (fallback parser)."}
    if negative and re.search(r"\b(charg\w*|recharg\w*|top(ping)? up|fill)", t):
        return {"directive_type": "no_charge_window", "applies": True, "hours_ranges": ranges,
                "explanation": "Battery charging blocked (fallback parser)."}
    if solar:
        f = _solar_factor(t)
        if f is not None:
            return {"directive_type": "solar_reduction", "applies": True, "hours_ranges": ranges,
                    "factor": f, "explanation": "Solar reduction (fallback parser)."}
    return no_op
