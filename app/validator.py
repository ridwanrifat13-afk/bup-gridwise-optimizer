"""Independent replay of a response, mirroring the judge (Problem Statement §09, §11).

Used on every response (violations are logged) and by the test-suite.
"""
import math

from app.constraints import build_limits
from app.schemas import Scenario

TOL = 0.01
ADJ_KEYS = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "no_charge_window": {"hours"},
    "no_discharge_window": {"hours"},
    "max_grid_window": {"hours", "max_grid_kwh"},
}


def validate_interpretation(entries: list[dict], n_notes: int) -> list[str]:
    errs = []
    if [e.get("note_index") for e in entries] != list(range(n_notes)):
        errs.append("note_index must be 0..N-1 in order")
    for e in entries:
        t = e.get("directive_type")
        adj = e.get("structured_adjustment")
        if t == "no_op":
            if e.get("applies") is not False or adj is not None:
                errs.append(f"note {e.get('note_index')}: no_op must have applies=false, adjustment=null")
            continue
        if t not in ADJ_KEYS:
            errs.append(f"note {e.get('note_index')}: unsupported type {t}")
            continue
        if e.get("applies") is not True:
            errs.append(f"note {e.get('note_index')}: applies must be true")
        if not isinstance(adj, dict) or set(adj) != ADJ_KEYS[t]:
            errs.append(f"note {e.get('note_index')}: bad structured_adjustment shape")
            continue
        hrs = adj["hours"]
        if not hrs or hrs != sorted(set(hrs)) or any(not isinstance(h, int) or not 0 <= h <= 23 for h in hrs):
            errs.append(f"note {e.get('note_index')}: bad hours")
    return errs


def validate_plan(scenario: Scenario, directives: list[dict], response: dict) -> list[str]:
    """`directives` = applied directives (the ground truth, in tests)."""
    errs = []
    lim = build_limits(scenario, directives)
    b = scenario.battery
    plan = response["hourly_plan"]
    if [p["hour"] for p in plan] != list(range(24)):
        return ["hourly_plan must have hours 0..23 in order"]

    energy = b.initial_energy_kwh
    for p in plan:
        h = p["hour"]
        vals = [p["grid_kwh"], p["solar_used_kwh"], p["battery_kwh"], p["battery_energy_after_kwh"]]
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < -TOL for v in vals):
            errs.append(f"h{h}: negative or non-finite value")
        act, amt = p["battery_action"], p["battery_kwh"]
        if act == "charge":
            energy += amt
            if amt > lim.max_charge[h] + TOL:
                errs.append(f"h{h}: charge {amt} > limit {lim.max_charge[h]}")
        elif act == "discharge":
            energy -= amt
            if amt > lim.max_discharge[h] + TOL:
                errs.append(f"h{h}: discharge {amt} > limit {lim.max_discharge[h]}")
        elif act == "idle":
            if abs(amt) > TOL:
                errs.append(f"h{h}: idle with battery_kwh != 0")
        else:
            errs.append(f"h{h}: bad battery_action {act}")
        if abs(energy - p["battery_energy_after_kwh"]) > TOL:
            errs.append(f"h{h}: battery_energy_after mismatch ({p['battery_energy_after_kwh']} vs {energy:.4f})")
        if energy < lim.min_energy[h] - TOL or energy > b.capacity_kwh + TOL:
            errs.append(f"h{h}: energy {energy:.4f} outside [{lim.min_energy[h]}, {b.capacity_kwh}]")
        if p["solar_used_kwh"] > lim.effective_solar[h] + TOL:
            errs.append(f"h{h}: solar {p['solar_used_kwh']} > effective {lim.effective_solar[h]:.4f}")
        charge = amt if act == "charge" else 0.0
        discharge = amt if act == "discharge" else 0.0
        lhs = p["grid_kwh"] + p["solar_used_kwh"] + discharge
        rhs = scenario.hours[h].demand_kwh + charge
        if abs(lhs - rhs) > TOL:
            errs.append(f"h{h}: energy balance {lhs:.4f} != {rhs:.4f}")
        if lim.grid_cap[h] is not None and p["grid_kwh"] > lim.grid_cap[h] + TOL:
            errs.append(f"h{h}: grid {p['grid_kwh']} > cap {lim.grid_cap[h]}")

    if abs(energy - b.initial_energy_kwh) > TOL:
        errs.append(f"final energy {energy:.4f} != initial {b.initial_energy_kwh}")

    grid = sum(p["grid_kwh"] for p in plan)
    cost = sum(p["grid_kwh"] * scenario.hours[p["hour"]].tariff_bdt_per_kwh for p in plan)
    peak = max(p["grid_kwh"] for p in plan)
    if abs(grid - response["total_grid_kwh"]) > TOL:
        errs.append("total_grid_kwh mismatch")
    if abs(cost - response["total_cost_bdt"]) > TOL:
        errs.append("total_cost_bdt mismatch")
    if abs(peak - response["peak_grid_kwh"]) > TOL:
        errs.append("peak_grid_kwh mismatch")
    return errs
