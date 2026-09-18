import math
import random


def make_scenario(seed: int = 0, notes=None, sid: str | None = None) -> dict:
    rnd = random.Random(seed)
    cap = rnd.choice([300, 400, 500, 800])
    minimum = round(cap * rnd.uniform(0.05, 0.2), 1)
    initial = round(rnd.uniform(minimum, cap * 0.8), 1)
    hours = []
    for h in range(24):
        sun = max(0.0, math.sin(math.pi * (h - 6) / 12))
        hours.append({
            "hour": h,
            "demand_kwh": round(rnd.uniform(120, 260) + (60 if 17 <= h <= 21 else 0), 1),
            "solar_kwh": round(sun * rnd.uniform(150, 320), 1),
            "tariff_bdt_per_kwh": 7 if h < 7 or h >= 23 else (12 if 17 <= h <= 22 else 9),
        })
    return {
        "scenario_id": sid or f"TEST-{seed}",
        "operator_notes": notes or ["The cafeteria menu changes tomorrow."],
        "hours": hours,
        "battery": {
            "capacity_kwh": cap,
            "initial_energy_kwh": initial,
            "minimum_energy_kwh": minimum,
            "max_charge_kwh_per_hour": rnd.choice([50, 80, 100]),
            "max_discharge_kwh_per_hour": rnd.choice([50, 80, 100]),
        },
    }
