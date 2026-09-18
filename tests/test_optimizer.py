import pytest

from app.guardrails import normalize_item
from app.optimizer import optimize, totals
from app.schemas import parse_scenario
from app.validator import validate_plan
from tests.helpers import make_scenario


def _run(body, directives):
    sc = parse_scenario(body)
    plan, dropped = optimize(sc, directives)
    resp = {"hourly_plan": plan, **totals(plan, sc)}
    return sc, resp, dropped


def _d(item, cap=500):
    return normalize_item(item, 0, cap)


@pytest.mark.parametrize("seed", range(40))
def test_base_plans_are_valid_and_cheaper_than_idle(seed):
    body = make_scenario(seed)
    sc, resp, dropped = _run(body, [])
    assert dropped == []
    assert validate_plan(sc, [], resp) == []
    idle_cost = sum(max(0, h["demand_kwh"] - h["solar_kwh"]) * h["tariff_bdt_per_kwh"] for h in body["hours"])
    assert resp["total_cost_bdt"] <= idle_cost + 1e-6
    for p in resp["hourly_plan"]:
        assert p["battery_action"] in ("charge", "discharge", "idle")
        if p["battery_action"] == "idle":
            assert p["battery_kwh"] == 0


@pytest.mark.parametrize("seed", range(20))
def test_directives_are_enforced(seed):
    body = make_scenario(seed)
    cap = body["battery"]["capacity_kwh"]
    reserve = round(min(cap, body["battery"]["minimum_energy_kwh"] + 60), 1)
    directives = [
        _d({"directive_type": "solar_reduction", "hours_ranges": [{"start": 11, "end": 14}], "factor": 0.2}, cap),
        _d({"directive_type": "no_charge_window", "hours_ranges": [{"start": 14, "end": 16}]}, cap),
        _d({"directive_type": "no_discharge_window", "hours_ranges": [{"start": 6, "end": 8}]}, cap),
        _d({"directive_type": "minimum_battery_reserve", "hours_ranges": [{"start": 18, "end": 21}],
            "minimum_energy_kwh": reserve}, cap),
        _d({"directive_type": "max_grid_window", "hours_ranges": [{"start": 2, "end": 4}], "max_grid_kwh": 300}, cap),
    ]
    sc, resp, dropped = _run(body, directives)
    active = [d for i, d in enumerate(directives) if i not in dropped]
    assert validate_plan(sc, active, resp) == []
    if not dropped:
        for p in resp["hourly_plan"]:
            if p["hour"] in (14, 15):
                assert p["battery_action"] != "charge"
            if p["hour"] in (6, 7):
                assert p["battery_action"] != "discharge"


def test_infeasible_directive_is_dropped_not_crashing():
    body = make_scenario(3)
    cap = body["battery"]["capacity_kwh"]
    # grid cap of 0 all evening with huge demand is infeasible -> dropped
    d = _d({"directive_type": "max_grid_window", "hours_ranges": [{"start": 17, "end": 22}], "max_grid_kwh": 0}, cap)
    sc, resp, dropped = _run(body, [d])
    assert dropped == [0]
    assert validate_plan(sc, [], resp) == []
