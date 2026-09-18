"""Run public sample cases against a running service and validate every response.

Usage:
  python scripts/run_samples.py --url http://localhost:8000 [--file samples/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json]

Accepts a JSON file that is either a list of cases or {"cases": [...]}. Each case may be the
raw request itself, or an object with the request under "request"/"input" and optional expected
output under "expected"/"expected_response"/"output".
Checks: HTTP 200, schema, interpretation guardrails, full plan replay (against our own
interpretation AND against the expected interpretation if provided), expected cost if provided.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schemas import parse_scenario  # noqa: E402
from app.validator import validate_interpretation, validate_plan  # noqa: E402

TOP_KEYS = {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh",
            "total_cost_bdt", "peak_grid_kwh", "plan_summary"}


def load_cases(path):
    data = json.load(open(path))
    if isinstance(data, dict):
        for k in ("cases", "samples", "public_cases", "scenarios", "test_cases"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            data = [data]
    out = []
    for c in data:
        req = next((c[k] for k in ("request", "input", "scenario", "request_body") if isinstance(c.get(k), dict)), c)
        exp = next((c[k] for k in ("expected", "expected_response", "expected_output", "output", "response")
                    if isinstance(c.get(k), dict)), None)
        out.append((c.get("id") or c.get("name") or req.get("scenario_id"), req, exp))
    return out


def post(url, body, timeout=35):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), time.monotonic() - t
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200], time.monotonic() - t


def compare_interp(ours, expected):
    diffs = []
    for o, e in zip(ours, expected):
        if o["directive_type"] != e.get("directive_type"):
            diffs.append(f"note {o['note_index']}: type {o['directive_type']} vs expected {e.get('directive_type')}")
            continue
        oa, ea = o["structured_adjustment"], e.get("structured_adjustment")
        if oa is None or ea is None:
            continue
        for k, v in ea.items():
            ov = oa.get(k)
            if k == "hours" and ov != v:
                diffs.append(f"note {o['note_index']}: hours {ov} vs expected {v}")
            elif k != "hours" and (ov is None or abs(ov - v) > 0.01):
                diffs.append(f"note {o['note_index']}: {k} {ov} vs expected {v}")
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--file", default=os.path.join(os.path.dirname(__file__), "..", "samples", "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))
    args = ap.parse_args()
    base = args.url.rstrip("/")

    with urllib.request.urlopen(base + "/health", timeout=15) as r:
        print("health:", r.status, r.read().decode())

    cases = load_cases(args.file)
    passed, latencies = 0, []
    for name, req, exp in cases:
        status, resp, dt = post(base + "/optimize-energy", req)
        latencies.append(dt)
        problems = []
        if status != 200:
            problems.append(f"HTTP {status}: {resp}")
        else:
            if set(resp) != TOP_KEYS:
                problems.append(f"top-level keys differ: {set(resp) ^ TOP_KEYS}")
            if resp.get("scenario_id") != req.get("scenario_id"):
                problems.append("scenario_id not echoed")
            interp = resp["directive_interpretation"]
            problems += validate_interpretation(interp, len(req["operator_notes"]))
            sc = parse_scenario(req)
            truth = interp
            if exp and isinstance(exp.get("directive_interpretation"), list):
                problems += compare_interp(interp, exp["directive_interpretation"])
                truth = exp["directive_interpretation"]
            problems += [f"plan: {v}" for v in validate_plan(sc, [e for e in truth if e.get("applies")], resp)]
            if exp and isinstance(exp.get("total_cost_bdt"), (int, float)):
                gap = resp["total_cost_bdt"] - exp["total_cost_bdt"]
                if gap > 0.01:
                    problems.append(f"cost {resp['total_cost_bdt']} > expected {exp['total_cost_bdt']} (+{gap:.2f})")
                elif gap < -0.01:
                    print(f"  note: {name} cost below expected by {-gap:.2f} (check directive application)")
        status_txt = "PASS" if not problems else "FAIL"
        print(f"[{status_txt}] {name}  ({dt:.2f}s)")
        for p in problems:
            print("     -", p)
        passed += not problems

    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else 0
    print(f"\n{passed}/{len(cases)} passed   p95 latency {p95:.2f}s")
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
