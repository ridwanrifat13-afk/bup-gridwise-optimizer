# GridWise LLM Energy Optimizer (BUP CSE Fest 2026 Hackathon, Preliminary)

This HTTP API reads natural-language **operator notes** with an LLM (Google Gemini) and turns them into structured directives. It validates those directives with deterministic guardrails, then solves a **linear program** to produce the cheapest valid 24-hour grid / solar / battery schedule for the campus.

```
 request ──► LLM interpreter ──► guardrail validator ──► LP optimizer ──► final replay validator ──► JSON response
            (Gemini, JSON schema)  (types, hours, ranges,   (scipy HiGHS,     (re-checks every GridWise
                                     applies, shapes)         exact optimum)    + directive rule)
```

| Endpoint | Description |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `POST /optimize-energy` | Scenario in, `directive_interpretation` + 24-hour `hourly_plan` + totals out |
| `GET /` (Vercel) | Web demo dashboard (`public/index.html`) |

**Live deployment:** https://grid-wise-cuet.vercel.app. The API base URL is the same, e.g. `https://grid-wise-cuet.vercel.app/health`.

**Public sample pack:** all 10 organizer sample cases pass against the live deployment (`scripts/run_samples.py`, p95 latency ≈ 1.5 s).

---

## 1. Quickstart (local, from a clean machine)

Requirements: Python 3.12+ and a Gemini API key (https://aistudio.google.com/apikey).

```bash
git clone https://github.com/ridwanrifat13-afk/bup-gridwise-optimizer.git
cd bup-gridwise-optimizer
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then put your key in .env: GEMINI_API_KEY=...
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @samples/example_request.json
```

Run the organizer public samples. The script validates the schema, interpretation, full plan replay and cost:

```bash
python scripts/run_samples.py --url http://localhost:8000 --file samples/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
```

Expected result: every case prints `[PASS]`, followed by a summary line `N/N passed   p95 latency …`.

Unit tests (no API key needed; the LLM is disabled inside tests):

```bash
pip install -r requirements-dev.txt
pytest -q
```

LLM paraphrase-robustness check (54 notes: 36 hand-written paraphrases/distractors + the 18 public-sample notes, needs the key):

```bash
python scripts/eval_paraphrases.py
```

## 2. Docker fallback image

**Image:** `docker.io/ridwanrifat13afk/gridwise-optimizer:v1.0.0` (public, linux/amd64)
**Digest:** `sha256:560e030966cb5ef7e3a30270a5e370f400aef30c4b986dabb2fa0688ad9abe1c`

- Port **8000**, bound to `0.0.0.0` (override with `-e PORT=...`).
- Required env var: `GEMINI_API_KEY`. Optional: the other variables in section 3.
- Runs as a non-root user with a built-in health check. No secrets are baked into the image.

```bash
docker pull ridwanrifat13afk/gridwise-optimizer:v1.0.0
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=<your-key> ridwanrifat13afk/gridwise-optimizer:v1.0.0
curl http://localhost:8000/health
# {"status":"ok"}
curl -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d @samples/example_request.json
```

Verified: a fresh pull reports `/health` ready in about 3 s and returns the expected SAMPLE-01 cost.

Rebuild and publish (from the repo root):

```bash
docker buildx build --platform linux/amd64 -t ridwanrifat13afk/gridwise-optimizer:v1.0.0 --push .
```

## 3. Configuration (environment variables)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | **yes** | — | Google Gemini API key |
| `GEMINI_MODEL` | no | `gemini-3.5-flash` | Primary interpreter model |
| `GEMINI_FALLBACK_MODEL` | no | `gemini-flash-lite-latest` | Used if the primary errors out / is rate-limited |
| `GEMINI_THINKING_BUDGET` | no | `0` | Thinking tokens (0 = fastest, -1 = model default) |
| `LLM_TIMEOUT_SECONDS` | no | `10` | Per LLM call timeout |
| `LLM_TOTAL_BUDGET_SECONDS` | no | `22` | Total LLM time budget per request (judge limit is 30 s) |
| `LOG_LEVEL` | no | `INFO` | Logging level |

Values are whitespace-trimmed, so a key or model name pasted into a dashboard with a trailing newline still works.

> **Quota note:** judges send many requests in a short window. On the Gemini free tier the primary model can return `429 Too Many Requests`, which pushes requests onto the fallback model or the rule-based safety net. Use a key with billing enabled for judging.

## 4. How it works

### 4.1 LLM role (the mandatory interpretation step)
- **Model / provider:** Google Gemini `gemini-3.5-flash` through the `google-genai` SDK, with `gemini-flash-lite-latest` as the automatic fallback model. Both are configurable (section 3).
- **One call per request** interprets all 1 to 3 notes together. It uses temperature 0 and a strict `response_schema`, so the output is JSON.
- The LLM decides for each note whether it `applies`, which `directive_type` it is (one of the 6 supported types), the time windows, and the numeric values. The prompt (`app/prompt.py`) encodes the spec conventions:
  - Start hour included, end hour excluded.
  - `factor` is the fraction of solar that *remains*.
  - Percent-of-capacity reserves.
  - kW is treated as kWh per hour.
  - Windows can wrap past midnight.
  - Notes about other days or with no enforceable rule are `no_op`.

  The prompt also includes 13 few-shot examples covering paraphrases and distractors.
- The LLM returns hour **ranges** (`{"start":13,"end":15}`), and deterministic code expands them into hour lists (`[13,14]`). This removes a class of arithmetic mistakes without taking the language understanding away from the model.

### 4.2 Guardrails (`app/guardrails.py`)
LLM output is treated as untrusted data:
- Each note gets exactly one entry, with `note_index` running 0..N-1 in order. Duplicates keep the first entry, and missing notes are flagged.
- `directive_type` must be one of the 6 supported values. Anything else is rejected.
- `no_op` always has `applies=false` and `structured_adjustment=null`. Every other type has `applies=true`.
- Hours are unique integers from 0 to 23, sorted ascending. A directive with no hours is rejected.
- `factor` must be in [0,1]. Reserves must be finite, ≥0 and ≤ capacity. `max_grid_kwh` must be finite and ≥0.
- `structured_adjustment` is rebuilt to the **exact** spec shape, and any extra keys are dropped.

**What happens when output fails:**
1. If the output fails validation, the model is called **once more** with the validation errors as feedback.
2. If the provider fails (timeout, 429, 5xx), the fallback model is tried.
3. If there is still no valid LLM answer for a note, a documented rule-based **safety net** (`app/fallback_parser.py`) is used for that note only.
4. If that also fails, the note is treated as `no_op`.

The service never crashes and never invents an unsupported directive. The primary interpreter is always the LLM. The safety net exists only for provider outages.

### 4.3 Optimizer (`app/optimizer.py`)
A linear program solved with **SciPy `linprog` (HiGHS)**. It has 120 variables and solves in about 1 ms.

**Variables per hour h:** grid `g`, solar used `s`, charge `c`, discharge `d`, energy after the hour `e`.

```
minimise   Σ tariff[h] · g[h]
subject to g + s + d = demand + c                       (energy balance)
           0 ≤ s ≤ solar[h] · factor[h]                 (effective solar after solar_reduction)
           0 ≤ c ≤ max_charge   (0 in no_charge_window hours)
           0 ≤ d ≤ max_discharge (0 in no_discharge_window hours)
           e[h] = e[h-1] + c − d,  e[-1] = initial
           max(base_min, reserve directive) ≤ e[h] ≤ capacity
           e[23] = initial                              (end-of-day neutrality)
           0 ≤ g ≤ max_grid_kwh (in max_grid_window hours)
```

- **Second stage:** a second LP keeps the optimal cost and minimises total battery throughput. This means the plan never charges and discharges in the same hour and doesn't cycle without reason.
- **Building the plan:** the solution becomes one `battery_action` per hour. Battery energy is **replayed** from the actions, and `total_grid_kwh`, `total_cost_bdt` and `peak_grid_kwh` are recomputed from the final `hourly_plan`.
- **Overlapping directives:** the strictest wins (min factor, max reserve, min grid cap).
- **Infeasible directives:** organizer cases are feasible, but if a mis-read directive makes the model infeasible, the optimizer drops the smallest number of directives needed to find a valid plan. The response is still a valid 200.

### 4.4 Final validator (`app/validator.py`)
Every response is replayed hour by hour against all GridWise and directive rules, with a 0.01 tolerance, the same way the judge does it. Violations are logged. The same validator drives the tests and `scripts/run_samples.py`.

### 4.5 Accepted request shapes
`POST /optimize-energy` accepts the scenario object from the Problem Statement. For convenience it also accepts a public sample-pack case, `{"id": ..., "label": ..., "input": {...scenario...}}`; when the body has no top-level `scenario_id`, the object under `input` (or `request`) is used. The response is identical either way.

### 4.6 HTTP behaviour

| Case | Code |
|---|---|
| OK | 200 |
| Malformed JSON, wrong structure (not 24 hours, 0 or >3 notes, empty notes, missing/non-numeric fields, duplicate hours) | 400 |
| Well-formed but semantically invalid (negative demand/solar/battery values, initial energy outside [min, capacity]) | 422 |
| Unexpected error | 500 `{"error":"internal error"}` (no stack traces or secrets) |

## 5. Web demo dashboard
`public/index.html` is a single-file demo UI served at `/` on Vercel. It calls the same `POST /optimize-energy` endpoint the judges use; it is not part of scoring.

- **Paste JSON input:** paste a bare request, a sample case (`{"id","label","input"}`) or a whole sample pack (`{"cases":[...]}` or an array). A pack shows a case picker. Missing closing brackets from a partial copy are closed automatically. **Load JSON** fills the form; **Load & run** optimises immediately.
- **Form builder:** scenario ID, up to three operator notes with suggestion chips, battery parameters and preset 24-hour profiles.
- **Results:** total grid / cost / peak / latency tiles, one card per note showing how it was interpreted (applied vs ignored, directive type, structured adjustment), the plan summary, an hourly chart (demand, solar used, charge/discharge) and the full hourly table.
- **Branding:** GridWise logo and icons in `public/assets/`, colour palette `#000000 · #1F150C · #412D15 · #E1DCC9`.

## 6. Deployment
- **Live:** Vercel serverless Python (`api/index.py` + `vercel.json`). `/` serves the dashboard, `/assets/*` serves static files, and every other path is rewritten to FastAPI. `GEMINI_API_KEY`, `GEMINI_MODEL` and `GEMINI_FALLBACK_MODEL` are set in the Vercel project environment variables.
- **Deploy:** `vercel --prod` from the repo root (the project is linked in `.vercel/`, which is git-ignored).
- **Fallback:** the Docker image above (`Dockerfile`, python:3.12-slim, uvicorn with 2 workers, non-root user).

## 7. Project layout

```
app/main.py            FastAPI app & pipeline
app/schemas.py         request parsing & 400/422 validation
app/prompt.py          system prompt, few-shots, Gemini response schema
app/llm.py             Gemini call, retry-with-feedback, model fallback, cache
app/guardrails.py      deterministic validation / normalisation of LLM output
app/fallback_parser.py rule-based safety net (provider outage only)
app/constraints.py     directives -> per-hour limits
app/optimizer.py       LP optimizer & plan construction
app/validator.py       judge-style replay validator
api/index.py           Vercel entrypoint
public/index.html      web demo dashboard (served at / on Vercel)
public/assets/         logo, favicon and app icon
scripts/run_samples.py public sample runner / validator
scripts/eval_paraphrases.py  LLM paraphrase accuracy check
tests/                 pytest suite + paraphrase set
```

## 8. Dependencies & credits
- FastAPI, Uvicorn, Pydantic (web)
- NumPy and SciPy with HiGHS (optimization)
- `google-genai` (Gemini API)
- python-dotenv
- pytest and httpx (tests)
- AI coding assistant (Claude Code) was used to help write code and docs. The architecture and design decisions are the team's.

## 9. Known limitations
- Interpretation quality depends on the Gemini API being available. During an outage, the rule-based safety net handles common phrasings, but it is less robust to unusual paraphrases.
- Each note is mapped to exactly one directive, as the spec requires. A note that describes two constraints keeps the dominant one.
- Grid export isn't modelled, since it isn't part of the challenge. Unused solar is curtailed.
- The in-memory interpretation cache is per process or instance.
- The dashboard's "API base URL" field only works for the same origin; the API does not enable CORS, so the browser blocks calls to other hosts.

## 10. Secret handling
- `GEMINI_API_KEY` is read only from the environment (`.env` locally, the Vercel env settings, or `docker run -e`).
- `.env` is git-ignored and docker-ignored, and no key is committed or baked into the image.
- Logs contain scenario IDs, directive types, timings and error *class names* only. They never include keys, prompts or stack traces, and error responses are generic.
