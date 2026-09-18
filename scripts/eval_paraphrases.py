"""Measure interpretation accuracy on tests/paraphrase_cases.json.

Usage:
  python scripts/eval_paraphrases.py              # LLM (needs GEMINI_API_KEY)
  python scripts/eval_paraphrases.py --fallback   # rule-based safety net only
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.fallback_parser import parse_note  # noqa: E402
from app.guardrails import GuardrailError, no_op_entry, normalize_item  # noqa: E402
from app.llm import interpret_notes  # noqa: E402

VALUE_KEYS = ("factor", "minimum_energy_kwh", "max_grid_kwh")


def check(entry: dict, exp: dict) -> str | None:
    if entry["directive_type"] != exp["type"]:
        return f"type {entry['directive_type']} != {exp['type']}"
    if exp["type"] == "no_op":
        return None
    adj = entry["structured_adjustment"]
    if adj["hours"] != exp["hours"]:
        return f"hours {adj['hours']} != {exp['hours']}"
    for k in VALUE_KEYS:
        if k in exp and abs(adj[k] - exp[k]) > 0.01:
            return f"{k} {adj[k]} != {exp[k]}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--batch", type=int, default=3, help="notes per request (1-3)")
    args = ap.parse_args()

    path = os.path.join(os.path.dirname(__file__), "..", "tests", "paraphrase_cases.json")
    data = json.load(open(path))
    cases = data["cases"]
    # batch notes that share a battery capacity (needed for % reserves)
    chunks, cur = [], []
    for c in cases:
        c.setdefault("capacity_kwh", data["capacity_kwh"])
        if cur and (len(cur) == args.batch or cur[0]["capacity_kwh"] != c["capacity_kwh"]):
            chunks.append(cur)
            cur = []
        cur.append(c)
    chunks.append(cur)
    ok, latencies = 0, []
    for chunk in chunks:
        cap = chunk[0]["capacity_kwh"]
        notes = [c["note"] for c in chunk]
        t = time.monotonic()
        if args.fallback:
            entries = []
            for i, n in enumerate(notes):
                try:
                    entries.append(normalize_item(parse_note(n), i, cap))
                except GuardrailError:
                    entries.append(no_op_entry(i, ""))
            src = "fallback"
        else:
            entries, meta = interpret_notes(notes, cap)
            src = meta["source"]
        latencies.append(time.monotonic() - t)
        for c, e in zip(chunk, entries):
            err = check(e, c)
            if err:
                print(f"FAIL [{src}] {c['note']!r}: {err}")
            else:
                ok += 1
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
    print(f"\n{ok}/{len(cases)} correct ({100 * ok / len(cases):.1f}%)  p95 latency/request {p95:.2f}s")


if __name__ == "__main__":
    main()
