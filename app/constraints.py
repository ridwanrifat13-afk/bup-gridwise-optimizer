"""Turn validated directives into per-hour effective constraints (Problem Statement §5.3)."""
from dataclasses import dataclass, field

from app.schemas import Scenario


@dataclass
class HourlyLimits:
    effective_solar: list[float]
    min_energy: list[float]            # max(base minimum, active reserve directives)
    max_charge: list[float]            # 0 inside no_charge_window hours
    max_discharge: list[float]         # 0 inside no_discharge_window hours
    grid_cap: list[float | None] = field(default_factory=lambda: [None] * 24)


def build_limits(scenario: Scenario, directives: list[dict]) -> HourlyLimits:
    """`directives` are guardrail-validated entries with applies=True."""
    b = scenario.battery
    factor = [1.0] * 24
    min_energy = [b.minimum_energy_kwh] * 24
    max_charge = [b.max_charge_kwh_per_hour] * 24
    max_discharge = [b.max_discharge_kwh_per_hour] * 24
    grid_cap: list[float | None] = [None] * 24

    for d in directives:
        t = d["directive_type"]
        adj = d["structured_adjustment"]
        for h in adj["hours"]:
            if t == "solar_reduction":
                factor[h] = min(factor[h], adj["factor"])
            elif t == "minimum_battery_reserve":
                min_energy[h] = max(min_energy[h], adj["minimum_energy_kwh"])
            elif t == "no_charge_window":
                max_charge[h] = 0.0
            elif t == "no_discharge_window":
                max_discharge[h] = 0.0
            elif t == "max_grid_window":
                cap = adj["max_grid_kwh"]
                grid_cap[h] = cap if grid_cap[h] is None else min(grid_cap[h], cap)

    eff_solar = [scenario.hours[h].solar_kwh * factor[h] for h in range(24)]
    return HourlyLimits(eff_solar, min_energy, max_charge, max_discharge, grid_cap)
