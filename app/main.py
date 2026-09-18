"""GridWise API: LLM interpreter -> guardrails -> LP optimizer -> replay validator."""
import json
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config
from app.llm import interpret_notes
from app.optimizer import optimize, totals
from app.schemas import RequestError, Scenario, parse_scenario
from app.validator import validate_plan

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gridwise")

app = FastAPI(title="GridWise LLM Energy Optimizer", version="1.0.0")


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError):
    return JSONResponse({"error": "invalid request"}, status_code=400)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    log.error("unhandled error: %s", type(exc).__name__)
    return JSONResponse({"error": "internal error"}, status_code=500)


@app.get("/")
def root():
    return {"service": "gridwise-optimizer", "endpoints": ["GET /health", "POST /optimize-energy"]}


@app.get("/health")
def health():
    return {"status": "ok"}


def _summary(scenario: Scenario, interp: list[dict], plan: list[dict], tot: dict, dropped: list[dict]) -> str:
    applied = [e["directive_type"] for e in interp if e["applies"]]
    charge = [p["hour"] for p in plan if p["battery_action"] == "charge"]
    discharge = [p["hour"] for p in plan if p["battery_action"] == "discharge"]
    parts = [
        f"Applied {len(applied)} operator directive(s)" + (f" ({', '.join(applied)})" if applied else ""),
        f"{len(interp) - len(applied)} note(s) ignored as no_op.",
        f"Battery charges in hours {charge or 'none'} and discharges in hours {discharge or 'none'}, "
        f"ending at its initial {scenario.battery.initial_energy_kwh} kWh.",
        f"Total grid import {tot['total_grid_kwh']} kWh costing {tot['total_cost_bdt']} BDT (peak {tot['peak_grid_kwh']} kWh).",
    ]
    if dropped:
        parts.append(f"{len(dropped)} directive(s) were infeasible together and could not all be enforced.")
    return " ".join(parts)


@app.post("/optimize-energy")
async def optimize_energy(request: Request):
    started = time.monotonic()
    raw = await request.body()
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "malformed JSON"}, status_code=400)
    try:
        scenario = parse_scenario(body)
    except RequestError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)

    interp, meta = await run_in_threadpool(interpret_notes, scenario.operator_notes, scenario.battery.capacity_kwh)
    directives = [e for e in interp if e["applies"]]
    plan, dropped_idx = await run_in_threadpool(optimize, scenario, directives)
    dropped = [directives[i] for i in dropped_idx]
    tot = totals(plan, scenario)

    response = {
        "scenario_id": scenario.scenario_id,
        "directive_interpretation": interp,
        "hourly_plan": plan,
        **tot,
        "plan_summary": _summary(scenario, interp, plan, tot, dropped),
    }

    enforced = [d for i, d in enumerate(directives) if i not in dropped_idx]
    violations = validate_plan(scenario, enforced, response)
    log.info(
        "scenario=%s notes=%d interp=%s types=%s dropped=%d violations=%d cost=%s ms=%d",
        scenario.scenario_id, len(interp), meta["source"], [e["directive_type"] for e in interp],
        len(dropped), len(violations), tot["total_cost_bdt"], int((time.monotonic() - started) * 1000),
    )
    if violations:
        log.warning("self-check violations for %s: %s", scenario.scenario_id, violations[:5])
    return response
