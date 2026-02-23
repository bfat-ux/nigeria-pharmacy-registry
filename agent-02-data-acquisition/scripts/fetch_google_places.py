#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Google Places API Pharmacy Fetcher

Uses the Google Places API (New) Nearby Search to discover pharmacies
across Nigeria via a grid-search strategy. Nigeria is covered by a grid
of overlapping circles, each queried for pharmacy POIs.

Usage:
    # Set your API key:
    export GOOGLE_PLACES_API_KEY="AIza..."

    # Dry run — show grid size and estimated cost:
    python agent-02-data-acquisition/scripts/fetch_google_places.py --dry-run

    # Fetch only Lagos state (good for testing):
    python agent-02-data-acquisition/scripts/fetch_google_places.py \
        --state Lagos \
        --output agent-02-data-acquisition/sources/google_places_lagos.json

    # Full national fetch:
    python agent-02-data-acquisition/scripts/fetch_google_places.py \
        --output agent-02-data-acquisition/sources/google_places_extract.json

    # Resume an interrupted run:
    python agent-02-data-acquisition/scripts/fetch_google_places.py \
        --resume agent-02-data-acquisition/sources/google_places_extract.json

    # Then ingest:
    python agent-02-data-acquisition/scripts/ingest_template.py \
        --source-file agent-02-data-acquisition/sources/google_places_extract.json \
        --template agent-02-data-acquisition/templates/google_places_extract.json \
        --source-id src-google-places \
        --output-dir output/google_places/

Dependencies:
    pip install requests

API costs (as of 2024):
    Nearby Search (New): $0.032 per request (up to 20 results each)
    ~1,200 grid points nationally ≈ $38-40 total
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print(
        "ERROR: requests library required. Install with: pip install requests",
        file=sys.stderr,
    )
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Google Places API (New) configuration
# ---------------------------------------------------------------------------

PLACES_API_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Fields we want from each place.
# Billing: Basic fields = $0.00, Contact fields = +$0.003, Atmosphere = +$0.005
# We stick to Basic + Contact to keep costs low.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.shortFormattedAddress",
    "places.location",
    "places.internationalPhoneNumber",
    "places.nationalPhoneNumber",
    "places.types",
    "places.businessStatus",
    "places.addressComponents",
])

# We search for pharmacies specifically
INCLUDED_TYPES = ["pharmacy"]

# Max radius per request (meters). Google allows up to 50,000m.
# We use 25,000m (25km) for good overlap between grid cells.
SEARCH_RADIUS_M = 25_000

# Max results per request (Google returns up to 20)
MAX_RESULT_COUNT = 20

# Rate limiting: requests per second (stay well under quota)
REQUESTS_PER_SECOND = 2
REQUEST_DELAY = 1.0 / REQUESTS_PER_SECOND

# ---------------------------------------------------------------------------
# Nigerian state bounding boxes (reused from fetch_osm.py)
# Format: (min_lat, max_lat, min_lon, max_lon)
# ---------------------------------------------------------------------------

STATE_BOXES: dict[str, tuple[float, float, float, float]] = {
    "Lagos": (6.38, 6.71, 3.08, 3.70),
    "FCT": (8.75, 9.22, 6.98, 7.62),
    "Kano": (11.50, 12.70, 8.20, 9.40),
    "Rivers": (4.30, 5.30, 6.50, 7.60),
    "Oyo": (7.10, 8.70, 3.00, 4.60),
    "Anambra": (5.75, 6.85, 6.60, 7.20),
    "Abia": (4.80, 5.95, 7.10, 7.90),
    "Delta": (5.05, 6.50, 5.30, 6.80),
    "Enugu": (6.05, 7.05, 7.00, 7.90),
    "Kaduna": (9.20, 11.30, 6.20, 8.80),
    "Ogun": (6.35, 7.75, 2.70, 3.90),
    "Ondo": (5.85, 7.70, 4.30, 5.85),
    "Edo": (5.75, 7.35, 5.00, 6.65),
    "Imo": (5.05, 5.95, 6.80, 7.45),
    "Osun": (7.00, 8.10, 3.90, 5.00),
    "Kwara": (7.70, 9.70, 3.50, 6.10),
    "Plateau": (8.50, 10.05, 8.50, 10.20),
    "Kogi": (6.70, 8.70, 5.50, 7.80),
    "Ekiti": (7.35, 8.10, 4.70, 5.70),
    "Niger": (8.20, 11.10, 3.50, 7.50),
    "Benue": (6.40, 8.20, 7.60, 10.00),
    "Akwa Ibom": (4.45, 5.40, 7.30, 8.30),
    "Cross River": (4.50, 6.85, 7.85, 9.50),
    "Bayelsa": (4.20, 5.20, 5.60, 6.70),
    "Ebonyi": (5.70, 6.60, 7.65, 8.40),
    "Nasarawa": (7.80, 9.20, 7.50, 9.60),
    "Taraba": (6.50, 9.50, 9.50, 11.80),
    "Adamawa": (7.50, 10.70, 11.50, 13.70),
    "Sokoto": (11.70, 13.80, 4.00, 6.10),
    "Kebbi": (10.50, 13.00, 3.40, 5.60),
    "Zamfara": (11.40, 13.10, 5.60, 7.30),
    "Katsina": (11.50, 13.40, 6.60, 8.60),
    "Jigawa": (11.50, 13.30, 8.60, 10.60),
    "Bauchi": (9.40, 11.50, 8.80, 10.90),
    "Gombe": (9.70, 11.10, 10.80, 11.90),
    "Borno": (10.00, 13.70, 11.50, 14.70),
    "Yobe": (10.80, 13.30, 10.30, 12.80),
}

# Nigeria national bounding box (used when no --state is specified)
NIGERIA_BBOX = (4.0, 14.0, 2.5, 14.7)  # (min_lat, max_lat, min_lon, max_lon)


def coords_to_state(lat: float, lon: float) -> str | None:
    """Map a (lat, lon) coordinate to a Nigerian state using bounding boxes."""
    for state_name, (min_lat, max_lat, min_lon, max_lon) in STATE_BOXES.items():
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return state_name
    return None


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_grid(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    radius_m: float,
) -> list[tuple[float, float]]:
    """
    Generate a grid of (lat, lon) center points that cover the given
    bounding box with overlapping circles of the specified radius.

    Uses a hex-style offset grid for efficient coverage with ~15% overlap
    to avoid gaps between circles.
    """
    # Convert radius to approximate degrees.
    # 1 degree latitude ≈ 111 km everywhere.
    # 1 degree longitude ≈ 111 km * cos(latitude) — varies with latitude.
    # We use 85% of the diameter as step size to ensure overlap.
    step_factor = 0.85  # overlap factor (smaller = more overlap)
    radius_km = radius_m / 1000.0

    lat_step = (radius_km * 2 * step_factor) / 111.0
    mid_lat = (min_lat + max_lat) / 2.0
    lon_step = (radius_km * 2 * step_factor) / (111.0 * math.cos(math.radians(mid_lat)))

    points = []
    lat = min_lat
    row = 0
    while lat <= max_lat:
        # Offset every other row by half a step for hex-style packing
        lon_offset = (lon_step / 2) if (row % 2 == 1) else 0.0
        lon = min_lon + lon_offset
        while lon <= max_lon:
            points.append((round(lat, 5), round(lon, 5)))
            lon += lon_step
        lat += lat_step
        row += 1

    return points


# ---------------------------------------------------------------------------
# Google Places API interaction
# ---------------------------------------------------------------------------


def search_nearby(
    api_key: str,
    lat: float,
    lon: float,
    radius_m: float,
    session: requests.Session,
) -> list[dict]:
    """
    Execute a single Nearby Search (New) request and return the places list.
    Returns an empty list on error (logged but non-fatal).
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }

    body = {
        "includedTypes": INCLUDED_TYPES,
        "maxResultCount": MAX_RESULT_COUNT,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": lat,
                    "longitude": lon,
                },
                "radius": radius_m,
            }
        },
    }

    for attempt in range(3):
        try:
            resp = session.post(
                PLACES_API_URL,
                json=body,
                headers=headers,
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data.get("places", [])

            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                logger.warning(
                    "Rate limited (429) at (%.4f, %.4f). Waiting %ds...",
                    lat, lon, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code == 400:
                # Bad request — likely invalid params, skip this point
                logger.warning(
                    "Bad request (400) at (%.4f, %.4f): %s",
                    lat, lon, resp.text[:200],
                )
                return []

            if resp.status_code == 403:
                logger.error(
                    "API key error (403). Check that Places API (New) is enabled "
                    "and the key is valid. Response: %s",
                    resp.text[:300],
                )
                sys.exit(1)

            # Other errors — retry
            logger.warning(
                "HTTP %d at (%.4f, %.4f). Retrying...",
                resp.status_code, lat, lon,
            )
            time.sleep(5 * (attempt + 1))

        except requests.exceptions.Timeout:
            logger.warning(
                "Timeout at (%.4f, %.4f) (attempt %d/3)",
                lat, lon, attempt + 1,
            )
            time.sleep(5)
        except requests.exceptions.RequestException as e:
            logger.warning(
                "Request error at (%.4f, %.4f): %s",
                lat, lon, e,
            )
            time.sleep(5)

    logger.error("All attempts failed for (%.4f, %.4f). Skipping.", lat, lon)
    return []


# ---------------------------------------------------------------------------
# Places response → ingestion records
# ---------------------------------------------------------------------------


def parse_place(place: dict) -> dict | None:
    """
    Convert a single Google Places API (New) result into an ingestion record
    matching the generic import format.
    """
    place_id = place.get("id")
    if not place_id:
        return None

    # Location coordinates
    location = place.get("location", {})
    lat = location.get("latitude")
    lon = location.get("longitude")

    if lat is None or lon is None:
        return None

    # Check within Nigeria bounds
    if not (3.0 <= lat <= 14.0 and 2.0 <= lon <= 15.0):
        return None

    # Display name
    display_name = place.get("displayName", {})
    name = display_name.get("text", "Unnamed Pharmacy")

    # Address
    formatted_address = place.get("formattedAddress", "")
    short_address = place.get("shortFormattedAddress", "")

    # Phone numbers — prefer international format
    phone_intl = place.get("internationalPhoneNumber")
    phone_national = place.get("nationalPhoneNumber")
    phone = phone_intl or phone_national

    # Normalize Nigerian phone numbers for our schema
    if phone:
        phone = normalize_phone(phone)

    # Business status → operational_status
    biz_status = place.get("businessStatus", "")
    operational_status = map_business_status(biz_status)

    # Extract state and LGA from address components
    state = None
    lga = None
    address_components = place.get("addressComponents", [])
    for comp in address_components:
        types = comp.get("types", [])
        if "administrative_area_level_1" in types:
            state = comp.get("longText")
        elif "administrative_area_level_2" in types:
            lga = comp.get("longText")

    # If state not found in components, try coordinate lookup
    if not state:
        state = coords_to_state(lat, lon)

    # Clean up state name — Google sometimes returns "Lagos State" etc.
    if state:
        state = clean_state_name(state)

    record = {
        "google_place_id": place_id,
        "name": name,
        "formatted_address": formatted_address,
        "short_address": short_address,
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "phone": phone,
        "state": state,
        "lga": lga,
        "operational_status": operational_status,
        "types": place.get("types", []),
    }

    return record


def normalize_phone(phone: str) -> str | None:
    """
    Normalize a phone string to match our schema pattern: (+234|0)XXXXXXXXXX
    Returns None if the phone can't be normalized to Nigerian format.
    """
    if not phone:
        return None

    # Strip spaces, dashes, parentheses, dots
    digits = ""
    has_plus = phone.strip().startswith("+")
    for ch in phone:
        if ch.isdigit():
            digits += ch

    if not digits:
        return None

    # Convert to +234 format
    if has_plus and digits.startswith("234"):
        return "+" + digits
    elif digits.startswith("234") and len(digits) >= 13:
        return "+" + digits
    elif digits.startswith("0") and len(digits) >= 11:
        return "+234" + digits[1:]
    elif len(digits) >= 10:
        # Assume it's a local number missing prefix
        return "+234" + digits

    return None


def map_business_status(status: str) -> str:
    """Map Google's businessStatus to our canonical operational_status."""
    mapping = {
        "OPERATIONAL": "operational",
        "CLOSED_TEMPORARILY": "temporarily_closed",
        "CLOSED_PERMANENTLY": "permanently_closed",
    }
    return mapping.get(status, "unknown")


def clean_state_name(raw: str) -> str | None:
    """
    Clean up a state name from Google's address components.
    Google sometimes returns "Lagos State", "Federal Capital Territory", etc.
    """
    if not raw:
        return None

    # Direct lookup first
    known_states = set(STATE_BOXES.keys())
    if raw in known_states:
        return raw

    # Common transformations
    cleaned = raw.replace(" State", "").strip()
    if cleaned in known_states:
        return cleaned

    # Special cases
    special_map = {
        "Federal Capital Territory": "FCT",
        "Abuja": "FCT",
        "Nasarawa": "Nasarawa",
        "Nassarawa": "Nasarawa",
    }
    if raw in special_map:
        return special_map[raw]
    if cleaned in special_map:
        return special_map[cleaned]

    # Case-insensitive match
    for state in known_states:
        if cleaned.lower() == state.lower():
            return state

    logger.debug("Could not match state name: '%s'", raw)
    return None


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------


def load_progress(output_path: str) -> tuple[set[str], list[dict], set[tuple[float, float]]]:
    """
    Load an existing output file for resume support.
    Returns (seen_place_ids, existing_records, completed_grid_points).
    """
    path = Path(output_path)
    if not path.exists():
        return set(), [], set()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data.get("records", [])
    seen_ids = {r["google_place_id"] for r in records if "google_place_id" in r}

    # Extract completed grid points from metadata
    completed = set()
    for pt in data.get("_completed_grid_points", []):
        completed.add((pt[0], pt[1]))

    logger.info(
        "Resuming: %d existing records, %d completed grid points",
        len(records), len(completed),
    )

    return seen_ids, records, completed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Nigerian pharmacy locations from Google Places API (New)",
    )
    parser.add_argument(
        "--output",
        default="agent-02-data-acquisition/sources/google_places_extract.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="Fetch only a single state (e.g. 'Lagos'). Omit for national.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show grid size and estimated cost without making API calls.",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="FILE",
        help="Resume from a previous partial output file.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=SEARCH_RADIUS_M,
        help=f"Search radius in meters (default: {SEARCH_RADIUS_M})",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=50,
        help="Save progress every N grid points (default: 50)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Get API key
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key and not args.dry_run:
        print(
            "ERROR: Set GOOGLE_PLACES_API_KEY environment variable.\n"
            "  export GOOGLE_PLACES_API_KEY='AIza...'\n\n"
            "To get a key:\n"
            "  1. Go to https://console.cloud.google.com/\n"
            "  2. Enable 'Places API (New)'\n"
            "  3. Create an API key\n"
            "  4. (Optional) Restrict to Places API only",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine search area
    if args.state:
        if args.state not in STATE_BOXES:
            print(
                f"ERROR: Unknown state '{args.state}'. "
                f"Valid states: {', '.join(sorted(STATE_BOXES.keys()))}",
                file=sys.stderr,
            )
            sys.exit(1)
        bbox = STATE_BOXES[args.state]
        scope = args.state
    else:
        bbox = NIGERIA_BBOX
        scope = "National"

    min_lat, max_lat, min_lon, max_lon = bbox

    # Generate grid
    grid = generate_grid(min_lat, max_lat, min_lon, max_lon, args.radius)
    logger.info("Scope: %s", scope)
    logger.info("Grid points: %d (radius: %dm)", len(grid), args.radius)

    # Cost estimate
    cost_per_request = 0.032  # Nearby Search (New) pricing
    estimated_cost = len(grid) * cost_per_request

    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"GOOGLE PLACES API — DRY RUN")
        print(f"{'='*60}")
        print(f"  Scope           : {scope}")
        print(f"  Bounding box    : ({min_lat}, {max_lat}) × ({min_lon}, {max_lon})")
        print(f"  Search radius   : {args.radius:,}m ({args.radius/1000:.1f}km)")
        print(f"  Grid points     : {len(grid):,}")
        print(f"  Est. API calls  : {len(grid):,}")
        print(f"  Est. cost       : ${estimated_cost:.2f}")
        print(f"  Max results     : {len(grid) * MAX_RESULT_COUNT:,} (up to 20 per cell)")
        print(f"  Est. time       : {len(grid) / REQUESTS_PER_SECOND / 60:.1f} minutes")
        print(f"{'='*60}\n")
        return

    logger.info("Estimated cost: $%.2f (%d API calls)", estimated_cost, len(grid))

    # Resume support
    if args.resume:
        seen_ids, all_records, completed_points = load_progress(args.resume)
        # Use resume file as output too
        args.output = args.resume
    else:
        seen_ids: set[str] = set()
        all_records: list[dict] = []
        completed_points: set[tuple[float, float]] = set()

    # Filter out already-completed grid points
    remaining_grid = [pt for pt in grid if pt not in completed_points]
    logger.info(
        "Grid points remaining: %d / %d", len(remaining_grid), len(grid),
    )

    if not remaining_grid:
        logger.info("All grid points already completed. Nothing to do.")
        return

    # Fetch
    session = requests.Session()
    query_ts = datetime.now(timezone.utc).isoformat()
    new_records = 0
    duplicates = 0
    errors = 0
    points_done = 0

    try:
        for i, (lat, lon) in enumerate(remaining_grid):
            # Rate limiting
            time.sleep(REQUEST_DELAY)

            places = search_nearby(api_key, lat, lon, args.radius, session)

            for place in places:
                parsed = parse_place(place)
                if parsed is None:
                    continue

                pid = parsed["google_place_id"]
                if pid in seen_ids:
                    duplicates += 1
                    continue

                seen_ids.add(pid)
                all_records.append(parsed)
                new_records += 1

            completed_points.add((lat, lon))
            points_done += 1

            # Progress log every 25 points
            if (i + 1) % 25 == 0:
                logger.info(
                    "Progress: %d/%d grid points (%.1f%%) — "
                    "%d new records, %d duplicates skipped",
                    points_done + len(grid) - len(remaining_grid),
                    len(grid),
                    ((points_done + len(grid) - len(remaining_grid)) / len(grid)) * 100,
                    new_records,
                    duplicates,
                )

            # Periodic save
            if points_done % args.save_every == 0:
                save_output(
                    args.output, all_records, completed_points,
                    query_ts, scope, args.radius,
                )
                logger.info("Progress saved (%d records so far)", len(all_records))

    except KeyboardInterrupt:
        logger.warning("\nInterrupted! Saving progress...")
        save_output(
            args.output, all_records, completed_points,
            query_ts, scope, args.radius,
        )
        logger.info(
            "Progress saved. Resume with: --resume %s", args.output,
        )
        sys.exit(0)

    # Final save
    save_output(
        args.output, all_records, completed_points,
        query_ts, scope, args.radius,
    )

    # Summary
    logger.info("=" * 60)
    logger.info("GOOGLE PLACES FETCH SUMMARY")
    logger.info("  Scope            : %s", scope)
    logger.info("  Grid points      : %d", len(grid))
    logger.info("  API calls made   : %d", points_done)
    logger.info("  New records      : %d", new_records)
    logger.info("  Duplicates skip  : %d", duplicates)
    logger.info("  Total records    : %d", len(all_records))
    logger.info("  Est. cost        : $%.2f", points_done * cost_per_request)
    logger.info("  Output           : %s", args.output)
    logger.info("=" * 60)

    # State breakdown
    state_counts: dict[str, int] = {}
    for rec in all_records:
        s = rec.get("state") or "Unknown"
        state_counts[s] = state_counts.get(s, 0) + 1

    if state_counts:
        logger.info("Records by state:")
        for state, count in sorted(state_counts.items(), key=lambda x: -x[1]):
            logger.info("  %-20s %d", state, count)

    logger.info(
        "\nNext: python agent-02-data-acquisition/scripts/ingest_template.py "
        "--source-file %s "
        "--template agent-02-data-acquisition/templates/google_places_extract.json "
        "--source-id src-google-places "
        "--output-dir output/google_places/",
        args.output,
    )


def save_output(
    output_path: str,
    records: list[dict],
    completed_points: set[tuple[float, float]],
    query_ts: str,
    scope: str,
    radius: int,
) -> None:
    """Write current records and progress metadata to the output file."""
    output = {
        "source_id": "src-google-places",
        "google_places_query_timestamp": query_ts,
        "scope": scope,
        "search_radius_m": radius,
        "total_records": len(records),
        "records": records,
        # Save completed points for resume support
        "_completed_grid_points": sorted(completed_points),
    }

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file first, then rename (atomic save)
    tmp_path = out_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    tmp_path.rename(out_path)


if __name__ == "__main__":
    main()
