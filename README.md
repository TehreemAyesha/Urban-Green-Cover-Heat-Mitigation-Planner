# Urban Green Cover Heat Mitigation Planner

Identifying where added urban green cover would most reduce heat, starting with
Phoenix, AZ. Combines satellite-derived vegetation/land-surface-temperature data
(Google Earth Engine) with FortyGuard's urban temperature API.

## Project layout

```
urban-green-heat-planner/
├── data/
│   ├── raw/            # unmodified source datasets
│   └── processed/      # cleaned / derived datasets
├── scripts/            # data pulling + processing
│   └── test_fortyguard.py
├── app/                # Streamlit dashboard (later)
├── requirements.txt
├── .env                # secrets — git-ignored, never commit
├── .env.example        # safe-to-commit template
└── .gitignore
```

## Setup

Create and activate a virtual environment (recommended), then install:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

> **Note on `geopandas` / `rasterio` on Windows:** these carry compiled GDAL
> dependencies. Modern pip wheels usually install cleanly. If they fail, install
> those two via conda instead:
> `conda install -c conda-forge geopandas rasterio`

## Configuration

Secrets live in `.env`, which is git-ignored. No keys are hardcoded in any script.

1. Open `.env`
2. Paste your FortyGuard key after `FORTYGUARD_API_KEY=`

The endpoint URL and `api-key` header format are confirmed and live in the script
as constants, so only the secret belongs in `.env`.

## Verify FortyGuard access

```bash
python scripts/test_fortyguard.py
```

Runs the complete two-step pipeline for a ~1km x 1km box around downtown
Phoenix (33.4484, -112.0740), authenticating with an `api-key` header:

1. **Submit** — `POST /v1/heatmap` returns an `activity_id`
2. **Poll** — `GET /v1/status/{activity_id}` every 5s (up to 10 min) until the
   status reads `completed`/`succeeded`, tolerating the 404 that occurs briefly
   right after submission

On completion it prints the `stats_data` section (min/max/mean temperature) and a
structural summary of the rest — the GeoJSON features are counted, not dumped.
The API key is redacted from all output.

Verified end to end: job completed in ~27s (5 polls) returning mean 42.90 across
79 features.

## Status

- [x] Step 1 — project structure, requirements, `.env` / `.gitignore`
- [x] Step 1 — dependencies installed (Python 3.13.5, 107 packages)
- [x] Step 2 — FortyGuard pipeline verified end to end (submit → poll → stats)
- [ ] Next — pull Earth Engine green-cover/NDVI data and build the dashboard

## Note on the shell

Git Bash on this machine cannot fork (`0xC0000142`), so `pip` and `python`
cannot be run through it. `scripts/setup_and_test.ps1` (install + test) and
`scripts/run_test.ps1` (test only) launch PowerShell directly and mirror all
output to `run_log.txt` as a workaround. Run either directly if needed:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_test.ps1
```

