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


def _num(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError(400, f"'{field}' must be a number")
    v = float(value)
    if not math.isfinite(v):
        raise RequestError(400, f"'{field}' must be finite")
    return v


def parse_scenario(body) -> Scenario:
    if not isinstance(body, dict):
        raise RequestError(400, "request body must be a JSON object")

    sid = body.get("scenario_id")
    if not isinstance(sid, str) or not sid.strip():
        raise RequestError(400, "'scenario_id' must be a non-empty string")

    notes = body.get("operator_notes")
    if not isinstance(notes, list) or not (1 <= len(notes) <= 3):
        raise RequestError(400, "'operator_notes' must be an array of 1-3 strings")
    for n in notes:
        if not isinstance(n, str) or not n.strip():
            raise RequestError(400, "each operator note must be a non-empty string")

    hours_raw = body.get("hours")
    if not isinstance(hours_raw, list) or len(hours_raw) != 24:
        raise RequestError(400, "'hours' must be an array of exactly 24 entries")
    hours: list[Hour] = []
    for i, h in enumerate(hours_raw):
        if not isinstance(h, dict):
            raise RequestError(400, f"hours[{i}] must be an object")
        hr = h.get("hour")
        if isinstance(hr, bool) or not isinstance(hr, (int, float)) or float(hr) != int(hr):
            raise RequestError(400, f"hours[{i}].hour must be an integer")
        hours.append(
            Hour(
                hour=int(hr),
                demand_kwh=_num(h.get("demand_kwh"), f"hours[{i}].demand_kwh"),
                solar_kwh=_num(h.get("solar_kwh"), f"hours[{i}].solar_kwh"),
                tariff_bdt_per_kwh=_num(h.get("tariff_bdt_per_kwh"), f"hours[{i}].tariff_bdt_per_kwh"),
            )
        )
    if sorted(h.hour for h in hours) != list(range(24)):
        raise RequestError(400, "'hours' must contain each hour 0..23 exactly once")
    hours.sort(key=lambda h: h.hour)

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

    # Semantic checks (well-formed but impossible) -> 422
    for h in hours:
        if h.demand_kwh < 0 or h.solar_kwh < 0:
            raise RequestError(422, f"hour {h.hour}: demand_kwh and solar_kwh must be non-negative")
    for name in ("capacity_kwh", "initial_energy_kwh", "minimum_energy_kwh",
                 "max_charge_kwh_per_hour", "max_discharge_kwh_per_hour"):
        if getattr(battery, name) < 0:
            raise RequestError(422, f"battery.{name} must be non-negative")
    if battery.minimum_energy_kwh > battery.capacity_kwh + 1e-9:
        raise RequestError(422, "battery.minimum_energy_kwh exceeds capacity_kwh")
    if not (battery.minimum_energy_kwh - 1e-9 <= battery.initial_energy_kwh <= battery.capacity_kwh + 1e-9):
        raise RequestError(422, "battery.initial_energy_kwh must be within [minimum_energy_kwh, capacity_kwh]")

    return Scenario(scenario_id=sid, operator_notes=list(notes), hours=hours, battery=battery)
