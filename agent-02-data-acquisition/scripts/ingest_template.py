#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Generic Ingestion Pipeline

Reads a source data file, validates records against an ingestion template
(JSON Schema), assigns L0 validation status, writes provenance records,
and outputs canonical format for loading into the registry database.

Usage:
    python ingest_template.py \
        --source-file data.csv \
        --template templates/generic_pharmacy_import.json \
        --source-id src-grid3-health \
        --actor "pipeline:ingest_template" \
        --output-dir output/

Dependencies:
    pip install jsonschema pydantic
"""

import argparse
import csv
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:
    jsonschema = None
    print(
        "WARNING: jsonschema not installed. "
        "Install with: pip install jsonschema",
        file=sys.stderr,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALIDATION_LEVEL_L0 = "L0_mapped"
VALIDATION_LABEL_L0 = "Mapped"

NIGERIAN_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
    "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu",
    "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi",
    "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo",
    "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe",
    "Zamfara", "FCT",
]

FACILITY_TYPE_MAP = {
    # Source-specific values → canonical enum
    "pharmacy": "pharmacy",
    "community_pharmacy": "pharmacy",
    "hospital_pharmacy": "hospital_pharmacy",
    "ppmv": "ppmv",
    "patent medicine store": "ppmv",
    "patent medicine vendor": "ppmv",
}

ALLOWED_FACILITY_TYPES = {"pharmacy", "hospital_pharmacy", "ppmv"}
ALLOWED_OPERATIONAL_STATUSES = {
    "operational",
    "temporarily_closed",
    "permanently_closed",
    "unknown",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core classes
# ---------------------------------------------------------------------------


class ProvenanceRecord:
    """Represents a provenance entry for an ingested record."""

    def __init__(
        self,
        entity_id: str,
        entity_type: str,
        action: str,
        actor: str,
        source_system: str,
        source_dataset: str,
        detail: dict[str, Any] | None = None,
    ):
        self.provenance_id = str(uuid.uuid4())
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.action = action
        self.actor = actor
        self.source_system = source_system
        self.source_dataset = source_dataset
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "action": self.action,
            "actor": self.actor,
            "source_system": self.source_system,
            "source_dataset": self.source_dataset,
            "timestamp": self.timestamp,
            "detail": self.detail,
        }


class ValidationStatusEntry:
    """Represents an append-only validation status history entry."""

    def __init__(
        self,
        pharmacy_id: str,
        new_level: str,
        new_label: str,
        changed_by: str,
        source_description: str,
    ):
        self.status_id = str(uuid.uuid4())
        self.pharmacy_id = pharmacy_id
        self.old_level = None  # First entry — no prior level
        self.new_level = new_level
        self.new_label = new_label
        self.changed_at = datetime.now(timezone.utc).isoformat()
        self.changed_by = changed_by
        self.evidence_reference = None
        self.source_description = source_description

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_id": self.status_id,
            "pharmacy_id": self.pharmacy_id,
            "old_level": self.old_level,
            "new_level": self.new_level,
            "new_label": self.new_label,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
            "evidence_reference": self.evidence_reference,
            "source_description": self.source_description,
        }


class CanonicalRecord:
    """A pharmacy/PPMV record in canonical registry format."""

    def __init__(self, source_record: dict[str, Any], source_id: str):
        self.pharmacy_id = str(uuid.uuid4())
        self.source_record_id = source_record.get("source_record_id")
        self.facility_name = source_record["facility_name"]
        self.facility_type = source_record["facility_type"]
        self.address_line = source_record.get("address_line")
        self.ward = source_record.get("ward")
        self.lga = source_record.get("lga")
        self.state = source_record.get("state")
        self.latitude = source_record.get("latitude")
        self.longitude = source_record.get("longitude")
        self.phone = source_record.get("phone")
        self.email = source_record.get("email")
        self.contact_person = source_record.get("contact_person")
        self.operational_status = source_record.get("operational_status", "unknown")
        self.ownership = source_record.get("ownership", "unknown")
        self.validation_level = VALIDATION_LEVEL_L0
        self.validation_label = VALIDATION_LABEL_L0
        self.source_id = source_id
        now = datetime.now(timezone.utc).isoformat()
        self.created_at = now
        self.updated_at = now

        # External identifiers (populated if available)
        self.external_ids = {}
        if source_record.get("registration_number"):
            self.external_ids["pcn_registration"] = source_record["registration_number"]
        if source_record.get("nhia_code"):
            self.external_ids["nhia_facility"] = source_record["nhia_code"]
        if source_record.get("nafdac_license"):
            self.external_ids["nafdac_license"] = source_record["nafdac_license"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pharmacy_id": self.pharmacy_id,
            "source_record_id": self.source_record_id,
            "facility_name": self.facility_name,
            "facility_type": self.facility_type,
            "address_line": self.address_line,
            "ward": self.ward,
            "lga": self.lga,
            "state": self.state,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "phone": self.phone,
            "email": self.email,
            "contact_person": self.contact_person,
            "operational_status": self.operational_status,
            "ownership": self.ownership,
            "validation_level": self.validation_level,
            "validation_label": self.validation_label,
            "source_id": self.source_id,
            "external_identifiers": self.external_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------


def load_template(template_path: str) -> dict[str, Any]:
    """Load and return a JSON Schema ingestion template."""
    with open(template_path, "r") as f:
        return json.load(f)


def load_source_registry(registry_path: str) -> dict[str, Any]:
    """Load source_registry.json and return a lookup dict keyed by source_id."""
    with open(registry_path, "r") as f:
        registry = json.load(f)
    return {s["source_id"]: s for s in registry.get("sources", [])}


def read_csv_source(file_path: str) -> list[dict[str, Any]]:
    """Read a CSV source file and return a list of row dicts."""
    records = []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert empty strings to None for cleaner downstream handling
            cleaned = {k: (v.strip() if v and v.strip() else None) for k, v in row.items()}
            records.append(cleaned)
    logger.info("Read %d records from %s", len(records), file_path)
    return records


def read_json_source(file_path: str) -> list[dict[str, Any]]:
    """Read a JSON source file and return the records array."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Support both {"records": [...]} and bare [...]
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("records", [])
    else:
        raise ValueError(f"Unexpected JSON structure in {file_path}")
    logger.info("Read %d records from %s", len(records), file_path)
    return records


def read_source_file(file_path: str) -> list[dict[str, Any]]:
    """Dispatch to the appropriate reader based on file extension."""
    ext = Path(file_path).suffix.lower()
    if ext == ".csv":
        return read_csv_source(file_path)
    elif ext in (".json", ".geojson"):
        return read_json_source(file_path)
    else:
        raise ValueError(
            f"Unsupported file format: {ext}. Supported: .csv, .json, .geojson"
        )


def validate_record(record: dict[str, Any], template: dict[str, Any]) -> list[str]:
    """
    Validate a single record against the ingestion template's record schema.
    Returns a list of validation error messages (empty if valid).
    """
    errors = []

    if jsonschema is not None:
        # Extract the per-record schema from the template
        record_schema = (
            template.get("properties", {})
            .get("records", {})
            .get("items", {})
        )
        if record_schema:
            validator = jsonschema.Draft202012Validator(record_schema)
            for error in validator.iter_errors(record):
                errors.append(f"{error.json_path}: {error.message}")
    else:
        # Fallback: minimal validation without jsonschema library
        if not record.get("facility_name") and not record.get("premises_name") and not record.get("name"):
            errors.append("Missing required field: facility_name")
        if not record.get("state") and not record.get("addr_state"):
            errors.append("Missing required field: state")

    # Custom validation: state name check
    state = record.get("state")
    if state and state not in NIGERIAN_STATES:
        errors.append(f"Unrecognized state: '{state}'. Must be a valid Nigerian state or 'FCT'.")

    # Custom validation: canonical enum safety
    facility_type = record.get("facility_type")
    if facility_type not in ALLOWED_FACILITY_TYPES:
        errors.append(
            "Unsupported facility_type: "
            f"'{facility_type}'. Allowed values: {sorted(ALLOWED_FACILITY_TYPES)}"
        )

    operational_status = record.get("operational_status", "unknown")
    if operational_status not in ALLOWED_OPERATIONAL_STATUSES:
        errors.append(
            "Unsupported operational_status: "
            f"'{operational_status}'. Allowed values: {sorted(ALLOWED_OPERATIONAL_STATUSES)}"
        )

    # Custom validation: coordinate bounds (Nigeria bounding box)
    lat = record.get("latitude")
    lon = record.get("longitude")
    if lat is not None:
        try:
            lat = float(lat)
            if not (3.0 <= lat <= 14.0):
                errors.append(f"Latitude {lat} is outside Nigeria bounds (3.0–14.0)")
        except (ValueError, TypeError):
            errors.append(f"Invalid latitude value: {lat}")
    if lon is not None:
        try:
            lon = float(lon)
            if not (2.0 <= lon <= 15.0):
                errors.append(f"Longitude {lon} is outside Nigeria bounds (2.0–15.0)")
        except (ValueError, TypeError):
            errors.append(f"Invalid longitude value: {lon}")

    return errors


def classify_validation_error(error_message: str) -> str:
    """
    Convert free-text validation errors into stable machine-readable codes.
    """
    if "Missing required field" in error_message:
        return "missing_required_field"
    if "Unsupported facility_type" in error_message:
        return "invalid_facility_type"
    if "Unsupported operational_status" in error_message:
        return "invalid_operational_status"
    if "Unrecognized state" in error_message:
        return "invalid_state"
    if "Invalid latitude value" in error_message:
        return "invalid_latitude_type"
    if "Invalid longitude value" in error_message:
        return "invalid_longitude_type"
    if "outside Nigeria bounds" in error_message:
        return "coordinate_out_of_bounds"
    if error_message.startswith("$"):
        return "json_schema_validation_error"
    return "unknown_validation_error"


def normalize_to_generic(record: dict[str, Any], source_id: str) -> dict[str, Any]:
    """
    Normalize a source-specific record into the generic import format.
    Handles field name differences across source types (PCN, OSM, etc.).
    """
    normalized = dict(record)

    # Normalize free-text enums into canonical DB-safe values before validation.
    raw_facility_type = normalized.get("facility_type")
    if isinstance(raw_facility_type, str):
        normalized["facility_type"] = FACILITY_TYPE_MAP.get(
            raw_facility_type.lower().strip(),
            raw_facility_type.lower().strip(),
        )

    raw_operational_status = normalized.get("operational_status")
    if isinstance(raw_operational_status, str):
        normalized["operational_status"] = raw_operational_status.lower().strip()

    # PCN-specific normalization
    if source_id == "src-pcn-premises":
        if "premises_name" in record and "facility_name" not in record:
            normalized["facility_name"] = record["premises_name"]
        if "facility_category" in record and "facility_type" not in record:
            cat = record["facility_category"]
            if isinstance(cat, str):
                normalized["facility_type"] = FACILITY_TYPE_MAP.get(
                    cat.lower().strip(),
                    cat.lower().strip(),
                )
        if "registration_number" in record:
            normalized["registration_number"] = record["registration_number"]

    # OSM-specific normalization
    elif source_id == "src-osm-pharmacy":
        if "name" in record and "facility_name" not in record:
            normalized["facility_name"] = record.get("name") or "Unnamed Pharmacy"
        if "addr_state" in record and "state" not in record:
            normalized["state"] = record["addr_state"]
        if "addr_street" in record and "address_line" not in record:
            parts = [record.get("addr_street"), record.get("addr_city")]
            normalized["address_line"] = ", ".join(p for p in parts if p)
        normalized["facility_type"] = "pharmacy"  # OSM default
        if "osm_id" in record:
            normalized["source_record_id"] = f"osm:{record['osm_type']}:{record['osm_id']}"

    # Google Places-specific normalization
    elif source_id == "src-google-places":
        if "name" in record and "facility_name" not in record:
            normalized["facility_name"] = record.get("name") or "Unnamed Pharmacy"
        if "formatted_address" in record and "address_line" not in record:
            normalized["address_line"] = record.get("short_address") or record.get("formatted_address")
        normalized["facility_type"] = "pharmacy"  # Google Places search is filtered to pharmacy type
        if "google_place_id" in record:
            normalized["source_record_id"] = f"google:{record['google_place_id']}"

    # Ensure numeric types for coordinates
    for coord_field in ("latitude", "longitude"):
        val = normalized.get(coord_field)
        if val is not None:
            try:
                normalized[coord_field] = float(val)
            except (ValueError, TypeError):
                normalized[coord_field] = None

    return normalized


def process_batch(
    records: list[dict[str, Any]],
    template: dict[str, Any],
    source_id: str,
    actor: str,
) -> dict[str, Any]:
    """
    Process a batch of source records through the ingestion pipeline.

    Returns a dict with:
        - canonical_records: list of CanonicalRecord dicts
        - provenance_records: list of ProvenanceRecord dicts
        - status_entries: list of ValidationStatusEntry dicts
        - rejected_records: list of (record, errors) tuples
        - stats: summary statistics
    """
    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    canonical_records = []
    provenance_records = []
    status_entries = []
    rejected_records = []
    rejection_reason_counts: dict[str, int] = {}

    for i, raw_record in enumerate(records):
        # Step 1: Normalize to generic format
        normalized = normalize_to_generic(raw_record, source_id)

        # Step 2: Validate against template
        errors = validate_record(normalized, template)
        if errors:
            error_codes = [classify_validation_error(e) for e in errors]
            for code in error_codes:
                rejection_reason_counts[code] = rejection_reason_counts.get(code, 0) + 1

            rejected_records.append({
                "record_index": i,
                "source_record": raw_record,
                "errors": errors,
                "error_codes": error_codes,
            })
            logger.warning(
                "Record %d rejected: %s", i, "; ".join(errors)
            )
            continue

        # Step 3: Create canonical record with L0 status
        canonical = CanonicalRecord(normalized, source_id)

        # Step 4: Create provenance record
        provenance = ProvenanceRecord(
            entity_id=canonical.pharmacy_id,
            entity_type="pharmacy_location",
            action="ingested",
            actor=actor,
            source_system=source_id,
            source_dataset=batch_id,
            detail={
                "batch_id": batch_id,
                "record_index": i,
                "source_record_id": canonical.source_record_id,
                "ingestion_timestamp": now,
            },
        )

        # Step 5: Create initial validation status entry (L0)
        status = ValidationStatusEntry(
            pharmacy_id=canonical.pharmacy_id,
            new_level=VALIDATION_LEVEL_L0,
            new_label=VALIDATION_LABEL_L0,
            changed_by=actor,
            source_description=f"Ingested from {source_id} batch {batch_id}",
        )

        canonical_records.append(canonical.to_dict())
        provenance_records.append(provenance.to_dict())
        status_entries.append(status.to_dict())

    stats = {
        "batch_id": batch_id,
        "source_id": source_id,
        "total_input": len(records),
        "accepted": len(canonical_records),
        "rejected": len(rejected_records),
        "acceptance_rate": (
            round((len(canonical_records) / len(records)) * 100, 2)
            if records
            else 0.0
        ),
        "rejection_rate": (
            round((len(rejected_records) / len(records)) * 100, 2)
            if records
            else 0.0
        ),
        "rejection_reason_counts": rejection_reason_counts,
        "ingestion_timestamp": now,
        "actor": actor,
    }

    logger.info(
        "Batch %s complete: %d accepted, %d rejected out of %d total",
        batch_id,
        stats["accepted"],
        stats["rejected"],
        stats["total_input"],
    )

    return {
        "canonical_records": canonical_records,
        "provenance_records": provenance_records,
        "status_entries": status_entries,
        "rejected_records": rejected_records,
        "stats": stats,
    }


def write_output(results: dict[str, Any], output_dir: str) -> None:
    """Write pipeline output to JSON files in the output directory."""
    os.makedirs(output_dir, exist_ok=True)
    batch_id = results["stats"]["batch_id"]

    files = {
        f"canonical_{batch_id}.json": results["canonical_records"],
        f"provenance_{batch_id}.json": results["provenance_records"],
        f"status_history_{batch_id}.json": results["status_entries"],
        f"rejected_{batch_id}.json": results["rejected_records"],
        f"stats_{batch_id}.json": results["stats"],
    }

    for filename, data in files.items():
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Wrote %s", filepath)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nigeria Pharmacy Registry — Ingestion Pipeline",
    )
    parser.add_argument(
        "--source-file",
        required=True,
        help="Path to source data file (CSV or JSON)",
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Path to ingestion template JSON Schema",
    )
    parser.add_argument(
        "--source-id",
        required=True,
        help="Source identifier (must match source_registry.json)",
    )
    parser.add_argument(
        "--actor",
        default="pipeline:ingest_template",
        help="Actor identifier for provenance (default: pipeline:ingest_template)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for output files (default: output/)",
    )
    parser.add_argument(
        "--source-registry",
        default=None,
        help="Path to source_registry.json (optional, for source validation)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("Starting ingestion pipeline")
    logger.info("  Source file: %s", args.source_file)
    logger.info("  Template:    %s", args.template)
    logger.info("  Source ID:   %s", args.source_id)
    logger.info("  Actor:       %s", args.actor)
    logger.info("  Output dir:  %s", args.output_dir)

    # Validate source_id against registry if provided
    if args.source_registry:
        registry = load_source_registry(args.source_registry)
        if args.source_id not in registry:
            logger.error(
                "Source ID '%s' not found in registry. Known sources: %s",
                args.source_id,
                list(registry.keys()),
            )
            sys.exit(1)
        logger.info("Source ID validated against registry")

    # Load template
    template = load_template(args.template)
    logger.info("Loaded template: %s", template.get("title", "unknown"))

    # Read source data
    records = read_source_file(args.source_file)
    if not records:
        logger.warning("No records found in source file. Exiting.")
        sys.exit(0)

    # Process batch
    results = process_batch(records, template, args.source_id, args.actor)

    # Write output
    write_output(results, args.output_dir)

    # Summary
    stats = results["stats"]
    logger.info("=" * 60)
    logger.info("INGESTION SUMMARY")
    logger.info("  Batch ID:    %s", stats["batch_id"])
    logger.info("  Total input: %d", stats["total_input"])
    logger.info("  Accepted:    %d (assigned L0 — Mapped)", stats["accepted"])
    logger.info("  Rejected:    %d", stats["rejected"])
    logger.info("=" * 60)

    if stats["rejected"] > 0:
        logger.warning(
            "Review rejected records in: %s/rejected_%s.json",
            args.output_dir,
            stats["batch_id"],
        )


if __name__ == "__main__":
    main()
