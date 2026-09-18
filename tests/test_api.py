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
    assert set(data) == {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh",
                         "total_cost_bdt", "peak_grid_kwh", "plan_summary"}
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
