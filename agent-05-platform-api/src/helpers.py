"""Shared helpers, constants, and JSON fallback state for the Nigeria Pharmacy Registry API."""

from __future__ import annotations

import glob
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import db
from .db import extras

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"

# ---------------------------------------------------------------------------
# JSON fallback state (populated by load_all_canonical)
# ---------------------------------------------------------------------------

_RECORDS: list[dict[str, Any]] = []
_INDEX: dict[str, dict[str, Any]] = {}


def load_all_canonical() -> None:
    """
    Load canonical pharmacy records from JSON files.

    Prefers the deduped registry (output/deduped/canonical_deduped_*.json)
    when available. Falls back to loading all raw canonical_*.json files
    from the full output tree if no deduped file exists.
    """
    global _RECORDS, _INDEX  # noqa: PLW0603

    records: list[dict] = []

    # Prefer deduped registry
    deduped_pattern = str(OUTPUT_DIR / "deduped" / "canonical_deduped_*.json")
    deduped_files = sorted(glob.glob(deduped_pattern))

    if deduped_files:
        fpath = deduped_files[-1]
        with open(fpath, "r", encoding="utf-8") as f:
            records = json.load(f)
        logger.info("Loaded %d records from deduped registry: %s", len(records), fpath)
    else:
        pattern = str(OUTPUT_DIR / "**" / "canonical_*.json")
        files = glob.glob(pattern, recursive=True)
        for fpath in files:
            with open(fpath, "r", encoding="utf-8") as f:
                batch = json.load(f)
            if isinstance(batch, list):
                records.extend(batch)
            logger.info("Loaded %d records from %s", len(batch) if isinstance(batch, list) else 0, fpath)

    # Deduplicate by pharmacy_id (safety net)
    seen: set[str] = set()
    unique: list[dict] = []
    for r in records:
        pid = r.get("pharmacy_id")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(r)

    _RECORDS = unique
    _INDEX = {r["pharmacy_id"]: r for r in _RECORDS}
    logger.info("Total unique JSON records loaded: %d", len(_RECORDS))


def get_records() -> list[dict[str, Any]]:
    """Access the JSON fallback records list."""
    return _RECORDS


def get_index() -> dict[str, dict[str, Any]]:
    """Access the JSON fallback index dict."""
    return _INDEX


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

VALIDATION_LEVELS = [
    "L0_mapped",
    "L1_contact_confirmed",
    "L2_evidence_documented",
    "L3_regulator_verified",
    "L4_high_assurance",
]
LEVEL_INDEX = {lvl: i for i, lvl in enumerate(VALIDATION_LEVELS)}

REQUIRED_EVIDENCE = {
    "L1_contact_confirmed": "contact_confirmation",
    "L2_evidence_documented": "location_confirmation",
    "L3_regulator_verified": "regulator_crossref",
    "L4_high_assurance": "in_person_audit",
}

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def level_label(level: str | None) -> str:
    """Human-readable label for a validation level."""
    labels = {
        "L0_mapped": "Mapped",
        "L1_contact_confirmed": "Contact Confirmed",
        "L2_evidence_documented": "Evidence Documented",
        "L3_regulator_verified": "Regulator Verified",
        "L4_high_assurance": "High Assurance",
    }
    return labels.get(level or "", level or "Unknown")


def iso(dt) -> str | None:
    """Convert a datetime to ISO string."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def parse_date_param(value: str | None, param_name: str) -> str | None:
    """Validate an ISO 8601 date string.  Returns the value if valid, raises 400 if not."""
    if value is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format for '{param_name}'. Expected ISO 8601 (e.g. 2026-02-20T00:00:00Z)",
        )


# ---------------------------------------------------------------------------
# Database row converters
# ---------------------------------------------------------------------------


def db_row_to_pharmacy(row: dict) -> dict:
    """Convert a DB row (RealDictRow) to the API's pharmacy dict format."""
    lat = row.get("latitude")
    lon = row.get("longitude")
    return {
        "pharmacy_id": str(row["id"]),
        "facility_name": row["name"],
        "facility_type": row["facility_type"],
        "address_line": row.get("address_line_1"),
        "ward": row.get("ward"),
        "lga": row.get("lga"),
        "state": row.get("state"),
        "latitude": float(lat) if lat is not None else None,
        "longitude": float(lon) if lon is not None else None,
        "phone": row.get("phone"),
        "operational_status": row.get("operational_status"),
        "validation_level": row.get("current_validation_level"),
        "validation_label": level_label(row.get("current_validation_level")),
        "source_id": row.get("primary_source"),
        "source_record_id": row.get("primary_source_id"),
        "created_at": iso(row.get("created_at")),
        "updated_at": iso(row.get("updated_at")),
    }


# ---------------------------------------------------------------------------
# Database query helpers
# ---------------------------------------------------------------------------


def db_list_pharmacies(
    state: str | None,
    lga: str | None,
    facility_type: str | None,
    source_id: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> dict | None:
    """Query pharmacies from DB. Returns None if DB unavailable."""
    if not db.is_available():
        return None

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                conditions: list[str] = []
                params: list[Any] = []

                if state:
                    conditions.append("pl.state ILIKE %s")
                    params.append(state)
                if lga:
                    conditions.append("pl.lga ILIKE %s")
                    params.append(lga)
                if facility_type:
                    conditions.append("pl.facility_type = %s::facility_type")
                    params.append(facility_type)
                if source_id:
                    conditions.append("pl.primary_source = %s")
                    params.append(source_id)
                if q:
                    conditions.append("pl.name ILIKE %s")
                    params.append(f"%{q}%")

                where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude,
                           c.contact_value AS phone
                    FROM pharmacy_locations pl
                    LEFT JOIN contacts c
                        ON c.pharmacy_id = pl.id
                        AND c.contact_type = 'phone'
                        AND c.is_primary = true
                    {where}
                    ORDER BY pl.state, pl.name
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "meta": {"total": total, "limit": limit, "offset": offset},
            "data": [db_row_to_pharmacy(r) for r in rows],
        }
    except Exception as e:
        logger.warning("DB query failed, will fall back to JSON: %s", e)
        return None


def db_get_pharmacy(pharmacy_id: str) -> dict | None:
    """Get a single pharmacy from DB with contacts and external IDs."""
    if not db.is_available():
        return None

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude
                    FROM pharmacy_locations pl
                    WHERE pl.id = %s
                    """,
                    (pharmacy_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"data": None}

                result = db_row_to_pharmacy(row)

                cur.execute(
                    "SELECT * FROM contacts WHERE pharmacy_id = %s ORDER BY is_primary DESC",
                    (pharmacy_id,),
                )
                contacts = cur.fetchall()
                result["contacts"] = [
                    {
                        "type": c["contact_type"],
                        "value": c["contact_value"],
                        "person": c.get("contact_person"),
                        "is_primary": c["is_primary"],
                        "is_verified": c["is_verified"],
                    }
                    for c in contacts
                ]
                for c in contacts:
                    if c["contact_type"] == "phone" and c["is_primary"]:
                        result["phone"] = c["contact_value"]
                        break

                cur.execute(
                    "SELECT * FROM external_identifiers WHERE pharmacy_id = %s AND is_current = true",
                    (pharmacy_id,),
                )
                ext_ids = cur.fetchall()
                result["external_identifiers"] = {
                    e["identifier_type"]: e["identifier_value"] for e in ext_ids
                }

                return {"data": result}
    except Exception as e:
        logger.warning("DB get_pharmacy failed: %s", e)
        return None


def db_get_stats() -> dict | None:
    """Aggregate stats from DB."""
    if not db.is_available():
        return None

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT count(*) AS total FROM pharmacy_locations")
                total = cur.fetchone()["total"]

                cur.execute(
                    "SELECT state, count(*) AS cnt FROM pharmacy_locations GROUP BY state ORDER BY cnt DESC"
                )
                by_state = {r["state"]: r["cnt"] for r in cur.fetchall()}

                cur.execute(
                    "SELECT primary_source, count(*) AS cnt FROM pharmacy_locations GROUP BY primary_source ORDER BY cnt DESC"
                )
                by_source = {r["primary_source"]: r["cnt"] for r in cur.fetchall()}

                cur.execute(
                    "SELECT facility_type::text, count(*) AS cnt FROM pharmacy_locations GROUP BY facility_type ORDER BY cnt DESC"
                )
                by_type = {r["facility_type"]: r["cnt"] for r in cur.fetchall()}

                cur.execute(
                    "SELECT current_validation_level::text, count(*) AS cnt FROM pharmacy_locations GROUP BY current_validation_level ORDER BY cnt DESC"
                )
                by_level = {r["current_validation_level"]: r["cnt"] for r in cur.fetchall()}

        return {
            "total": total,
            "by_state": by_state,
            "by_source": by_source,
            "by_facility_type": by_type,
            "by_validation_level": by_level,
            "states_covered": len(by_state),
        }
    except Exception as e:
        logger.warning("DB stats failed: %s", e)
        return None


def db_get_geojson(
    state: str | None,
    source_id: str | None,
    facility_type: str | None,
) -> dict | None:
    """GeoJSON FeatureCollection from DB."""
    if not db.is_available():
        return None

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                conditions = ["pl.geolocation IS NOT NULL"]
                params: list[Any] = []

                if state:
                    conditions.append("pl.state ILIKE %s")
                    params.append(state)
                if source_id:
                    conditions.append("pl.primary_source = %s")
                    params.append(source_id)
                if facility_type:
                    conditions.append("pl.facility_type = %s::facility_type")
                    params.append(facility_type)

                where = " WHERE " + " AND ".join(conditions)

                cur.execute(
                    f"""
                    SELECT pl.id, pl.name, pl.facility_type::text,
                           pl.state, pl.lga, pl.primary_source,
                           pl.current_validation_level::text,
                           pl.operational_status::text,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude,
                           c.contact_value AS phone,
                           pl.address_line_1
                    FROM pharmacy_locations pl
                    LEFT JOIN contacts c
                        ON c.pharmacy_id = pl.id
                        AND c.contact_type = 'phone'
                        AND c.is_primary = true
                    {where}
                    """,
                    params,
                )
                rows = cur.fetchall()

        features = []
        for r in rows:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]},
                "properties": {
                    "pharmacy_id": str(r["id"]),
                    "facility_name": r["name"],
                    "facility_type": r["facility_type"],
                    "state": r["state"],
                    "lga": r["lga"],
                    "source_id": r["primary_source"],
                    "validation_label": level_label(r["current_validation_level"]),
                    "operational_status": r["operational_status"],
                    "phone": r.get("phone"),
                    "address_line": r.get("address_line_1"),
                },
            })

        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        logger.warning("DB geojson failed: %s", e)
        return None
