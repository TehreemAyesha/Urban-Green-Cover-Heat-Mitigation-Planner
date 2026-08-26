# CLAUDE.md

Context for future Claude Code sessions. **Read this first — it should remove any
need for prior conversation history.**

Last updated: **2026-08-27** (end of Day 1 session).

---

## 1. The project

**Urban Green Cover Heat Mitigation Planner** — FortyGuard Hackathon '26.

Goal: identify where added urban green cover (trees, parks, shade) would most
reduce heat. Combines FortyGuard's urban temperature data with satellite-derived
vegetation indices, surfaced through a map dashboard.

**Target city: Phoenix, AZ.** Reference point used throughout: `33.4484, -112.0740`
(downtown).

The user does not write code — Claude handles all setup, coding, and file
creation. Deliver working files, not instructions for the user to follow.

---

## 2. Tech stack

Pure Python (no JS/TS build step). Installed and verified working:

| Package | Version | Role |
|---|---|---|
| `geemap` | 0.38.3 | Earth Engine mapping helpers |
| `earthengine-api` | 1.7.41 | GEE access (NDVI, land surface temp) |
| `geopandas` | 1.1.4 | vector geospatial data |
| `rasterio` | 1.5.1 | raster I/O |
| `streamlit` | 1.62.0 | dashboard |
| `pydeck` | 0.9.3 | map layers |
| `requests` | 2.34.2 | HTTP |
| `pandas` | 3.0.5 | dataframes |
| `python-dotenv` | 1.2.3 | secrets from `.env` |

Interpreter: **Python 3.13.5** at
`C:\Users\PMLS\AppData\Local\Programs\Python\Python313\python.exe`.
pip 26.2.1. 107 packages total, all installed as prebuilt wheels — **no GDAL
compilation was needed**, so don't pre-emptively route geopandas/rasterio
through conda.

Install is already done. To reinstall: `pip install -r requirements.txt`
(but see the shell quirk in section 6 — bash cannot run pip on this machine).

---

## 3. Layout

```
urban-green-heat-planner/
├── CLAUDE.md               # this file
├── README.md               # human-facing setup + status
├── requirements.txt
├── .env                    # SECRETS - git-ignored, never commit
├── .env.example            # safe-to-commit template
├── .gitignore
├── .gitattributes          # line-ending normalisation
├── .claude/launch.json     # preview-server configs (shell workaround)
├── data/
│   ├── raw/                # unmodified source datasets
│   └── processed/          # cleaned / derived datasets
├── scripts/
│   ├── test_fortyguard.py  # full submit->poll pipeline test (WORKING)
│   ├── setup_and_test.ps1  # install deps + run test
│   ├── run_test.ps1        # run test only
│   ├── git_setup.ps1       # init + ignore verification + commit
│   ├── git_push.ps1        # push to origin
│   └── git_sync.ps1        # stage + secret gate + commit + push
└── app/                    # Streamlit dashboard (empty, not started)
```

## Git / GitHub

Initialised and pushed. Default branch **`main`**.

```
origin  https://github.com/TehreemAyesha/Urban-Green-Cover-Heat-Mitigation-Planner.git
```

Auth is **HTTPS via Git Credential Manager, already cached** — pushes succeed
without prompting. Do not switch to SSH; there is no keypair on this machine.

To commit and push further work, use the `git-sync` preview config (it stages,
runs a secret gate, commits, and pushes). Git itself works fine when launched
via PowerShell — only bash is broken.

**Secret gate:** `git_setup.ps1` / `git_sync.ps1` refuse to commit if `.env`, any
`*.log` / `*_log.txt`, or the literal API key value appears in staged content.
The key is read from `.env` at runtime, so the scripts never contain it. Keep
this gate in any future git helper.

---

## 4. Google Earth Engine

**Project ID: `greenhouse-66798`**

Use as `ee.Initialize(project="greenhouse-66798")`.

**Not yet authenticated or tested.** No `ee.Authenticate()` has been run in this
project and no GEE call has been made. Expect to handle auth before the first
NDVI pull. Do not assume it works.

---

## 5. FortyGuard API — confirmed working

Auth: **`api-key: <key>` header** on every request. Key lives in `.env` as
`FORTYGUARD_API_KEY` — load via `python-dotenv`, never hardcode.

Two-step **asynchronous** pattern:

```
1. SUBMIT   POST https://api.fortyguard.com/v1/heatmap
            -> 200 {"message": "Heatmap Submitted Successfully",
                    "data": {"activity_id": "<uuid>"}}

2. POLL     GET  https://api.fortyguard.com/v1/status/{activity_id}
            -> data.status: "Processing" ... then "Completed"
```

Submit body shape (confirmed):

```json
{
  "polygon_aoi": { "type": "FeatureCollection", "features": [
      { "type": "Feature", "properties": {},
        "geometry": { "type": "Polygon", "coordinates": [[ [lon,lat], ... ]] } } ] },
  "date_time": { "start_date": "YYYY-MM-DD", "start_time": "14:00", "filter_type": 1 },
  "granularity": 100
}
```

Polling rules that matter:
- Poll every 5s, cap ~120 attempts (10 min).
- Compare `data.status` **case-insensitively** — the API returns `"Processing"` /
  `"Completed"`, not lowercase.
- Success: `completed` / `succeeded`. Failure: `failed` / `error`.
- **A 404 is expected for the first few seconds** after submission (activity not
  yet registered). Grace-window it; don't treat it as fatal.
- The API can report **logical failure inside an HTTP 200** via `"error": true`.
  Check the body, not just the status code.
- Rate limit is exposed in `x-ratelimit-limit` / `x-ratelimit-remaining`
  (100/window observed). Each test run costs 2 requests.

### Verified result (2026-08-27)

1 km box around downtown Phoenix, `2026-08-26 14:00`, granularity 100 →
**completed in ~27s (5 polls)**, 31s wall clock end to end.

```
temperature_stats: minimum 42.89, maximum 42.92, mean 42.90, standard_deviation 0.01
GeoJSON features: 79
```

Units are **not labelled** by the API, but 42.9 °C ≈ 109 °F, which matches a real
Phoenix August afternoon — treat values as **Celsius**.

`stats_data` nesting is undocumented; `test_fortyguard.py` locates it by key name
via a recursive search rather than a fixed path. Keep that approach.

### IMPORTANT finding — small AOIs are useless for this project

The 1 km box showed **almost no temperature variation: 0.03 °C spread, std dev
0.01**, with all 79 features landing in a single histogram bucket. That is fine
for an API smoke test but useless for ranking neighbourhoods, because there is no
hot/cool contrast to rank.

**The real data pull needs a city-scale polygon** spanning mixed land cover
(parks vs. parking lots vs. dense built-up) to capture meaningful contrast. Do not
build the analysis on top of a small box. Revisit `granularity` at the same time —
both AOI size and granularity are one-line changes in the payload.

---

## 6. Environment quirk — Git Bash is broken

**The `Bash` tool cannot run anything on this machine.** Every invocation dies
during shell startup, before the command runs:

```
dofork: child -1 - forked process N died unexpectedly, exit code 0xC0000142
/etc/profile: fork: Resource temporarily unavailable
```

It is a broken MSYS/Cygwin fork emulation (one run also reported a corrupted
mount table: `Directory \drivers\etc does not exist`). Consequences:

- `python`, `pip`, `ls`, even `echo` all fail. It is **not** intermittent enough
  to rely on, and it fails identically for `cmd.exe` and `powershell.exe`
  wrappers, because bash dies before reaching them.
- Do **not** waste turns retrying bash. Do not tell the user to "fix their shell"
  and stop — a workaround already exists.

### Working pattern: PowerShell via the preview-server tool

`mcp__Claude_Browser__preview_start` spawns processes directly, bypassing bash.
Configs already exist in `.claude/launch.json`:

| Config | Does |
|---|---|
| `setup-and-test` | `pip install -r requirements.txt` + run the API test |
| `run-test` | run the API test only |

Usage: `preview_start` with the config name → `preview_logs` to watch output.
The "server" exits when the script finishes (`Server not found` afterwards is
expected, not an error).

**Reuse this pattern for any future script needing shell or install access:**

1. Write a `.ps1` that does the work and mirrors output to `run_log.txt`
   (`Add-Content` per line, so progress is readable while it runs).
2. Add a config to `.claude/launch.json` pointing at it with an unused port.
3. `preview_start` → poll `preview_logs`.
4. Read `run_log.txt` with the `Read`/`Grep` tools for the final output — this
   works even after the process exits and the log survives.

Two gotchas already solved, keep them:
- **Use `python -u`.** Python block-buffers stdout when piped, which hides all
  progress until exit.
- **Keep printed output ASCII.** The Windows console is cp1252 and renders
  em-dashes and similar as `?`. Unicode in comments/docstrings is fine; unicode
  in `print()` is not.

`run_log.txt` and `install_log.txt` are git-ignored.

---

## 7. Current status — Day 1 of 6

Done:
- [x] Project structure, `requirements.txt`, `.env` / `.env.example` / `.gitignore`
- [x] All 9 dependencies installed and verified (Python 3.13.5, 107 packages)
- [x] FortyGuard pipeline **working end to end**: submit → poll → temperature stats
- [x] PowerShell workaround built for the broken bash
- [x] Git initialised, committed, and pushed to GitHub (`main`)

Not started:
- [ ] GEE authentication for `greenhouse-66798` (untested)
- [ ] NDVI / green-cover pull script (`scripts/` — nothing written)
- [ ] City-scale FortyGuard pull (see the small-AOI finding above)
- [ ] Joining temperature + vegetation to rank planting priority
- [ ] Streamlit dashboard (`app/` is empty)

Immediate next step: the GEE/NDVI pull script, plus a city-scale FortyGuard
polygon for Phoenix.

---

## 8. Reference notes

- FortyGuard docs live at `https://docs-api.fortyguard.com/docs/introduction`
  but are **client-side rendered — `WebFetch` returns only the page title**.
  `mint.json`, `openapi.json`, and `llms.txt` all return the same SPA shell.
  Don't burn turns trying to scrape them; ask the user to paste the relevant
  section instead. `api.fortyguard.com` returns 401 at the root (expected).
- Everything in section 5 came from the user reading those docs directly.

---

## Maintaining this file

**Update this file at the end of each work session** so a fresh session needs no
conversation history. Specifically: bump "Last updated", move items between Done
and Not started in section 7, and record any newly confirmed API/GEE behaviour or
environment gotcha. Correct anything that turns out to be wrong rather than
appending a contradiction.
