# Team Atreides — Codefest Datathon 2026, Round 1 (Urban Flow Analytics)

## Contents
| Path | What it is |
|---|---|
| `Atreides_FinalNotebook.ipynb` | Final notebook (executed): EDA, data-quality audit, fare & duration models, ablations, demand forecasting, clustering, architecture |
| `src/ingest.py` | Streams the 12 monthly CSVs out of the raw zip into DuckDB; writes full-population quality counts, aggregates and a 5 % sample |
| `src/features.py` | Cleaning rules, pre-trip feature engineering, corridor statistics, deployable `TripModel` wrapper |
| `src/demand.py` | Direct multi-horizon (1–72 h) design matrix for zone demand forecasting |
| `src/ledger.py` | Full-population money ledger (every raw row classified valid / reversal / zero fare / invalid) |
| `src/warehouse.py` | Builds `data/warehouse.duckdb`, the analytics store behind the dashboard and the assistant |
| `src/dashboard.py` | **Track 6** — builds the management dashboard |
| `assistant/` | **Track 5** — AI Mobility Assistant (Claude agent + offline engine, SQL safety guard, terminal and web chat) |
| `models/fare_model.pkl` | Upfront fare model (`TripModel`: booking request → `base_fare`) |
| `models/duration_model.pkl` | Trip-duration model (`TripModel`: booking request → minutes) |
| `models/demand_model.pkl` | Global LightGBM demand forecaster for the 10 busiest zones |
| `data/splits/trips_{train,val,test}.parquet` | Cleaned trip splits (train Apr–Dec 2025 · val Jan 2026 · test Feb–Mar 2026) |
| `data/splits/demand_{train,val,test}.parquet` | Demand-forecast design matrices for the same periods |
| `data/quality`, `data/agg`, `data/sample` | Outputs of `src/ingest.py` / `src/ledger.py` |
| `reports/` | Technical report, figures, `results.json` (all metrics), zone archetypes, `dashboard/` |

Authoring tooling that is **not** part of the solution — report generator, notebook source and the architecture diagram source `architecture.drawio` — lives in `../tools/`, outside this folder.

## Reproduce
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/ingest.py          # ~2 min; expects the organisers' zip next to this folder
.venv/bin/python src/ledger.py          # ~2 min
.venv/bin/python -m ipykernel install --prefix .venv --name atreides
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 --ExecutePreprocessor.kernel_name=atreides Atreides_FinalNotebook.ipynb
.venv/bin/python src/warehouse.py       # ~1 min, needs the notebook outputs
.venv/bin/python src/dashboard.py
```
`src/ingest.py` looks for `../Datathon 2026 - Round 1 - Materials/Urban_Flow_Analytics_Dataset_csv.zip`
(edit `RAW_ZIP` at the top of the file if it lives elsewhere). All randomness is seeded (42).
The submission ZIP omits `data/warehouse.duckdb` (183 MB) — rebuild it with `src/warehouse.py`.

## Using a saved model
```python
import sys, joblib, pandas as pd
sys.path.insert(0, "src")                      # TripModel lives in src/features.py
fare = joblib.load("models/fare_model.pkl")
eta = joblib.load("models/duration_model.pkl")
req = pd.DataFrame({"pickup_timestamp": ["2026-03-20 08:15"], "origin_loc_id": [237], "dest_loc_id": [161],
                    "rider_count": [1], "provider_code": [2], "rate_class_id": [1], "is_flex": [0]})
fare.predict(req), eta.predict(req)
```

## Track 6 — Business dashboard
Open `reports/dashboard/Atreides_Business_Dashboard.html` in any browser (single file, works offline).
Story: *Flex Fare — is upfront pricing paying off?* Problem → evidence → causes → recommendations, with filters
(borough, months, weekday/weekend), a tip-step scenario slider, a data table under every chart and shareable
links (e.g. `...html#borough=Queens&from=2025-12`).

## Track 5 — AI Mobility Assistant
```bash
export ANTHROPIC_API_KEY=...                      # optional: without it the offline engine answers
.venv/bin/python assistant/web.py                 # chat UI at http://localhost:8000
.venv/bin/python assistant/cli.py --sql           # terminal chat (shows the SQL behind each answer)
.venv/bin/python assistant/cli.py --ask "Which 5 zones had the most pickups in March 2026?"
```
* **Claude engine** (`claude-opus-5`, tool use): plans a query, resolves place names with `find_zones`, runs SQL through
  the guard, retries on errors, asks one clarifying question when a question is materially ambiguous, and keeps the
  conversation for follow-ups. Server-side refusal fallbacks are enabled.
* **Offline engine**: transparent rule-based parser (metric, grouping, zones, period, hours, day type, payment) for
  the common question families — used automatically when no API key is set or the API fails.
* **Multi-user**: each visitor gets an isolated conversation (session cookie), 20 questions/minute per session;
  the server binds to 127.0.0.1 and expects a reverse proxy when hosted.
* **Safety** (`assistant/guard.py`): read-only database with external access disabled and configuration locked;
  one statement only, parsed by DuckDB and required to be SELECT; deny-list for file/extension/settings functions;
  200-row cap and 15-second timeout. Answers are rendered as plain text in the UI.
