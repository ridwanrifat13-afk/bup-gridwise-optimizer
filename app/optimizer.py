"""Linear-programming scheduler (scipy HiGHS).

Variables, per hour h (5 x 24 = 120):
    g[h] grid import, s[h] solar used, c[h] battery charge, d[h] battery discharge,
    e[h] battery energy after hour h.

Stage 1: minimise sum(tariff[h] * g[h]).
Stage 2: keep cost <= optimum and minimise sum(c + d) so the plan has no
         simultaneous charge/discharge and no pointless cycling.
"""
from itertools import combinations

import numpy as np
from scipy.optimize import linprog

from app.constraints import HourlyLimits, build_limits
from app.schemas import Scenario

H = 24
G, S, C, D, E = (i * H for i in range(5))
N = 5 * H
ROUND = 6


def _solve_lp(scenario: Scenario, lim: HourlyLimits):
    b = scenario.battery
    demand = [h.demand_kwh for h in scenario.hours]
    tariff = np.array([h.tariff_bdt_per_kwh for h in scenario.hours])

    a_eq = np.zeros((2 * H, N))
    b_eq = np.zeros(2 * H)
    for h in range(H):
        # energy balance: g + s + d - c = demand
        a_eq[h, G + h] = 1
        a_eq[h, S + h] = 1
        a_eq[h, D + h] = 1
        a_eq[h, C + h] = -1
        b_eq[h] = demand[h]
        # battery transition: e[h] - e[h-1] - c + d = 0   (e[-1] = initial)
        r = H + h
        a_eq[r, E + h] = 1
        a_eq[r, C + h] = -1
        a_eq[r, D + h] = 1
        if h > 0:
            a_eq[r, E + h - 1] = -1
        else:
            b_eq[r] = b.initial_energy_kwh

    bounds = []
    bounds += [(0, lim.grid_cap[h]) for h in range(H)]
    bounds += [(0, lim.effective_solar[h]) for h in range(H)]
    bounds += [(0, lim.max_charge[h]) for h in range(H)]
    bounds += [(0, lim.max_discharge[h]) for h in range(H)]
    for h in range(H):
        lo, hi = lim.min_energy[h], b.capacity_kwh
        if h == H - 1:  # end-of-day neutrality
            if not (lo - 1e-9 <= b.initial_energy_kwh <= hi + 1e-9):
                return None
            lo = hi = b.initial_energy_kwh
        if lo > hi + 1e-9:
            return None
        bounds.append((lo, hi))

    cost = np.zeros(N)
    cost[G:G + H] = tariff
    r1 = linprog(cost, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if r1.status != 0:
        return None

    cycling = np.zeros(N)
    cycling[C:C + H] = 1
    cycling[D:D + H] = 1
    r2 = linprog(
        cycling,
        A_ub=cost.reshape(1, -1),
        b_ub=[r1.fun + 1e-7 + 1e-9 * abs(r1.fun)],
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
    )
    return r2.x if r2.status == 0 else r1.x


def _to_plan(scenario: Scenario, lim: HourlyLimits, x) -> list[dict]:
    """Convert LP solution into hourly_plan with exact accounting (replayed, not copied)."""
    b = scenario.battery
    energy = b.initial_energy_kwh
    plan = []
    for h in range(H):
        demand = scenario.hours[h].demand_kwh
        net = float(x[C + h] - x[D + h]) if x is not None else 0.0
        if net > 1e-7:
            action, amount = "charge", round(min(net, lim.max_charge[h]), ROUND)
        elif net < -1e-7:
            action, amount = "discharge", round(min(-net, lim.max_discharge[h]), ROUND)
        else:
            action, amount = "idle", 0.0
        if amount == 0.0:
            action = "idle"

        signed = amount if action == "charge" else -amount if action == "discharge" else 0.0
        need = demand + signed  # grid + solar must cover this
        solar = float(x[S + h]) if x is not None else lim.effective_solar[h]
        solar = max(0.0, min(round(solar, ROUND), lim.effective_solar[h], need))
        grid = round(need - solar, ROUND)
        if grid < 0:  # rounding dust
            solar = round(solar + grid, ROUND)
            grid = 0.0
        energy = round(energy + signed, ROUND)
        plan.append({
            "hour": h,
            "grid_kwh": grid,
            "solar_used_kwh": solar,
            "battery_action": action,
            "battery_kwh": amount,
            "battery_energy_after_kwh": energy,
        })
    return plan


def optimize(scenario: Scenario, directives: list[dict]) -> tuple[list[dict], list[int]]:
    """Return (hourly_plan, indices of directives that had to be dropped to stay feasible).

    Organizer scenarios are guaranteed feasible under ground-truth directives, so
    dropping only happens if the LLM mis-read a note; we then keep as many
    directives as possible rather than failing the request.
    """
    idx = list(range(len(directives)))
    for keep in range(len(idx), -1, -1):
        for subset in combinations(idx, keep):
            active = [directives[i] for i in subset]
            lim = build_limits(scenario, active)
            x = _solve_lp(scenario, lim)
            if x is not None:
                dropped = [i for i in idx if i not in subset]
                return _to_plan(scenario, lim, x), dropped
    # Base problem is always feasible (idle battery, grid covers the rest), but be safe.
    lim = build_limits(scenario, [])
    return _to_plan(scenario, lim, None), idx


def totals(plan: list[dict], scenario: Scenario) -> dict:
    total_grid = sum(p["grid_kwh"] for p in plan)
    cost = sum(p["grid_kwh"] * scenario.hours[p["hour"]].tariff_bdt_per_kwh for p in plan)
    return {
        "total_grid_kwh": round(total_grid, 4),
        "total_cost_bdt": round(cost, 4),
        "peak_grid_kwh": round(max(p["grid_kwh"] for p in plan), 4),
    }
