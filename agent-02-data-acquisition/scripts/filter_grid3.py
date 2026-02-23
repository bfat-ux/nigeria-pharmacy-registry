#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — GRID3 Health Facilities Filter

Reads the full GRID3 NGA Health Facilities CSV (~51k records) and extracts
only pharmacy/PPMV-relevant records based on facility name patterns.

Outputs a clean CSV in the generic_pharmacy_import format, ready for the
ingestion pipeline.

Usage:
    python agent-02-data-acquisition/scripts/filter_grid3.py \
        --input agent-02-data-acquisition/GRID3_NGA_health_facilities_v2_0_5806009649412052847.csv \
        --output output/grid3/grid3_pharmacies.csv

    # Then ingest:
    python agent-02-data-acquisition/scripts/ingest_template.py \
        --source-file output/grid3/grid3_pharmacies.csv \
        --template agent-02-data-acquisition/templates/generic_pharmacy_import.json \
        --source-id src-grid3-health \
        --output-dir output/grid3/
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pharmacy detection
# ---------------------------------------------------------------------------

# Patterns that indicate a pharmacy or PPMV.
# Applied to the facility_name field.
_PHARMACY_NAME_RE = re.compile(
    r"""
    pharma                  # pharmacy, pharmaceutical, pharma
    | ppmv                  # patent proprietary medicine vendor
    | drug\s*store          # drug store
    | chemist               # chemist
    | patent\s*medicine     # patent medicine store/vendor
    | dispensary            # dispensary (sometimes a pharmacy)
    | dispensing            # dispensing outlet
    | med\s*store           # medical store (common PPMV name)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Patterns that DISQUALIFY a match (hospitals with "pharma" in the name)
_EXCLUDE_RE = re.compile(
    r"""
    hospital(?!\s*pharma)   # "Pharmaco Hospital" — exclude unless "Hospital Pharmacy"
    | maternity
    | clinic(?!.*pharma)    # exclude clinics unless they contain "pharma"
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_pharmacy_record(row: dict) -> bool:
    """
    Determine whether a GRID3 row represents a pharmacy or PPMV.

    Logic:
        1. Facility name matches a pharmacy pattern → candidate
        2. Exclude if name also matches a hospital/clinic pattern
           UNLESS the name contains both (e.g. "Hospital Pharmacy")
        3. Accept remaining candidates
    """
    name = (row.get("facility_name") or "").strip()
    if not name:
        return False

    if not _PHARMACY_NAME_RE.search(name):
        return False

    # Check for hospital/clinic false positives
    # But allow "Hospital Pharmacy" or "Clinic Pharmacy"
    has_pharma = bool(re.search(r"pharma", name, re.IGNORECASE))
    has_exclude = bool(_EXCLUDE_RE.search(name))

    if has_exclude and not has_pharma:
        return False

    # "Pharmaco Hospital" — pharma is in the hospital name, not a pharmacy
    if has_exclude and has_pharma:
        # Accept only if "pharmacy" appears as a separate word
        if re.search(r"\bpharmacy\b", name, re.IGNORECASE):
            return True
        return False

    return True


def classify_facility_type(name: str) -> str:
    """
    Classify a facility into pharmacy/ppmv/hospital_pharmacy based on name.
    """
    name_lower = name.lower()
    if "hospital" in name_lower and "pharma" in name_lower:
        return "hospital_pharmacy"
    if any(term in name_lower for term in ["ppmv", "patent medicine", "drug store", "med store", "chemist"]):
        return "ppmv"
    return "pharmacy"


# ---------------------------------------------------------------------------
# GRID3 → Generic format mapping
# ---------------------------------------------------------------------------

# GRID3 state names may differ slightly from our canonical list.
# This handles known mismatches.
_STATE_MAP = {
    "Fct": "FCT",
    "FCT": "FCT",
    "Nassarawa": "Nasarawa",
}


def map_to_generic(row: dict) -> dict:
    """Convert a GRID3 row to the generic_pharmacy_import format."""
    name = (row.get("facility_name") or "").strip()
    state = (row.get("state") or "").strip()
    state = _STATE_MAP.get(state, state)

    lat = row.get("latitude")
    lon = row.get("longitude")
    try:
        lat = float(lat) if lat else None
    except (ValueError, TypeError):
        lat = None
    try:
        lon = float(lon) if lon else None
    except (ValueError, TypeError):
        lon = None

    return {
        "source_record_id": row.get("globalid", ""),
        "facility_name": name,
        "facility_type": classify_facility_type(name),
        "address_line": None,
        "ward": (row.get("ward") or "").strip() or None,
        "lga": (row.get("lga") or "").strip() or None,
        "state": state,
        "latitude": lat,
        "longitude": lon,
        "phone": None,
        "email": None,
        "contact_person": None,
        "registration_number": None,
        "operational_status": "unknown",
        "ownership": (row.get("ownership") or "unknown").strip().lower(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

GENERIC_FIELDS = [
    "source_record_id", "facility_name", "facility_type", "address_line",
    "ward", "lga", "state", "latitude", "longitude", "phone", "email",
    "contact_person", "registration_number", "operational_status", "ownership",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter GRID3 health facilities to pharmacy/PPMV records",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the GRID3 CSV file",
    )
    parser.add_argument(
        "--output",
        default="output/grid3/grid3_pharmacies.csv",
        help="Output CSV path (default: output/grid3/grid3_pharmacies.csv)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Read and filter
    total = 0
    matched = 0
    excluded = 0
    pharmacy_records = []

    with open(input_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if is_pharmacy_record(row):
                generic = map_to_generic(row)
                pharmacy_records.append(generic)
                matched += 1

    # Write output CSV
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=GENERIC_FIELDS)
        writer.writeheader()
        writer.writerows(pharmacy_records)

    # Summary
    logger.info("=" * 60)
    logger.info("GRID3 FILTER SUMMARY")
    logger.info("  Input          : %s", input_path)
    logger.info("  Total records  : %d", total)
    logger.info("  Pharmacy/PPMV  : %d", matched)
    logger.info("  Filtered out   : %d", total - matched)
    logger.info("  Output         : %s", output_path)
    logger.info("=" * 60)

    # Breakdown by state
    from collections import Counter
    states = Counter(r["state"] for r in pharmacy_records)
    logger.info("  By state:")
    for state, count in states.most_common():
        logger.info("    %s: %d", state, count)

    logger.info("")
    logger.info(
        "Next: python agent-02-data-acquisition/scripts/ingest_template.py "
        "--source-file %s "
        "--template agent-02-data-acquisition/templates/generic_pharmacy_import.json "
        "--source-id src-grid3-health "
        "--output-dir output/grid3/",
        output_path,
    )


if __name__ == "__main__":
    main()
