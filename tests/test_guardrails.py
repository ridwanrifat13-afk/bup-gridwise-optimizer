import pytest

from app.guardrails import GuardrailError, expand_ranges, normalize_all, normalize_item


def test_expand_ranges():
    assert expand_ranges([{"start": 13, "end": 15}]) == [13, 14]
    assert expand_ranges([{"start": 22, "end": 2}]) == [0, 1, 22, 23]
    assert expand_ranges([{"start": 17, "end": 17}]) == [17]
    assert expand_ranges([{"start": 20, "end": 24}]) == [20, 21, 22, 23]
    assert expand_ranges([{"start": 1, "end": 3}, {"start": 2, "end": 4}]) == [1, 2, 3]


@pytest.mark.parametrize("bad", [
    {"directive_type": "shed_load", "hours_ranges": [{"start": 1, "end": 2}]},
    {"directive_type": "solar_reduction", "hours_ranges": [{"start": 1, "end": 2}], "factor": 1.7e9},
    {"directive_type": "solar_reduction", "hours_ranges": [{"start": 1, "end": 2}], "factor": -0.1},
    {"directive_type": "solar_reduction", "hours_ranges": [], "factor": 0.2},
    {"directive_type": "no_charge_window", "hours_ranges": [{"start": 25, "end": 26}]},
    {"directive_type": "minimum_battery_reserve", "hours_ranges": [{"start": 1, "end": 2}], "minimum_energy_kwh": 9999},
    {"directive_type": "max_grid_window", "hours_ranges": [{"start": 1, "end": 2}], "max_grid_kwh": -5},
    {"directive_type": "max_grid_window", "hours_ranges": [{"start": 1, "end": 2}], "max_grid_kwh": float("nan")},
    "garbage",
])
def test_rejects_invalid(bad):
    with pytest.raises(GuardrailError):
        normalize_item(bad, 0, 500)


def test_exact_shapes():
    e = normalize_item({"directive_type": "minimum_battery_reserve", "hours_ranges": [{"start": 18, "end": 21}],
                        "reserve_percent_of_capacity": 40, "factor": 0.3}, 0, 500)
    assert e == {"note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
                 "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 200.0},
                 "explanation": "Interpreted as minimum_battery_reserve."}
    n = normalize_item({"directive_type": "no_op", "applies": True, "hours_ranges": [{"start": 1, "end": 2}]}, 1, 500)
    assert n["applies"] is False and n["structured_adjustment"] is None


def test_normalize_all_handles_missing_duplicate_and_garbage():
    raw = {"directives": [
        {"note_index": 1, "directive_type": "no_op"},
        {"note_index": 1, "directive_type": "no_charge_window", "hours_ranges": [{"start": 1, "end": 2}]},
        {"note_index": 7, "directive_type": "no_op"},
    ]}
    entries, errors = normalize_all(raw, 3, 500)
    assert entries[0] is None and entries[2] is None and set(errors) == {0, 2}
    assert entries[1]["directive_type"] == "no_op"
    entries, errors = normalize_all("nonsense", 2, 500)
    assert entries == [None, None] and len(errors) == 2
