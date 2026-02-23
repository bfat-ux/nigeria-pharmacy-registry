#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — OpenStreetMap Pharmacy Fetcher

Queries the Overpass API for pharmacy POIs in Nigeria, maps coordinates
to Nigerian states, and outputs records ready for the ingestion pipeline.

Usage:
    # Fetch all Nigerian pharmacies and pipe through the ingestion pipeline:
    python agent-02-data-acquisition/scripts/fetch_osm.py \
        --output agent-02-data-acquisition/sources/osm_extract.json

    # Fetch only Lagos state (faster for testing):
    python agent-02-data-acquisition/scripts/fetch_osm.py \
        --state Lagos \
        --output agent-02-data-acquisition/sources/osm_extract_lagos.json

    # Then ingest:
    python agent-02-data-acquisition/scripts/ingest_template.py \
        --source-file agent-02-data-acquisition/sources/osm_extract.json \
        --template agent-02-data-acquisition/templates/osm_extract.json \
        --source-id src-osm-pharmacy \
        --output-dir output/osm/

Dependencies:
    pip install requests
"""

from __future__ import annotations

import argparse
import json
import logging
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
# Overpass API
# ---------------------------------------------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Query from source_registry.json — fetch pharmacies in Nigeria.
# Uses both amenity=pharmacy and healthcare=pharmacy tags.
OVERPASS_QUERY_NATIONAL = """
[out:json][timeout:120];
area["name"="Nigeria"]->.ng;
(
  node["amenity"="pharmacy"](area.ng);
  way["amenity"="pharmacy"](area.ng);
  node["healthcare"="pharmacy"](area.ng);
  way["healthcare"="pharmacy"](area.ng);
);
out center;
"""

# State-scoped query template (faster, useful for testing or incremental runs)
OVERPASS_QUERY_STATE = """
[out:json][timeout:60];
area["name"="{state_name}"]["admin_level"="4"]->.state;
(
  node["amenity"="pharmacy"](area.state);
  way["amenity"="pharmacy"](area.state);
  node["healthcare"="pharmacy"](area.state);
  way["healthcare"="pharmacy"](area.state);
);
out center;
"""

# ---------------------------------------------------------------------------
# Nigerian state lookup from coordinates
# ---------------------------------------------------------------------------

# Approximate bounding boxes for all 36 states + FCT.
# Format: (min_lat, max_lat, min_lon, max_lon)
# These are rough rectangles — good enough for assigning a state when
# OSM doesn't have addr:state tagged (which is most of the time).
# Ordered roughly by pharmacy density so common states match first.

_STATE_BOXES: list[tuple[str, tuple[float, float, float, float]]] = [
    ("Lagos", (6.38, 6.71, 3.08, 3.70)),
    ("FCT", (8.75, 9.22, 6.98, 7.62)),
    ("Kano", (11.50, 12.70, 8.20, 9.40)),
    ("Rivers", (4.30, 5.30, 6.50, 7.60)),
    ("Oyo", (7.10, 8.70, 3.00, 4.60)),
    ("Anambra", (5.75, 6.85, 6.60, 7.20)),
    ("Abia", (4.80, 5.95, 7.10, 7.90)),
    ("Delta", (5.05, 6.50, 5.30, 6.80)),
    ("Enugu", (6.05, 7.05, 7.00, 7.90)),
    ("Kaduna", (9.20, 11.30, 6.20, 8.80)),
    ("Ogun", (6.35, 7.75, 2.70, 3.90)),
    ("Ondo", (5.85, 7.70, 4.30, 5.85)),
    ("Edo", (5.75, 7.35, 5.00, 6.65)),
    ("Imo", (5.05, 5.95, 6.80, 7.45)),
    ("Osun", (7.00, 8.10, 3.90, 5.00)),
    ("Kwara", (7.70, 9.70, 3.50, 6.10)),
    ("Plateau", (8.50, 10.05, 8.50, 10.20)),
    ("Kogi", (6.70, 8.70, 5.50, 7.80)),
    ("Ekiti", (7.35, 8.10, 4.70, 5.70)),
    ("Niger", (8.20, 11.10, 3.50, 7.50)),
    ("Benue", (6.40, 8.20, 7.60, 10.00)),
    ("Akwa Ibom", (4.45, 5.40, 7.30, 8.30)),
    ("Cross River", (4.50, 6.85, 7.85, 9.50)),
    ("Bayelsa", (4.20, 5.20, 5.60, 6.70)),
    ("Ebonyi", (5.70, 6.60, 7.65, 8.40)),
    ("Nasarawa", (7.80, 9.20, 7.50, 9.60)),
    ("Taraba", (6.50, 9.50, 9.50, 11.80)),
    ("Adamawa", (7.50, 10.70, 11.50, 13.70)),
    ("Sokoto", (11.70, 13.80, 4.00, 6.10)),
    ("Kebbi", (10.50, 13.00, 3.40, 5.60)),
    ("Zamfara", (11.40, 13.10, 5.60, 7.30)),
    ("Katsina", (11.50, 13.40, 6.60, 8.60)),
    ("Jigawa", (11.50, 13.30, 8.60, 10.60)),
    ("Bauchi", (9.40, 11.50, 8.80, 10.90)),
    ("Gombe", (9.70, 11.10, 10.80, 11.90)),
    ("Borno", (10.00, 13.70, 11.50, 14.70)),
    ("Yobe", (10.80, 13.30, 10.30, 12.80)),
]


def coords_to_state(lat: float, lon: float) -> str | None:
    """
    Map a (lat, lon) coordinate to a Nigerian state using bounding boxes.

    Returns the state name or None if no match. When coordinates fall
    in overlapping boxes (border areas), the first match wins — boxes
    are ordered by pharmacy density so the more likely state matches first.
    """
    for state_name, (min_lat, max_lat, min_lon, max_lon) in _STATE_BOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return state_name
    return None


# ---------------------------------------------------------------------------
# Overpass response → ingestion records
# ---------------------------------------------------------------------------


def parse_overpass_element(element: dict) -> dict | None:
    """
    Convert a single Overpass API JSON element to an ingestion record
    matching the osm_extract.json template schema.
    """
    osm_type = element.get("type")  # "node" or "way"
    osm_id = element.get("id")

    if osm_type is None or osm_id is None:
        return None

    # For nodes: lat/lon are top-level.
    # For ways: lat/lon come from the "center" field (requested via `out center`).
    if osm_type == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")

    if lat is None or lon is None:
        return None

    # Check coordinates are within Nigeria
    if not (3.0 <= lat <= 14.0 and 2.0 <= lon <= 15.0):
        return None

    tags = element.get("tags", {})

    # Determine state from OSM tags or reverse lookup from coordinates
    addr_state = tags.get("addr:state")
    if not addr_state:
        addr_state = coords_to_state(lat, lon)

    record = {
        "osm_id": osm_id,
        "osm_type": osm_type,
        "name": tags.get("name"),
        "amenity": tags.get("amenity"),
        "healthcare": tags.get("healthcare"),
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "addr_street": tags.get("addr:street"),
        "addr_city": tags.get("addr:city"),
        "addr_state": addr_state,
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "opening_hours": tags.get("opening_hours"),
        "operator": tags.get("operator"),
    }

    return record


def fetch_overpass(query: str) -> list[dict]:
    """
    Execute an Overpass API query and return the parsed elements.
    Includes retry logic with backoff for rate limiting.
    """
    logger.info("Querying Overpass API...")

    for attempt in range(3):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=180,
                headers={"User-Agent": "NigeriaPharmacyRegistry/0.1"},
            )

            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning("Rate limited (429). Waiting %ds...", wait)
                time.sleep(wait)
                continue

            if resp.status_code == 504:
                logger.warning("Gateway timeout (504). Retrying...")
                time.sleep(10)
                continue

            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])
            logger.info("Received %d elements from Overpass", len(elements))
            return elements

        except requests.exceptions.Timeout:
            logger.warning("Request timed out (attempt %d/3)", attempt + 1)
            time.sleep(10)
        except requests.exceptions.RequestException as e:
            logger.error("Request failed: %s", e)
            if attempt < 2:
                time.sleep(10)

    logger.error("All Overpass API attempts failed")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Nigerian pharmacy locations from OpenStreetMap",
    )
    parser.add_argument(
        "--output",
        default="agent-02-data-acquisition/sources/osm_extract.json",
        help="Output JSON file path (default: agent-02-data-acquisition/sources/osm_extract.json)",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="Fetch only a single state (e.g. 'Lagos'). Omit for national.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Overpass query without executing it.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Build query
    if args.state:
        query = OVERPASS_QUERY_STATE.format(state_name=args.state)
        scope = args.state
    else:
        query = OVERPASS_QUERY_NATIONAL
        scope = "National"

    logger.info("Scope: %s", scope)

    if args.dry_run:
        print(query)
        return

    # Fetch
    elements = fetch_overpass(query)

    if not elements:
        logger.warning("No pharmacy elements found. Exiting.")
        sys.exit(0)

    # Parse elements into ingestion records
    query_ts = datetime.now(timezone.utc).isoformat()
    records = []
    skipped = 0
    no_state = 0

    for el in elements:
        rec = parse_overpass_element(el)
        if rec is None:
            skipped += 1
            continue
        if rec["addr_state"] is None:
            no_state += 1
            skipped += 1
            continue
        records.append(rec)

    # Deduplicate by osm_id + osm_type (Overpass can return duplicates
    # when an element matches multiple query clauses)
    seen = set()
    unique_records = []
    for rec in records:
        key = (rec["osm_type"], rec["osm_id"])
        if key not in seen:
            seen.add(key)
            unique_records.append(rec)
    deduped = len(records) - len(unique_records)
    records = unique_records

    # Build output in template format
    output = {
        "source_id": "src-osm-pharmacy",
        "overpass_query_timestamp": query_ts,
        "records": records,
    }

    # Write
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Summary
    logger.info("=" * 60)
    logger.info("OSM FETCH SUMMARY")
    logger.info("  Scope          : %s", scope)
    logger.info("  Elements       : %d", len(elements))
    logger.info("  Parsed records : %d", len(records))
    logger.info("  Skipped        : %d (no coords or outside Nigeria)", skipped - no_state)
    logger.info("  No state match : %d (coords didn't map to a state)", no_state)
    logger.info("  Deduped        : %d (appeared in multiple query clauses)", deduped)
    logger.info("  Output         : %s", out_path)
    logger.info("=" * 60)
    logger.info(
        "Next: python agent-02-data-acquisition/scripts/ingest_template.py "
        "--source-file %s "
        "--template agent-02-data-acquisition/templates/osm_extract.json "
        "--source-id src-osm-pharmacy "
        "--output-dir output/osm/",
        out_path,
    )


if __name__ == "__main__":
    main()
