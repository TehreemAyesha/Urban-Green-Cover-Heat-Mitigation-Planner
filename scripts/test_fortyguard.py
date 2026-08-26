"""
End-to-end test of FortyGuard's Temperature API for a small area of Phoenix, AZ.

Run:
    python scripts/test_fortyguard.py

The API is a two-step, asynchronous pipeline:

    1. SUBMIT  POST /v1/heatmap            -> returns an activity_id
    2. POLL    GET  /v1/status/{id}        -> returns status, then the result

This script runs both steps, then prints the stats_data section (min/max/mean
temperature) rather than the full GeoJSON, which is far too large to read.

The API key is read from .env (never hardcoded) and is redacted from all output.

Optional .env overrides for debugging:
    FORTYGUARD_TEST_DATE=2024-07-15   # heat data often lags; try an older date
    FORTYGUARD_TEST_TIME=14:00

All printed output is ASCII so it renders correctly in the Windows console
(cp1252), which cannot encode characters like em-dashes.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

# --- API ---------------------------------------------------------------------
SUBMIT_URL = "https://api.fortyguard.com/v1/heatmap"
STATUS_URL_TEMPLATE = "https://api.fortyguard.com/v1/status/{activity_id}"
REQUEST_TIMEOUT_SECONDS = 60

# --- Polling behaviour -------------------------------------------------------
POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 120  # 120 x 5s = 10 minutes
# Their docs note an activity can be "temporarily unavailable" right after
# submission, so a 404 inside this window is expected rather than fatal.
NOT_FOUND_GRACE_SECONDS = 45
# Tolerate a short run of network blips / 5xx before giving up.
MAX_CONSECUTIVE_TRANSIENT_ERRORS = 5
# Print a progress line at most this often while the status is unchanged.
PROGRESS_EVERY_N_ATTEMPTS = 6

COMPLETED_STATUSES = {"completed", "succeeded"}
FAILED_STATUSES = {"failed", "error"}

# --- Test area: downtown Phoenix, AZ -----------------------------------------
PHOENIX_LAT = 33.4484
PHOENIX_LON = -112.0740
BOX_SIZE_KM = 1.0  # square box edge length

# --- Request parameters ------------------------------------------------------
DEFAULT_START_TIME = "14:00"  # mid-afternoon: near peak urban heat
FILTER_TYPE = 1
GRANULARITY = 100

MAX_BODY_CHARS = 4000
KM_PER_DEGREE_LAT = 111.32


def _rule(title: str = "") -> None:
    """Print a labelled horizontal rule to keep terminal output readable."""
    if title:
        print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    else:
        print("=" * 70)


# -----------------------------------------------------------------------------
# Geometry
# -----------------------------------------------------------------------------
def build_bbox_polygon(
    center_lat: float, center_lon: float, size_km: float
) -> list[list[float]]:
    """
    Return a closed GeoJSON Polygon ring for a square box around a point.

    Latitude degrees are a constant distance apart; longitude degrees shrink by
    cos(latitude), so the two half-extents are computed separately to keep the
    box roughly square on the ground.
    """
    half_km = size_km / 2.0
    lat_delta = half_km / KM_PER_DEGREE_LAT
    lon_delta = half_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(center_lat)))

    south = center_lat - lat_delta
    north = center_lat + lat_delta
    west = center_lon - lon_delta
    east = center_lon + lon_delta

    # Counter-clockwise from the south-west corner, closing back on itself.
    return [
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
    ]


def build_payload(ring: list[list[float]], start_date: str, start_time: str) -> dict:
    """Assemble the heatmap request body."""
    return {
        "polygon_aoi": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                }
            ],
        },
        "date_time": {
            "start_date": start_date,
            "start_time": start_time,
            "filter_type": FILTER_TYPE,
        },
        "granularity": GRANULARITY,
    }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def load_api_key() -> str:
    """Load the API key from .env, exiting with guidance if it is missing."""
    load_dotenv()
    api_key = (os.getenv("FORTYGUARD_API_KEY") or "").strip()

    if not api_key:
        _rule("CONFIGURATION INCOMPLETE - no request was sent")
        print("  FORTYGUARD_API_KEY is empty in .env.")
        print("  -> Open .env and paste your key after 'FORTYGUARD_API_KEY='")
        print("\n  Nothing was sent to FortyGuard, so this is NOT an API failure.")
        _rule()
        sys.exit(1)

    return api_key


def redact(text: str, secret: str) -> str:
    """Never let the API key reach the terminal."""
    return text.replace(secret, "***REDACTED***") if secret else text


def parse_json(response: requests.Response) -> object:
    """Return the parsed JSON body, or None if it is not JSON."""
    try:
        return response.json()
    except ValueError:
        return None


def find_key(obj: object, target: str, _depth: int = 0) -> object:
    """
    Depth-first search for the first value stored under `target`.

    The exact nesting of stats_data is not documented, so locate it by name
    instead of assuming a fixed path.
    """
    if _depth > 8:
        return None
    if isinstance(obj, dict):
        if target in obj:
            return obj[target]
        for value in obj.values():
            found = find_key(value, target, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj[:50]:
            found = find_key(value, target, _depth + 1)
            if found is not None:
                return found
    return None


def get_status(parsed: object) -> str | None:
    """Pull the job status string out of a status response."""
    if not isinstance(parsed, dict):
        return None
    data = parsed.get("data")
    if isinstance(data, dict) and isinstance(data.get("status"), str):
        return data["status"]
    if isinstance(parsed.get("status"), str):
        return parsed["status"]
    found = find_key(parsed, "status")
    return found if isinstance(found, str) else None


def get_activity_id(parsed: object) -> str | None:
    """Pull the async job id out of the submit response."""
    if not isinstance(parsed, dict):
        return None
    data = parsed.get("data")
    if isinstance(data, dict) and isinstance(data.get("activity_id"), str):
        return data["activity_id"]
    value = parsed.get("activity_id")
    return value if isinstance(value, str) else None


def fmt(value: object) -> str:
    """Format a stat value: trim floats, leave everything else readable."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (int, str)):
        return str(value)
    return json.dumps(value)


def print_mapping(mapping: dict, indent: str = "    ") -> None:
    """Print a flat-ish dict as an aligned key/value block."""
    if not mapping:
        print(f"{indent}(empty)")
        return
    width = max(len(str(k)) for k in mapping)
    for key, value in mapping.items():
        if isinstance(value, dict):
            print(f"{indent}{str(key).ljust(width)} :")
            print_mapping(value, indent + "    ")
        elif isinstance(value, list) and len(value) > 8:
            print(f"{indent}{str(key).ljust(width)} : [{len(value)} values]")
        else:
            print(f"{indent}{str(key).ljust(width)} : {fmt(value)}")


def print_body_preview(response: requests.Response, api_key: str) -> None:
    """Print a truncated body, used for error paths only."""
    if not response.text:
        print("  (empty body)")
        return
    parsed = parse_json(response)
    text = (
        json.dumps(parsed, indent=2) if parsed is not None else response.text
    )
    text = redact(text, api_key)
    print(text[:MAX_BODY_CHARS])
    if len(text) > MAX_BODY_CHARS:
        print(f"  ... truncated ({len(text)} chars total)")


# -----------------------------------------------------------------------------
# Step 1: submit
# -----------------------------------------------------------------------------
def submit_job(
    session: requests.Session, payload: dict, api_key: str
) -> tuple[str | None, bool]:
    """
    Submit the heatmap job.

    Returns (activity_id, fatal_error). activity_id is None when submission
    failed for any reason.
    """
    _rule("STEP 1 of 2: SUBMIT  (POST /v1/heatmap)")
    print(f"  Endpoint    : POST {SUBMIT_URL}")
    print(f"  Auth header : api-key: ***REDACTED*** ({len(api_key)} chars loaded)")
    print(f"  Location    : Phoenix, AZ ({PHOENIX_LAT}, {PHOENIX_LON})")
    print(f"  Test area   : {BOX_SIZE_KM} km x {BOX_SIZE_KM} km box")
    ring = payload["polygon_aoi"]["features"][0]["geometry"]["coordinates"][0]
    print(
        f"  Bounding box: lon {ring[0][0]:.6f} .. {ring[1][0]:.6f}, "
        f"lat {ring[0][1]:.6f} .. {ring[2][1]:.6f}"
    )
    dt = payload["date_time"]
    print(f"  Date / time : {dt['start_date']} at {dt['start_time']}")
    print(f"  filter_type : {dt['filter_type']}")
    print(f"  granularity : {payload['granularity']}")

    try:
        response = session.post(
            SUBMIT_URL, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.exceptions.RequestException as exc:
        print(f"\n  SUBMIT FAILED: {type(exc).__name__}: {redact(str(exc), api_key)}")
        return None, True

    parsed = parse_json(response)
    print(f"\n  HTTP status : {response.status_code} {response.reason}")
    print(f"  Elapsed     : {response.elapsed.total_seconds():.2f}s")

    limit = response.headers.get("x-ratelimit-limit")
    remaining = response.headers.get("x-ratelimit-remaining")
    if limit or remaining:
        print(f"  Rate limit  : {remaining} of {limit} requests remaining")

    if not response.ok or (isinstance(parsed, dict) and parsed.get("error") is True):
        print("\n  --- Response body ---")
        print_body_preview(response, api_key)
        print("\n  SUBMIT REJECTED - see the body above. Not polling.")
        return None, True

    message = ""
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or "")
    activity_id = get_activity_id(parsed)

    if not activity_id:
        print("\n  --- Response body ---")
        print_body_preview(response, api_key)
        print("\n  SUBMIT returned 200 but no activity_id, so there is nothing")
        print("  to poll. The response shape may have changed.")
        return None, True

    if message:
        print(f'  Message     : "{message}"')
    print(f"  Activity id : {activity_id}")
    print("\n  Job accepted. Moving on to polling.")
    return activity_id, False


# -----------------------------------------------------------------------------
# Step 2: poll
# -----------------------------------------------------------------------------
def poll_for_result(
    session: requests.Session, activity_id: str, api_key: str
) -> tuple[str, object]:
    """
    Poll the status endpoint until the job finishes, fails, or we time out.

    Returns (outcome, parsed) where outcome is one of:
    "completed", "failed", "timeout", "error".
    """
    status_url = STATUS_URL_TEMPLATE.format(activity_id=activity_id)

    _rule("STEP 2 of 2: POLL  (GET /v1/status/{activity_id})")
    print(f"  Endpoint    : GET {status_url}")
    print(
        f"  Strategy    : every {POLL_INTERVAL_SECONDS}s, up to "
        f"{MAX_POLL_ATTEMPTS} attempts "
        f"({POLL_INTERVAL_SECONDS * MAX_POLL_ATTEMPTS // 60} minutes max)"
    )
    print(f"  404 grace   : tolerated for the first {NOT_FOUND_GRACE_SECONDS}s")
    print("")

    started = time.monotonic()
    last_status: str | None = None
    consecutive_transient = 0

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        elapsed = time.monotonic() - started

        try:
            response = session.get(status_url, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as exc:
            consecutive_transient += 1
            print(
                f"  [{attempt:>3}] t={elapsed:>5.0f}s  network error "
                f"({type(exc).__name__}), retry {consecutive_transient}"
                f"/{MAX_CONSECUTIVE_TRANSIENT_ERRORS}"
            )
            if consecutive_transient >= MAX_CONSECUTIVE_TRANSIENT_ERRORS:
                print("\n  Too many consecutive network errors. Giving up.")
                return "error", None
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        # --- 404: expected briefly right after submission -------------------
        if response.status_code == 404:
            if elapsed <= NOT_FOUND_GRACE_SECONDS:
                print(
                    f"  [{attempt:>3}] t={elapsed:>5.0f}s  404 not found yet "
                    "(within grace window, still waiting)"
                )
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            print(
                f"  [{attempt:>3}] t={elapsed:>5.0f}s  404 after the "
                f"{NOT_FOUND_GRACE_SECONDS}s grace window"
            )
            print("\n  The activity id is not recognised by the status endpoint.")
            print("  --- Response body ---")
            print_body_preview(response, api_key)
            return "error", None

        # --- transient server errors ---------------------------------------
        if response.status_code >= 500:
            consecutive_transient += 1
            print(
                f"  [{attempt:>3}] t={elapsed:>5.0f}s  HTTP "
                f"{response.status_code} (server error), retry "
                f"{consecutive_transient}/{MAX_CONSECUTIVE_TRANSIENT_ERRORS}"
            )
            if consecutive_transient >= MAX_CONSECUTIVE_TRANSIENT_ERRORS:
                print("\n  Too many consecutive server errors. Giving up.")
                return "error", None
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        # --- hard client errors --------------------------------------------
        if not response.ok:
            print(f"  [{attempt:>3}] t={elapsed:>5.0f}s  HTTP {response.status_code}")
            print("\n  Polling rejected. --- Response body ---")
            print_body_preview(response, api_key)
            return "error", None

        consecutive_transient = 0
        parsed = parse_json(response)
        status = get_status(parsed)
        normalized = (status or "").strip().lower()

        # Print on the first attempt, on any status change, or periodically.
        changed = normalized != (last_status or "")
        if changed or attempt == 1 or attempt % PROGRESS_EVERY_N_ATTEMPTS == 0:
            shown = status if status else "(no status field)"
            print(f"  [{attempt:>3}] t={elapsed:>5.0f}s  status={shown}")
        last_status = normalized

        if normalized in COMPLETED_STATUSES:
            print(f"\n  Job finished after {elapsed:.0f}s ({attempt} polls).")
            return "completed", parsed

        if normalized in FAILED_STATUSES:
            print(f"\n  Job reported failure after {elapsed:.0f}s.")
            return "failed", parsed

        if not status:
            # No status field at all: if a result is already present, treat the
            # job as done rather than polling pointlessly for 10 minutes.
            if find_key(parsed, "stats_data") is not None:
                print("\n  No status field, but stats_data is present - treating")
                print("  the job as complete.")
                return "completed", parsed

        time.sleep(POLL_INTERVAL_SECONDS)

    print(
        f"\n  TIMED OUT after {MAX_POLL_ATTEMPTS} polls "
        f"({POLL_INTERVAL_SECONDS * MAX_POLL_ATTEMPTS // 60} minutes)."
    )
    print(f"  Last status seen: {last_status or 'unknown'}")
    return "timeout", None


# -----------------------------------------------------------------------------
# Result reporting
# -----------------------------------------------------------------------------
def report_result(parsed: object) -> bool:
    """
    Print the stats_data section and a structural summary of everything else.

    Returns True if temperature stats were found.
    """
    _rule("RESULT: TEMPERATURE STATISTICS FOR PHOENIX")

    stats = find_key(parsed, "stats_data")
    if stats is None:
        print("  No 'stats_data' section found in the completed response.")
        print("\n  Top-level structure of what came back:")
        describe_structure(parsed)
        return False

    if isinstance(stats, dict):
        print_mapping(stats)
    elif isinstance(stats, list):
        print(f"  stats_data is a list of {len(stats)} entries:")
        for i, entry in enumerate(stats[:10], start=1):
            print(f"\n    [{i}]")
            if isinstance(entry, dict):
                print_mapping(entry, indent="        ")
            else:
                print(f"        {fmt(entry)}")
        if len(stats) > 10:
            print(f"\n    ... {len(stats) - 10} more entries")
    else:
        print(f"  stats_data = {fmt(stats)}")

    print("\n  (Units are reported exactly as the API returned them; FortyGuard")
    print("   does not label them in this payload.)")

    print("\n  --- Rest of the payload (GeoJSON withheld: too large) ---")
    describe_structure(parsed)
    return True


def describe_structure(parsed: object, indent: str = "  ") -> None:
    """List keys and sizes without dumping large geometry blobs."""
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            kind = type(value).__name__
            if isinstance(value, (dict, list)):
                print(f"{indent}- {key} ({kind}, {len(value)} items)")
            else:
                shown = fmt(value)
                if len(shown) > 60:
                    shown = shown[:60] + "..."
                print(f"{indent}- {key} ({kind}) = {shown}")
    elif isinstance(parsed, list):
        print(f"{indent}(list of {len(parsed)} items)")
    else:
        print(f"{indent}{fmt(parsed)}")

    features = find_key(parsed, "features")
    if isinstance(features, list):
        print(f"{indent}- GeoJSON features found: {len(features)} (not printed)")


def print_failure_details(parsed: object) -> None:
    """Surface whatever the API said about a failed job."""
    if parsed is None:
        print("  No parsable response body was returned.")
        return
    for key in ("message", "error_message", "reason", "detail", "errors"):
        value = find_key(parsed, key)
        if value:
            print(f"  {key}: {fmt(value)}")
    print("\n  Full response structure:")
    describe_structure(parsed)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> int:
    api_key = load_api_key()

    start_date = (os.getenv("FORTYGUARD_TEST_DATE") or "").strip() or (
        date.today() - timedelta(days=1)
    ).isoformat()
    start_time = (os.getenv("FORTYGUARD_TEST_TIME") or "").strip() or DEFAULT_START_TIME

    ring = build_bbox_polygon(PHOENIX_LAT, PHOENIX_LON, BOX_SIZE_KM)
    payload = build_payload(ring, start_date, start_time)

    session = requests.Session()
    session.headers.update(
        {
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )

    _rule("FORTYGUARD TEMPERATURE API - FULL PIPELINE TEST")
    print("  Submit a heatmap job, poll until it finishes, then report the")
    print("  temperature statistics for a small box in Phoenix, AZ.")

    overall_start = time.monotonic()

    # ---- Step 1 ------------------------------------------------------------
    activity_id, fatal = submit_job(session, payload, api_key)
    if fatal or not activity_id:
        _rule("FINAL VERDICT")
        print("  PIPELINE FAILED at step 1 (submit).")
        print("  No job was created, so there was nothing to poll.")
        _rule()
        return 1

    # ---- Step 2 ------------------------------------------------------------
    outcome, result = poll_for_result(session, activity_id, api_key)
    total_elapsed = time.monotonic() - overall_start

    if outcome == "completed":
        had_stats = report_result(result)
        _rule("FINAL VERDICT")
        if had_stats:
            print("  PIPELINE SUCCEEDED END TO END.")
            print("")
            print("    submit -> poll -> retrieve temperature data for Phoenix")
            print("")
            print(f"    Activity id   : {activity_id}")
            print(f"    Total runtime : {total_elapsed:.0f}s")
            print("")
            print("  Real temperature statistics came back for the Phoenix test")
            print("  box, shown above. The API integration is fully working.")
            _rule()
            return 0

        print("  PIPELINE PARTIALLY SUCCEEDED.")
        print("")
        print("    submit -> OK,  poll -> OK,  temperature stats -> NOT FOUND")
        print("")
        print("  The job completed, but the response contained no 'stats_data'")
        print("  section. The structure above shows what did come back.")
        _rule()
        return 1

    _rule("FINAL VERDICT")
    if outcome == "failed":
        print("  PIPELINE FAILED at step 2: FortyGuard reported the job failed.")
        print("")
        print_failure_details(result)
    elif outcome == "timeout":
        print("  PIPELINE INCOMPLETE: the job never finished within the polling")
        print("  window. It may still be processing on FortyGuard's side.")
        print(f"\n    Activity id: {activity_id}")
        print("    You can re-check that id later without resubmitting.")
    else:
        print("  PIPELINE FAILED at step 2 (polling). See the details above.")
        print(f"\n    Activity id: {activity_id}")

    print(f"\n    Total runtime : {total_elapsed:.0f}s")
    _rule()
    return 1


if __name__ == "__main__":
    sys.exit(main())
