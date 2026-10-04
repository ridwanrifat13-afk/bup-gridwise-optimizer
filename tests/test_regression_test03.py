"""Regression test suite for TEST-03 (known answer key from independent LP solver).

Validates:
1. Rule-based fallback path against the full answer key (table and totals, tolerance 0.01).
2. Mocked LLM path against the full answer key (table and totals, tolerance 0.01).
3. Negative checks expecting HTTP 400 naming the exact issue:
   a. Only 23 hours in load profile.
   b. hours[5].tariff_bdt_per_kwh missing.
   c. Duplicate entries for hour 7.
4. Positive check: Scenario written with alias keys (demand, solar, tariff) matches exactly.
"""
import copy
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

TEST_03_INPUT = {
    "id": "TEST-03",
    "label": "Solar dip, evening grid cap, time-bearing irrelevant note",
    "input": {
        "scenario_id": "GRID-303",
        "operator_notes": [
            "Solar output will drop to about 40% from 10 AM to 12 PM.",
            "Cap grid imports at 60 kWh from 6 PM to 10 PM.",
            "Staff lunch is moved to 1 PM tomorrow."
        ],
        "hours": [
            {"hour": 0, "demand_kwh": 40, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.0},
            {"hour": 1, "demand_kwh": 40, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.1},
            {"hour": 2, "demand_kwh": 40, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.2},
            {"hour": 3, "demand_kwh": 40, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.3},
            {"hour": 4, "demand_kwh": 40, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.4},
            {"hour": 5, "demand_kwh": 40, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.5},
            {"hour": 6, "demand_kwh": 60, "solar_kwh": 0, "tariff_bdt_per_kwh": 7.0},
            {"hour": 7, "demand_kwh": 60, "solar_kwh": 10, "tariff_bdt_per_kwh": 7.2},
            {"hour": 8, "demand_kwh": 60, "solar_kwh": 30, "tariff_bdt_per_kwh": 7.4},
            {"hour": 9, "demand_kwh": 70, "solar_kwh": 60, "tariff_bdt_per_kwh": 7.6},
            {"hour": 10, "demand_kwh": 70, "solar_kwh": 100, "tariff_bdt_per_kwh": 8.0},
            {"hour": 11, "demand_kwh": 70, "solar_kwh": 100, "tariff_bdt_per_kwh": 8.2},
            {"hour": 12, "demand_kwh": 70, "solar_kwh": 90, "tariff_bdt_per_kwh": 8.4},
            {"hour": 13, "demand_kwh": 70, "solar_kwh": 80, "tariff_bdt_per_kwh": 8.6},
            {"hour": 14, "demand_kwh": 70, "solar_kwh": 50, "tariff_bdt_per_kwh": 8.8},
            {"hour": 15, "demand_kwh": 70, "solar_kwh": 30, "tariff_bdt_per_kwh": 9.0},
            {"hour": 16, "demand_kwh": 70, "solar_kwh": 10, "tariff_bdt_per_kwh": 9.2},
            {"hour": 17, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 11.0},
            {"hour": 18, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 14.0},
            {"hour": 19, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 14.5},
            {"hour": 20, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 15.0},
            {"hour": 21, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 13.0},
            {"hour": 22, "demand_kwh": 45, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.8},
            {"hour": 23, "demand_kwh": 45, "solar_kwh": 0, "tariff_bdt_per_kwh": 4.6}
        ],
        "battery": {
            "capacity_kwh": 200,
            "initial_energy_kwh": 80,
            "minimum_energy_kwh": 20,
            "max_charge_kwh_per_hour": 50,
            "max_discharge_kwh_per_hour": 50
        }
    }
}

EXPECTED_ROWS = [
    # hour, demand, solar_used, grid, action, amt, end_energy
    (0, 40, 0, 90, "charge", 50, 130),
    (1, 40, 0, 90, "charge", 50, 180),
    (2, 40, 0, 60, "charge", 20, 200),
    (3, 40, 0, 40, "idle", 0, 200),
    (4, 40, 0, 40, "idle", 0, 200),
    (5, 40, 0, 40, "idle", 0, 200),
    (6, 60, 0, 60, "idle", 0, 200),
    (7, 60, 10, 50, "idle", 0, 200),
    (8, 60, 30, 30, "idle", 0, 200),
    (9, 70, 60, 10, "idle", 0, 200),
    (10, 70, 40, 30, "idle", 0, 200),
    (11, 70, 40, 0, "discharge", 30, 170),
    (12, 70, 90, 0, "charge", 20, 190),
    (13, 70, 80, 0, "charge", 10, 200),
    (14, 70, 50, 20, "idle", 0, 200),
    (15, 70, 30, 40, "idle", 0, 200),
    (16, 70, 10, 60, "idle", 0, 200),
    (17, 90, 0, 90, "idle", 0, 200),
    (18, 100, 0, 60, "discharge", 40, 160),
    (19, 100, 0, 50, "discharge", 50, 110),
    (20, 100, 0, 50, "discharge", 50, 60),
    (21, 100, 0, 60, "discharge", 40, 20),
    (22, 45, 0, 55, "charge", 10, 30),
    (23, 45, 0, 95, "charge", 50, 80),
]

EXPECTED_TOTAL_GRID = 1120.0
EXPECTED_TOTAL_COST = 8701.0
EXPECTED_PEAK_GRID = 95.0
EXPECTED_TOTAL_SOLAR_USED = 440.0
EXPECTED_TOTAL_CHARGE = 210.0
EXPECTED_TOTAL_DISCHARGE = 210.0
EXPECTED_FINAL_ENERGY = 80.0


def _assert_plan_and_totals(data: dict):
    plan = data["hourly_plan"]
    assert len(plan) == 24

    tot_solar = 0.0
    tot_ch = 0.0
    tot_dis = 0.0

    for i, exp in enumerate(EXPECTED_ROWS):
        h, dem, sol, grid, act, amt, ende = exp
        row = plan[i]
        assert row["hour"] == h
        assert abs(row["solar_used_kwh"] - sol) < 0.01, f"Hour {h}: solar_used mismatch"
        assert abs(row["grid_kwh"] - grid) < 0.01, f"Hour {h}: grid mismatch"
        assert row["battery_action"] == act, f"Hour {h}: action mismatch"
        assert abs(row["battery_kwh"] - amt) < 0.01, f"Hour {h}: amount mismatch"
        assert abs(row["battery_energy_after_kwh"] - ende) < 0.01, f"Hour {h}: end energy mismatch"

        tot_solar += row["solar_used_kwh"]
        if row["battery_action"] == "charge":
            tot_ch += row["battery_kwh"]
        elif row["battery_action"] == "discharge":
            tot_dis += row["battery_kwh"]

    assert abs(data["total_grid_kwh"] - EXPECTED_TOTAL_GRID) < 0.01
    assert abs(data["total_cost_bdt"] - EXPECTED_TOTAL_COST) < 0.01
    assert abs(data["peak_grid_kwh"] - EXPECTED_PEAK_GRID) < 0.01
    assert abs(tot_solar - EXPECTED_TOTAL_SOLAR_USED) < 0.01
    assert abs(tot_ch - EXPECTED_TOTAL_CHARGE) < 0.01
    assert abs(tot_dis - EXPECTED_TOTAL_DISCHARGE) < 0.01
    assert abs(plan[-1]["battery_energy_after_kwh"] - EXPECTED_FINAL_ENERGY) < 0.01

    assert data["profile_source"] == "custom"
    assert abs(data["input_total_demand_kwh"] - 1560.0) < 0.01
    assert abs(data["input_total_solar_kwh"] - 560.0) < 0.01
    assert abs(data["input_min_tariff_bdt"] - 4.0) < 0.01
    assert abs(data["input_max_tariff_bdt"] - 15.0) < 0.01


def test_test03_rule_based_fallback(monkeypatch):
    """Verify TEST-03 under rule-based fallback."""
    monkeypatch.setattr(config, "FORCE_FALLBACK", True)
    r = client.post("/optimize-energy", json=TEST_03_INPUT)
    assert r.status_code == 200
    data = r.json()
    assert data["interpreter_source"] == "rule_based_fallback"

    interp = data["directive_interpretation"]
    assert interp[0]["directive_type"] == "solar_reduction"
    assert interp[0]["structured_adjustment"]["hours"] == [10, 11]
    assert abs(interp[0]["structured_adjustment"]["factor"] - 0.4) < 0.01
    assert interp[1]["directive_type"] == "max_grid_window"
    assert interp[1]["structured_adjustment"]["hours"] == [18, 19, 20, 21]
    assert abs(interp[1]["structured_adjustment"]["max_grid_kwh"] - 60.0) < 0.01
    assert interp[2]["directive_type"] == "no_op"
    assert interp[2]["applies"] is False

    _assert_plan_and_totals(data)


def test_test03_mocked_llm_path(monkeypatch):
    """Verify TEST-03 under LLM interpreter path with mocked LLM response."""
    mock_reply = {
        "directives": [
            {
                "note_index": 0,
                "directive_type": "solar_reduction",
                "applies": True,
                "hours_ranges": [{"start": 10, "end": 12}],
                "factor": 0.4,
                "explanation": "Solar output reduced to 40% of normal from 10 AM to 12 PM."
            },
            {
                "note_index": 1,
                "directive_type": "max_grid_window",
                "applies": True,
                "hours_ranges": [{"start": 18, "end": 22}],
                "max_grid_kwh": 60.0,
                "explanation": "Grid import capped at 60 kWh per hour from 6 PM to 10 PM."
            },
            {
                "note_index": 2,
                "directive_type": "no_op",
                "applies": False,
                "explanation": "Staff lunch is moved to tomorrow; does not affect today's schedule."
            }
        ]
    }

    monkeypatch.setattr(config, "FORCE_FALLBACK", False)
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "mock-key")
    from app import llm
    monkeypatch.setattr(llm, "_call_openrouter", lambda *args, **kwargs: mock_reply)
    llm._cache.clear()

    r = client.post("/optimize-energy", json=TEST_03_INPUT)
    assert r.status_code == 200
    data = r.json()
    assert data["interpreter_source"] == "llm"

    interp = data["directive_interpretation"]
    assert interp[0]["directive_type"] == "solar_reduction"
    assert interp[0]["structured_adjustment"]["hours"] == [10, 11]
    assert abs(interp[0]["structured_adjustment"]["factor"] - 0.4) < 0.01
    assert interp[1]["directive_type"] == "max_grid_window"
    assert interp[1]["structured_adjustment"]["hours"] == [18, 19, 20, 21]
    assert abs(interp[1]["structured_adjustment"]["max_grid_kwh"] - 60.0) < 0.01
    assert interp[2]["directive_type"] == "no_op"
    assert interp[2]["applies"] is False

    _assert_plan_and_totals(data)


def test_negative_only_23_hours():
    """Negative check: Scenario with only 23 hours must return 400 naming the problem."""
    case = copy.deepcopy(TEST_03_INPUT)
    case["input"]["hours"] = case["input"]["hours"][:23]
    r = client.post("/optimize-energy", json=case)
    assert r.status_code == 400
    err = r.json()["error"]
    assert "24" in err and "hours" in err


def test_negative_missing_hour_5_tariff():
    """Negative check: Scenario missing hours[5].tariff_bdt_per_kwh must return 400 naming the field."""
    case = copy.deepcopy(TEST_03_INPUT)
    del case["input"]["hours"][5]["tariff_bdt_per_kwh"]
    r = client.post("/optimize-energy", json=case)
    assert r.status_code == 400
    err = r.json()["error"]
    assert "hours[5].tariff_bdt_per_kwh" in err


def test_negative_duplicate_hour_7():
    """Negative check: Scenario with two hour 7 entries must return 400 naming duplicate hour 7."""
    case = copy.deepcopy(TEST_03_INPUT)
    case["input"]["hours"][6]["hour"] = 7
    r = client.post("/optimize-energy", json=case)
    assert r.status_code == 400
    err = r.json()["error"]
    assert "duplicate hour 7" in err


def test_positive_alias_keys():
    """Positive check: Same scenario with alias keys demand, solar, tariff yields exact same result."""
    case = copy.deepcopy(TEST_03_INPUT)
    for h in case["input"]["hours"]:
        h["demand"] = h.pop("demand_kwh")
        h["solar"] = h.pop("solar_kwh")
        h["tariff"] = h.pop("tariff_bdt_per_kwh")

    r = client.post("/optimize-energy", json=case)
    assert r.status_code == 200
    data = r.json()
    _assert_plan_and_totals(data)
