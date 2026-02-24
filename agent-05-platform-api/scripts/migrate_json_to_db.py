#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — JSON → PostgreSQL Migration

Loads the canonical (deduped) JSON registry into the PostgreSQL database.
Populates:
    1. pharmacy_locations   — core record with PostGIS point
    2. contacts             — phone/email (when present)
    3. external_identifiers — source-specific IDs
    4. validation_status_history — initial L0 entry
    5. provenance_records   — import action

Design:
    - Batch commits every 500 records
    - ON CONFLICT (id) DO NOTHING — safe to re-run (idempotent)
    - Actor: system:json_migration

Usage:
    python3 agent-05-platform-api/scripts/migrate_json_to_db.py
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2 import extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"

ACTOR = "system:json_migration"
ACTOR_TYPE = "system"
BATCH_SIZE = 500

# Facility-type mapping (JSON value → DB enum)
FACILITY_TYPE_MAP = {
    "pharmacy": "pharmacy",
    "ppmv": "ppmv",
    "hospital_pharmacy": "hospital_pharmacy",
}

# Operational status mapping
OP_STATUS_MAP = {
    "operational": "operational",
    "temporarily_closed": "temporarily_closed",
    "permanently_closed": "permanently_closed",
}

# Source ID → source_system shorthand
SOURCE_SYSTEM_MAP = {
    "src-osm-pharmacy": "osm",
    "src-grid3-health": "grid3",
    "src-google-places": "google_places",
}


def get_db_config() -> dict:
    return {
        "host": os.environ.get("NPR_DB_HOST", "localhost"),
        "port": int(os.environ.get("NPR_DB_PORT", "5432")),
        "dbname": os.environ.get("NPR_DB_NAME", "npr_registry"),
        "user": os.environ.get("NPR_DB_USER", "npr"),
        "password": os.environ.get("NPR_DB_PASSWORD", "npr_local_dev"),
    }


def load_json_records() -> list[dict]:
    """Load canonical JSON records (prefer deduped)."""
    deduped_pattern = str(OUTPUT_DIR / "deduped" / "canonical_deduped_*.json")
    deduped_files = sorted(glob.glob(deduped_pattern))

    if deduped_files:
        fpath = deduped_files[-1]
        with open(fpath, "r", encoding="utf-8") as f:
            records = json.load(f)
        logger.info("Loaded %d records from %s", len(records), fpath)
        return records

    # Fallback: load raw canonical files
    pattern = str(OUTPUT_DIR / "**" / "canonical_*.json")
    files = glob.glob(pattern, recursive=True)
    records = []
    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            batch = json.load(f)
        if isinstance(batch, list):
            records.extend(batch)
        logger.info("Loaded %d records from %s", len(batch) if isinstance(batch, list) else 0, fpath)

    # Deduplicate by pharmacy_id
    seen = set()
    unique = []
    for r in records:
        pid = r.get("pharmacy_id")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(r)
    return unique


def migrate(conn, records: list[dict]) -> dict:
    """Insert records into PostgreSQL. Returns counters."""
    stats = {
        "pharmacies_inserted": 0,
        "pharmacies_skipped": 0,
        "contacts_inserted": 0,
        "identifiers_inserted": 0,
        "history_inserted": 0,
        "provenance_inserted": 0,
    }

    total = len(records)
    batch_start = 0

    while batch_start < total:
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = records[batch_start:batch_end]

        with conn.cursor() as cur:
            for rec in batch:
                pharmacy_id = rec["pharmacy_id"]
                facility_type = FACILITY_TYPE_MAP.get(
                    rec.get("facility_type", ""), "pharmacy"
                )
                op_status = OP_STATUS_MAP.get(
                    rec.get("operational_status", ""), "unknown"
                )
                lat = rec.get("latitude")
                lon = rec.get("longitude")
                source_id = rec.get("source_id", "")
                source_system = SOURCE_SYSTEM_MAP.get(source_id, source_id)

                # 1. Insert pharmacy_locations
                cur.execute(
                    """
                    INSERT INTO pharmacy_locations (
                        id, name, facility_type, operational_status,
                        address_line_1, ward, lga, state, country,
                        current_validation_level, geolocation,
                        primary_source, primary_source_id,
                        created_at, updated_at, created_by, updated_by
                    ) VALUES (
                        %s, %s, %s::facility_type, %s::operational_status,
                        %s, %s, %s, %s, 'NG',
                        'L0_mapped'::validation_level,
                        CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                             THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                             ELSE NULL END,
                        %s, %s,
                        COALESCE(%s::timestamptz, now()),
                        COALESCE(%s::timestamptz, now()),
                        %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        pharmacy_id,
                        rec.get("facility_name") or "Unnamed",
                        facility_type,
                        op_status,
                        rec.get("address_line"),
                        rec.get("ward"),
                        rec.get("lga") or "Unknown",
                        rec.get("state") or "Unknown",
                        lon, lat,   # for the CASE check
                        lon, lat,   # for ST_MakePoint(lon, lat)
                        source_id,
                        rec.get("source_record_id"),
                        rec.get("created_at"),
                        rec.get("updated_at"),
                        ACTOR,
                        ACTOR,
                    ),
                )
                if cur.rowcount > 0:
                    stats["pharmacies_inserted"] += 1
                else:
                    stats["pharmacies_skipped"] += 1
                    continue  # Already exists — skip dependent inserts

                # 2. Insert contacts (phone, email)
                for contact_type, field in [("phone", "phone"), ("email", "email")]:
                    value = rec.get(field)
                    if value:
                        cur.execute(
                            """
                            INSERT INTO contacts (
                                pharmacy_id, contact_type, contact_value,
                                contact_person, is_primary, is_verified,
                                created_by, updated_by
                            ) VALUES (%s, %s, %s, %s, true, false, %s, %s)
                            """,
                            (
                                pharmacy_id,
                                contact_type,
                                value,
                                rec.get("contact_person"),
                                ACTOR,
                                ACTOR,
                            ),
                        )
                        stats["contacts_inserted"] += 1

                # 3. Insert external identifiers
                ext_ids = rec.get("external_identifiers") or {}
                source_record_id = rec.get("source_record_id")
                if source_record_id:
                    # The main source record ID
                    id_type = {
                        "src-osm-pharmacy": "osm_node_id",
                        "src-grid3-health": "grid3_facility_id",
                        "src-google-places": "google_place_id",
                    }.get(source_id, "source_record_id")

                    cur.execute(
                        """
                        INSERT INTO external_identifiers (
                            pharmacy_id, identifier_type, identifier_value,
                            issuing_authority, is_current,
                            created_by, updated_by
                        ) VALUES (%s, %s, %s, %s, true, %s, %s)
                        ON CONFLICT (pharmacy_id, identifier_type, identifier_value)
                        DO NOTHING
                        """,
                        (
                            pharmacy_id,
                            id_type,
                            source_record_id,
                            source_system,
                            ACTOR,
                            ACTOR,
                        ),
                    )
                    if cur.rowcount > 0:
                        stats["identifiers_inserted"] += 1

                # Also insert any extra external identifiers from the merged record
                for id_type, id_value in ext_ids.items():
                    if id_value:
                        cur.execute(
                            """
                            INSERT INTO external_identifiers (
                                pharmacy_id, identifier_type, identifier_value,
                                issuing_authority, is_current,
                                created_by, updated_by
                            ) VALUES (%s, %s, %s, %s, true, %s, %s)
                            ON CONFLICT (pharmacy_id, identifier_type, identifier_value)
                            DO NOTHING
                            """,
                            (
                                pharmacy_id,
                                id_type,
                                str(id_value),
                                source_system,
                                ACTOR,
                                ACTOR,
                            ),
                        )
                        if cur.rowcount > 0:
                            stats["identifiers_inserted"] += 1

                # 4. Insert initial validation_status_history (L0)
                cur.execute(
                    """
                    INSERT INTO validation_status_history (
                        pharmacy_id, old_level, new_level,
                        changed_by, actor_type,
                        source_description, evidence_detail,
                        created_by, updated_by
                    ) VALUES (
                        %s, NULL, 'L0_mapped'::validation_level,
                        %s, %s,
                        %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        pharmacy_id,
                        ACTOR,
                        ACTOR_TYPE,
                        f"Initial import from {source_system}",
                        json.dumps({
                            "source_id": source_id,
                            "source_record_id": source_record_id,
                            "import_method": "json_migration",
                        }),
                        ACTOR,
                        ACTOR,
                    ),
                )
                stats["history_inserted"] += 1

                # 5. Log provenance
                cur.execute(
                    """
                    SELECT log_provenance(
                        'pharmacy_location', %s::uuid, 'import',
                        %s, %s, %s, 'json_migration', %s,
                        %s::jsonb
                    )
                    """,
                    (
                        pharmacy_id,
                        ACTOR,
                        ACTOR_TYPE,
                        source_system,
                        source_record_id,
                        json.dumps({
                            "action": "initial_import",
                            "source_file": "canonical_deduped",
                            "facility_name": rec.get("facility_name"),
                        }),
                    ),
                )
                stats["provenance_inserted"] += 1

        conn.commit()
        logger.info(
            "Batch %d–%d committed (%d/%d)",
            batch_start + 1,
            batch_end,
            batch_end,
            total,
        )
        batch_start = batch_end

    return stats


def main():
    logger.info("=" * 60)
    logger.info("Nigeria Pharmacy Registry — JSON → PostgreSQL Migration")
    logger.info("=" * 60)

    # Load JSON
    records = load_json_records()
    if not records:
        logger.error("No records found to migrate!")
        sys.exit(1)
    logger.info("Loaded %d records to migrate", len(records))

    # Connect
    config = get_db_config()
    logger.info(
        "Connecting to %s@%s:%s/%s",
        config["user"],
        config["host"],
        config["port"],
        config["dbname"],
    )
    conn = psycopg2.connect(**config)
    conn.autocommit = False

    try:
        # Quick check: can we reach the tables?
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pharmacy_locations")
            existing = cur.fetchone()[0]
        logger.info("Existing records in pharmacy_locations: %d", existing)

        t0 = time.time()
        stats = migrate(conn, records)
        elapsed = time.time() - t0

        logger.info("=" * 60)
        logger.info("Migration complete in %.1f seconds", elapsed)
        for key, val in stats.items():
            logger.info("  %-25s %d", key, val)

        # Final count
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pharmacy_locations")
            total = cur.fetchone()[0]
        logger.info("Total pharmacy_locations: %d", total)
        logger.info("=" * 60)

    except Exception:
        conn.rollback()
        logger.exception("Migration failed — rolled back")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
