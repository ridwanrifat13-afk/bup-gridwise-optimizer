"""API contract tests. The LLM is disabled (no key) so the deterministic safety net is used."""
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.schemas import parse_scenario
from app.validator import validate_interpretation, validate_plan
from tests.helpers import make_scenario

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_full_response_contract():
    notes = [
        "Solar output will drop to about 20% from 1 PM to 3 PM.",
        "Do not charge the battery between 2 PM and 4 PM.",
        "The cafeteria menu changes tomorrow.",
    ]
    body = make_scenario(7, notes=notes, sid="GRID-101")
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {
        "scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh",
        "total_cost_bdt", "peak_grid_kwh", "plan_summary", "profile_source",
        "interpreter_source", "input_total_demand_kwh", "input_total_solar_kwh",
        "input_min_tariff_bdt", "input_max_tariff_bdt"
    }
    assert data["scenario_id"] == "GRID-101"
    interp = data["directive_interpretation"]
    assert validate_interpretation(interp, 3) == []
    assert interp[0]["structured_adjustment"] == {"hours": [13, 14], "factor": 0.2}
    assert interp[1]["structured_adjustment"] == {"hours": [14, 15]}
    assert interp[2]["directive_type"] == "no_op" and interp[2]["structured_adjustment"] is None
    sc = parse_scenario(body)
    assert validate_plan(sc, [e for e in interp if e["applies"]], data) == []
    for p in data["hourly_plan"]:
        assert set(p) == {"hour", "grid_kwh", "solar_used_kwh", "battery_action", "battery_kwh",
                          "battery_energy_after_kwh"}


def test_hours_out_of_order_are_accepted():
    body = make_scenario(2)
    body["hours"].reverse()
    assert client.post("/optimize-energy", json=body).status_code == 200


def test_sample_case_wrapper_is_unwrapped():
    body = make_scenario(7, notes=["The cafeteria menu changes tomorrow."], sid="SAMPLE-01")
    r = client.post("/optimize-energy", json={"id": "SAMPLE-01", "label": "demo", "input": body})
    assert r.status_code == 200
    assert r.json()["scenario_id"] == "SAMPLE-01"


def test_malformed_json():
    r = client.post("/optimize-energy", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


@pytest.mark.parametrize("mutate", [
    lambda b: b.pop("hours"),
    lambda b: b["hours"].pop(),
    lambda b: b.update(operator_notes=[]),
    lambda b: b.update(operator_notes=["a", "b", "c", "d"]),
    lambda b: b.update(operator_notes=[""]),
    lambda b: b.update(operator_notes="just a string"),
    lambda b: b["battery"].pop("capacity_kwh"),
    lambda b: b["hours"][3].update(demand_kwh="lots"),
    lambda b: b["hours"][3].update(hour=4),
    lambda b: b.pop("scenario_id"),
])
def test_structural_errors_400(mutate):
    body = make_scenario(1)
    mutate(body)
    assert client.post("/optimize-energy", json=body).status_code == 400


def test_semantic_error_422():
    body = make_scenario(1)
    body["hours"][5]["demand_kwh"] = -10
    assert client.post("/optimize-energy", json=body).status_code == 422


def test_non_object_body():
    assert client.post("/optimize-energy", json=[1, 2]).status_code == 400


def test_canonical_and_aliases_accepted():
    body = make_scenario(1)
    # Hour 0 uses canonical keys
    body["hours"][0] = {"hour": 0, "demand_kwh": 100.0, "solar_kwh": 20.0, "tariff_bdt_per_kwh": 8.0}
    # Hour 1 uses aliases (load, pv, price)
    body["hours"][1] = {"hour": 1, "load": 110.0, "pv": 15.0, "price": 9.0}
    # Hour 2 uses other aliases (demand, solar, tariff)
    body["hours"][2] = {"hour": 2, "demand": 120.0, "solar": 25.0, "tariff": 10.0}
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 200
    data = r.json()
    assert len(data["hourly_plan"]) == 24
    # Parse directly to verify normalized hours
    scenario = parse_scenario(body)
    assert scenario.hours[0].demand_kwh == 100.0
    assert scenario.hours[1].demand_kwh == 110.0
    assert scenario.hours[2].demand_kwh == 120.0
    assert scenario.hours[1].solar_kwh == 15.0
    assert scenario.hours[1].tariff_bdt_per_kwh == 9.0
    assert data["input_total_demand_kwh"] == round(sum(h.demand_kwh for h in scenario.hours), 4)


def test_conflicting_canonical_and_alias_gives_400():
    body = make_scenario(1)
    body["hours"][3] = {"hour": 3, "demand_kwh": 100.0, "demand": 150.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 8.0}
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 400
    assert "conflicting values for hours[3].demand_kwh and alias 'demand'" in r.json()["error"]


def test_missing_field_gives_400_naming_exact_path():
    body = make_scenario(1)
    # Remove tariff from hour 5
    body["hours"][5].pop("tariff_bdt_per_kwh")
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 400
    assert "hours[5].tariff_bdt_per_kwh is missing" in r.json()["error"]


def test_23_entries_gives_400():
    body = make_scenario(1)
    body["hours"].pop()  # now 23 entries
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 400
    assert "'hours' must be an array of exactly 24 entries" in r.json()["error"]


def test_duplicate_hour_gives_400():
    body = make_scenario(1)
    body["hours"][10]["hour"] = 9  # duplicate hour 9
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 400
    assert "duplicate hour 9" in r.json()["error"]


def test_optional_hour_absent_uses_array_order():
    body = make_scenario(1)
    for h in body["hours"]:
        h.pop("hour")  # remove 'hour' key completely
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 200
    plan = r.json()["hourly_plan"]
    assert len(plan) == 24
    for i, p in enumerate(plan):
        assert p["hour"] == i


def test_profile_source_and_input_totals_echo():
    body = make_scenario(1, sid="CUSTOM-SCENARIO")
    body["profile_source"] = "custom"
    # set predictable values
    for i, h in enumerate(body["hours"]):
        h["demand_kwh"] = 10.0
        h["solar_kwh"] = 5.0
        h["tariff_bdt_per_kwh"] = 7.0 if i < 12 else 14.0
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["profile_source"] == "custom"
    assert data["interpreter_source"] == "rule_based_fallback"
    assert data["input_total_demand_kwh"] == 240.0
    assert data["input_total_solar_kwh"] == 120.0
    assert data["input_min_tariff_bdt"] == 7.0
    assert data["input_max_tariff_bdt"] == 14.0

