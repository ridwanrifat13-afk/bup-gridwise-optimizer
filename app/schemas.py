"""Request parsing / structural validation (Problem Statement §07).

Implemented by hand instead of relying on Pydantic error output so that we can
map failures to the exact status codes we want (400 structural, 422 semantic)
and never leak internals in error messages.
"""
import math
from dataclasses import dataclass


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Hour:
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float


@dataclass
class Battery:
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float


@dataclass
class Scenario:
    scenario_id: str
    operator_notes: list[str]
    hours: list[Hour]  # sorted by hour, 0..23
    battery: Battery
    profile_source: str = "custom"


def _num(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError(400, f"'{field}' must be a number")
    v = float(value)
    if not math.isfinite(v):
        raise RequestError(400, f"'{field}' must be finite")
    return v


FIELD_ALIASES = {
    "demand_kwh": ("demand", "demand_kw", "load"),
    "solar_kwh": ("solar", "solar_kw", "pv"),
    "tariff_bdt_per_kwh": ("tariff", "tariff_bdt", "price"),
}


def _extract_field(h: dict, canonical: str, aliases: tuple[str, ...], idx: int) -> float:
    has_canonical = canonical in h and h[canonical] is not None
    v_canonical = h[canonical] if has_canonical else None

    found_aliases = {}
    for a in aliases:
        if a in h and h[a] is not None:
            found_aliases[a] = h[a]

    if not has_canonical and not found_aliases:
        raise RequestError(400, f"hours[{idx}].{canonical} is missing")

    if has_canonical:
        c_num = _num(v_canonical, f"hours[{idx}].{canonical}")
        for a_name, a_val in found_aliases.items():
            a_num = _num(a_val, f"hours[{idx}].{a_name}")
            if abs(c_num - a_num) > 1e-6:
                raise RequestError(400, f"conflicting values for hours[{idx}].{canonical} and alias '{a_name}'")
        return c_num
    else:
        alias_items = []
        for a_name, a_val in found_aliases.items():
            a_num = _num(a_val, f"hours[{idx}].{a_name}")
            alias_items.append((a_name, a_num))
        first_name, first_num = alias_items[0]
        for a_name, a_num in alias_items[1:]:
            if abs(first_num - a_num) > 1e-6:
                raise RequestError(400, f"conflicting alias values for hours[{idx}].{canonical} ('{first_name}' vs '{a_name}')")
        return first_num


def normalize_and_validate_hours(hours_raw) -> list[Hour]:
    if not isinstance(hours_raw, list) or len(hours_raw) != 24:
        raise RequestError(400, "'hours' must be an array of exactly 24 entries")

    has_any_hour = any(isinstance(h, dict) and "hour" in h and h["hour"] is not None for h in hours_raw)
    hours: list[Hour] = []

    for i, h in enumerate(hours_raw):
        if not isinstance(h, dict):
            raise RequestError(400, f"hours[{i}] must be an object")

        if has_any_hour:
            if "hour" not in h or h["hour"] is None:
                raise RequestError(400, f"hours[{i}].hour is missing (required 0..23 when 'hour' is provided)")
            hr = h["hour"]
            if isinstance(hr, bool) or not isinstance(hr, (int, float)) or float(hr) != int(hr):
                raise RequestError(400, f"hours[{i}].hour must be an integer")
            hour_val = int(hr)
            if hour_val < 0 or hour_val > 23:
                raise RequestError(400, f"hours[{i}].hour must be in range 0..23")
        else:
            hour_val = i

        d_kwh = _extract_field(h, "demand_kwh", FIELD_ALIASES["demand_kwh"], i)
        s_kwh = _extract_field(h, "solar_kwh", FIELD_ALIASES["solar_kwh"], i)
        t_bdt = _extract_field(h, "tariff_bdt_per_kwh", FIELD_ALIASES["tariff_bdt_per_kwh"], i)

        hours.append(Hour(hour=hour_val, demand_kwh=d_kwh, solar_kwh=s_kwh, tariff_bdt_per_kwh=t_bdt))

    if has_any_hour:
        seen = set()
        for h in hours:
            if h.hour in seen:
                raise RequestError(400, f"duplicate hour {h.hour} in 'hours'")
            seen.add(h.hour)
        if len(seen) != 24 or set(range(24)) != seen:
            raise RequestError(400, "'hours' must contain each hour 0..23 exactly once")
        hours.sort(key=lambda h: h.hour)

    for h in hours:
        if h.demand_kwh < 0 or h.solar_kwh < 0:
            raise RequestError(422, f"hour {h.hour}: demand_kwh and solar_kwh must be non-negative")
        if h.tariff_bdt_per_kwh < 0:
            raise RequestError(422, f"hour {h.hour}: tariff_bdt_per_kwh must be non-negative")

    return hours


def parse_scenario(body) -> Scenario:
    if not isinstance(body, dict):
        raise RequestError(400, "request body must be a JSON object")
    # Accept a sample-pack case wrapper like {"id": ..., "label": ..., "input": {...request...}}.
    if "scenario_id" not in body:
        inner = next((body[k] for k in ("input", "request") if isinstance(body.get(k), dict)), None)
        if inner is not None:
            body = inner

    sid = body.get("scenario_id")
    if not isinstance(sid, str) or not sid.strip():
        raise RequestError(400, "'scenario_id' must be a non-empty string")

    profile_src = body.get("profile_source")
    if not isinstance(profile_src, str) or not profile_src.strip():
        profile_src = "custom"

    notes = body.get("operator_notes")
    if not isinstance(notes, list) or not (1 <= len(notes) <= 3):
        raise RequestError(400, "'operator_notes' must be an array of 1-3 strings")
    for n in notes:
        if not isinstance(n, str) or not n.strip():
            raise RequestError(400, "each operator note must be a non-empty string")

    hours = normalize_and_validate_hours(body.get("hours"))

    b = body.get("battery")
    if not isinstance(b, dict):
        raise RequestError(400, "'battery' must be an object")
    battery = Battery(
        capacity_kwh=_num(b.get("capacity_kwh"), "battery.capacity_kwh"),
        initial_energy_kwh=_num(b.get("initial_energy_kwh"), "battery.initial_energy_kwh"),
        minimum_energy_kwh=_num(b.get("minimum_energy_kwh"), "battery.minimum_energy_kwh"),
        max_charge_kwh_per_hour=_num(b.get("max_charge_kwh_per_hour"), "battery.max_charge_kwh_per_hour"),
        max_discharge_kwh_per_hour=_num(b.get("max_discharge_kwh_per_hour"), "battery.max_discharge_kwh_per_hour"),
    )

    for name in ("capacity_kwh", "initial_energy_kwh", "minimum_energy_kwh",
                 "max_charge_kwh_per_hour", "max_discharge_kwh_per_hour"):
        if getattr(battery, name) < 0:
            raise RequestError(422, f"battery.{name} must be non-negative")
    if battery.minimum_energy_kwh > battery.capacity_kwh + 1e-9:
        raise RequestError(422, "battery.minimum_energy_kwh exceeds capacity_kwh")
    if not (battery.minimum_energy_kwh - 1e-9 <= battery.initial_energy_kwh <= battery.capacity_kwh + 1e-9):
        raise RequestError(422, "battery.initial_energy_kwh must be within [minimum_energy_kwh, capacity_kwh]")

    return Scenario(
        scenario_id=sid,
        operator_notes=list(notes),
        hours=hours,
        battery=battery,
        profile_source=profile_src.strip(),
    )
