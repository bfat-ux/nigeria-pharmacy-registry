#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Geocoding Backfill

Uses the Google Geocoding API to reverse-geocode pharmacy records that
are missing state or LGA information. Updates canonical records in place.

Usage:
    export GOOGLE_PLACES_API_KEY="AIza..."

    # Dry run — show how many records need backfill:
    python agent-02-data-acquisition/scripts/geocode_backfill.py --dry-run

    # Backfill missing state/LGA on the deduped registry:
    python agent-02-data-acquisition/scripts/geocode_backfill.py \
        --input output/deduped/canonical_deduped_*.json

    # Backfill a specific source directory:
    python agent-02-data-acquisition/scripts/geocode_backfill.py \
        --input output/google_places/canonical_*.json

Dependencies:
    pip install requests

API costs:
    Geocoding API: $0.005 per request
    ~500 records ≈ $2.50
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
# Google Geocoding API
# ---------------------------------------------------------------------------

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Rate limiting
REQUESTS_PER_SECOND = 10
REQUEST_DELAY = 1.0 / REQUESTS_PER_SECOND

# Nigerian state bounding boxes for validation
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

KNOWN_STATES = set(STATE_BOXES.keys())


def clean_state_name(raw: str) -> str | None:
    """Normalize a state name from Google's geocoding response."""
    if not raw:
        return None

    if raw in KNOWN_STATES:
        return raw

    cleaned = raw.replace(" State", "").strip()
    if cleaned in KNOWN_STATES:
        return cleaned

    special = {
        "Federal Capital Territory": "FCT",
        "Abuja": "FCT",
        "Nassarawa": "Nasarawa",
    }
    if raw in special:
        return special[raw]
    if cleaned in special:
        return special[cleaned]

    # Case-insensitive
    for state in KNOWN_STATES:
        if cleaned.lower() == state.lower():
            return state

    return None


def reverse_geocode(
    lat: float,
    lon: float,
    api_key: str,
    session: requests.Session,
) -> dict[str, str | None]:
    """
    Reverse-geocode a coordinate using Google Geocoding API.
    Returns {"state": ..., "lga": ..., "address": ...} or nulls on failure.
    """
    params = {
        "latlng": f"{lat},{lon}",
        "key": api_key,
        "result_type": "administrative_area_level_1|administrative_area_level_2|street_address",
        "language": "en",
    }

    for attempt in range(3):
        try:
            resp = session.get(GEOCODING_URL, params=params, timeout=10)

            if resp.status_code == 200:
                data = resp.json()

                if data.get("status") == "OK":
                    return parse_geocode_result(data["results"])
                elif data.get("status") == "ZERO_RESULTS":
                    return {"state": None, "lga": None, "address": None}
                elif data.get("status") == "OVER_QUERY_LIMIT":
                    wait = 5 * (attempt + 1)
                    logger.warning("Over query limit. Waiting %ds...", wait)
                    time.sleep(wait)
                    continue
                elif data.get("status") == "REQUEST_DENIED":
                    logger.error("API key denied. Check Geocoding API is enabled.")
                    sys.exit(1)
                else:
                    logger.warning("Geocoding status: %s", data.get("status"))
                    return {"state": None, "lga": None, "address": None}

            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue

        except requests.exceptions.RequestException as e:
            logger.warning("Geocoding request failed: %s", e)
            time.sleep(3)

    return {"state": None, "lga": None, "address": None}


def parse_geocode_result(results: list[dict]) -> dict[str, str | None]:
    """Extract state, LGA, and address from geocoding results."""
    state = None
    lga = None
    address = None

    for result in results:
        components = result.get("address_components", [])
        for comp in components:
            types = comp.get("types", [])
            if "administrative_area_level_1" in types and not state:
                state = clean_state_name(comp.get("long_name", ""))
            if "administrative_area_level_2" in types and not lga:
                lga = comp.get("long_name")

        # Use formatted address from most specific result
        if not address and result.get("formatted_address"):
            address = result["formatted_address"]

    return {"state": state, "lga": lga, "address": address}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing state/LGA data using Google Geocoding API",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Glob pattern for canonical JSON files to process. "
             "Default: latest deduped file or all output canonical files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show how many records need backfill without making API calls.",
    )
    parser.add_argument(
        "--backfill-lga",
        action="store_true",
        default=True,
        help="Also backfill missing LGA (default: true).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Max records to geocode (for cost control).",
    )
    return parser.parse_args()


def find_input_files(pattern: str | None) -> list[str]:
    """Find canonical JSON files to process."""
    if pattern:
        files = glob.glob(pattern)
    else:
        # Try deduped first, then all canonical files
        files = glob.glob("output/deduped/canonical_deduped_*.json")
        if not files:
            files = glob.glob("output/**/canonical_*.json", recursive=True)

    return sorted(files)


def main():
    args = parse_args()

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key and not args.dry_run:
        print(
            "ERROR: Set GOOGLE_PLACES_API_KEY environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Find and load input files
    input_files = find_input_files(args.input)
    if not input_files:
        logger.error("No canonical JSON files found.")
        sys.exit(1)

    logger.info("Loading %d files...", len(input_files))

    all_records: list[dict] = []
    file_record_map: dict[str, list[dict]] = {}  # file_path -> records

    for fpath in input_files:
        with open(fpath, "r", encoding="utf-8") as f:
            records = json.load(f)
        if isinstance(records, list):
            all_records.extend(records)
            file_record_map[fpath] = records
        logger.info("  %s: %d records", fpath, len(records))

    logger.info("Total records: %d", len(all_records))

    # Find records needing backfill
    needs_state: list[dict] = []
    needs_lga: list[dict] = []
    no_coords: list[dict] = []

    for r in all_records:
        has_coords = r.get("latitude") is not None and r.get("longitude") is not None

        if not has_coords:
            no_coords.append(r)
            continue

        state = (r.get("state") or "").strip()
        lga = (r.get("lga") or "").strip()

        if not state or state == "Unknown":
            needs_state.append(r)
        elif args.backfill_lga and not lga:
            needs_lga.append(r)

    # Records to geocode: state-missing first (higher priority), then LGA-missing
    to_geocode = needs_state + needs_lga

    if args.max_records:
        to_geocode = to_geocode[:args.max_records]

    cost_estimate = len(to_geocode) * 0.005

    logger.info("=" * 60)
    logger.info("BACKFILL ANALYSIS")
    logger.info("  Total records      : %d", len(all_records))
    logger.info("  Missing state      : %d", len(needs_state))
    logger.info("  Missing LGA only   : %d", len(needs_lga))
    logger.info("  No coordinates     : %d (cannot geocode)", len(no_coords))
    logger.info("  To geocode         : %d", len(to_geocode))
    logger.info("  Est. cost          : $%.2f", cost_estimate)
    logger.info("=" * 60)

    if args.dry_run:
        # Show state breakdown of records needing backfill
        if needs_state:
            logger.info("Records missing state (by source):")
            src_counts: dict[str, int] = {}
            for r in needs_state:
                src = r.get("source_id", "unknown")
                src_counts[src] = src_counts.get(src, 0) + 1
            for src, count in sorted(src_counts.items(), key=lambda x: -x[1]):
                logger.info("  %-25s %d", src, count)
        return

    if not to_geocode:
        logger.info("Nothing to geocode. All records have state/LGA data.")
        return

    # Run geocoding
    session = requests.Session()
    updated = 0
    state_filled = 0
    lga_filled = 0
    failed = 0

    # Build index for quick lookup
    record_index = {r.get("pharmacy_id"): r for r in all_records}

    for i, rec in enumerate(to_geocode):
        time.sleep(REQUEST_DELAY)

        lat = rec["latitude"]
        lon = rec["longitude"]

        result = reverse_geocode(lat, lon, api_key, session)

        changed = False
        state_val = (rec.get("state") or "").strip()
        lga_val = (rec.get("lga") or "").strip()

        if result["state"] and (not state_val or state_val == "Unknown"):
            rec["state"] = result["state"]
            state_filled += 1
            changed = True

        if result["lga"] and not lga_val:
            rec["lga"] = result["lga"]
            lga_filled += 1
            changed = True

        # Also backfill address if missing
        if result["address"] and not rec.get("address_line"):
            rec["address_line"] = result["address"]
            changed = True

        if changed:
            rec["updated_at"] = datetime.now(timezone.utc).isoformat()
            updated += 1
        else:
            failed += 1

        # Progress
        if (i + 1) % 50 == 0:
            logger.info(
                "Progress: %d/%d (%.1f%%) — %d states filled, %d LGAs filled",
                i + 1, len(to_geocode),
                ((i + 1) / len(to_geocode)) * 100,
                state_filled, lga_filled,
            )

    # Write back to files
    for fpath, records in file_record_map.items():
        out_path = fpath  # overwrite in place
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        logger.info("Updated %s", out_path)

    # Summary
    logger.info("=" * 60)
    logger.info("GEOCODING BACKFILL SUMMARY")
    logger.info("  Records processed : %d", len(to_geocode))
    logger.info("  States filled     : %d", state_filled)
    logger.info("  LGAs filled       : %d", lga_filled)
    logger.info("  Records updated   : %d", updated)
    logger.info("  Failed/no-result  : %d", failed)
    logger.info("  Est. cost         : $%.2f", len(to_geocode) * 0.005)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
