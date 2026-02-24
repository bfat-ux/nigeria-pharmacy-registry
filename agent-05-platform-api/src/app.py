#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Dashboard API

Dual-mode FastAPI server:
  • Database mode — reads from PostgreSQL when available (enables writes)
  • JSON fallback — reads from canonical JSON files when DB is unavailable

Usage:
    uvicorn agent-05-platform-api.src.app:app --reload --port 8000
"""

from __future__ import annotations

import glob
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .db import extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON data loading (fallback mode)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"

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


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

# Validation level ordering (for transition rules)
VALIDATION_LEVELS = [
    "L0_mapped",
    "L1_contact_confirmed",
    "L2_evidence_documented",
    "L3_regulator_verified",
    "L4_high_assurance",
]
_LEVEL_INDEX = {lvl: i for i, lvl in enumerate(VALIDATION_LEVELS)}

# Required evidence type per target level
REQUIRED_EVIDENCE = {
    "L1_contact_confirmed": "contact_confirmation",
    "L2_evidence_documented": "location_confirmation",
    "L3_regulator_verified": "regulator_crossref",
    "L4_high_assurance": "in_person_audit",
}


def _db_row_to_pharmacy(row: dict) -> dict:
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
        "validation_label": _level_label(row.get("current_validation_level")),
        "source_id": row.get("primary_source"),
        "source_record_id": row.get("primary_source_id"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _level_label(level: str | None) -> str:
    """Human-readable label for a validation level."""
    labels = {
        "L0_mapped": "Mapped",
        "L1_contact_confirmed": "Contact Confirmed",
        "L2_evidence_documented": "Evidence Documented",
        "L3_regulator_verified": "Regulator Verified",
        "L4_high_assurance": "High Assurance",
    }
    return labels.get(level or "", level or "Unknown")


def _iso(dt) -> str | None:
    """Convert a datetime to ISO string."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _db_list_pharmacies(
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
                # Build WHERE clauses
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

                # Count query
                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                # Data query
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
            "data": [_db_row_to_pharmacy(r) for r in rows],
        }
    except Exception as e:
        logger.warning("DB query failed, will fall back to JSON: %s", e)
        return None


def _db_get_pharmacy(pharmacy_id: str) -> dict | None:
    """Get a single pharmacy from DB with contacts and external IDs."""
    if not db.is_available():
        return None

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Main record
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

                result = _db_row_to_pharmacy(row)

                # Contacts
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
                # Set top-level phone from primary contact
                for c in contacts:
                    if c["contact_type"] == "phone" and c["is_primary"]:
                        result["phone"] = c["contact_value"]
                        break

                # External identifiers
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


def _db_get_stats() -> dict | None:
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


def _db_get_geojson(
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
                    "validation_label": _level_label(r["current_validation_level"]),
                    "operational_status": r["operational_status"],
                    "phone": r.get("phone"),
                    "address_line": r.get("address_line_1"),
                },
            })

        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        logger.warning("DB geojson failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Pydantic models for verification
# ---------------------------------------------------------------------------


class VerifyRequest(BaseModel):
    target_level: str = Field(
        ...,
        description="Target validation level (e.g. L1_contact_confirmed)",
    )
    evidence_type: str = Field(
        ...,
        description="Type of evidence (contact_confirmation, location_confirmation, regulator_crossref)",
    )
    capture_method: str | None = Field(
        None,
        description="How evidence was captured (phone_call, site_visit, api_sync, etc.)",
    )
    actor_id: str = Field(
        ...,
        description="ID of the person/system performing verification",
    )
    actor_type: str = Field(
        "field_agent",
        description="Actor type: field_agent, partner_api, regulator_sync, system",
    )
    source_description: str | None = Field(
        None,
        description="Human-readable description of the verification",
    )
    evidence_detail: dict | None = Field(
        None,
        description="Structured evidence metadata",
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nigeria Pharmacy Registry",
    version="0.2.0",
    description="Dashboard + Verification API for the Nigeria Pharmacy Registry",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def startup():
    # Always load JSON (fallback data)
    load_all_canonical()
    # Try to connect to DB (best-effort)
    if db.init_pool():
        logger.info("Running in DATABASE mode")
    else:
        logger.info("Running in JSON FALLBACK mode")


@app.on_event("shutdown")
async def shutdown():
    db.close_pool()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    """Health check — reports mode, record count, version."""
    mode = "database" if db.is_available() else "json_fallback"
    count = 0

    if db.is_available():
        try:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM pharmacy_locations")
                    count = cur.fetchone()[0]
        except Exception:
            count = len(_RECORDS)
            mode = "json_fallback"
    else:
        count = len(_RECORDS)

    return {
        "status": "healthy",
        "mode": mode,
        "record_count": count,
        "version": "0.2.0",
        "database_connected": db.is_available(),
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    """Serve the dashboard HTML."""
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Read endpoints (dual-mode: DB preferred, JSON fallback)
# ---------------------------------------------------------------------------


@app.get("/api/pharmacies")
async def list_pharmacies(
    state: str | None = Query(None, description="Filter by state name"),
    lga: str | None = Query(None, description="Filter by LGA"),
    facility_type: str | None = Query(None, description="Filter by facility type"),
    source_id: str | None = Query(None, description="Filter by data source"),
    q: str | None = Query(None, description="Search facility name (case-insensitive)"),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List pharmacy records with optional filters."""
    # Try DB first
    result = _db_list_pharmacies(state, lga, facility_type, source_id, q, limit, offset)
    if result is not None:
        return result

    # JSON fallback
    results = _RECORDS

    if state:
        state_lower = state.lower()
        results = [r for r in results if (r.get("state") or "").lower() == state_lower]
    if lga:
        lga_lower = lga.lower()
        results = [r for r in results if (r.get("lga") or "").lower() == lga_lower]
    if facility_type:
        ft_lower = facility_type.lower()
        results = [r for r in results if (r.get("facility_type") or "").lower() == ft_lower]
    if source_id:
        results = [r for r in results if r.get("source_id") == source_id]
    if q:
        q_lower = q.lower()
        results = [r for r in results if q_lower in (r.get("facility_name") or "").lower()]

    total = len(results)
    page = results[offset : offset + limit]

    return {
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": page,
    }


@app.get("/api/pharmacies/nearby")
async def nearby_pharmacies(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    radius_km: float = Query(5.0, ge=0.1, le=100, description="Search radius in km"),
    limit: int = Query(20, ge=1, le=100),
):
    """Find pharmacies near a given location using PostGIS spatial queries."""
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — spatial queries require a database connection",
        )

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM find_pharmacies_within_radius(%s, %s, %s)
                    LIMIT %s
                    """,
                    (lat, lon, radius_km, limit),
                )
                rows = cur.fetchall()

        return {
            "center": {"latitude": lat, "longitude": lon},
            "radius_km": radius_km,
            "count": len(rows),
            "data": [
                {
                    "pharmacy_id": str(r["id"]),
                    "facility_name": r["name"],
                    "facility_type": r["facility_type"],
                    "state": r["state"],
                    "lga": r["lga"],
                    "latitude": float(r["latitude"]),
                    "longitude": float(r["longitude"]),
                    "distance_km": float(r["distance_km"]),
                    "validation_level": r["current_validation_level"],
                    "operational_status": r["operational_status"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Nearby query failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pharmacies/{pharmacy_id}")
async def get_pharmacy(pharmacy_id: str) -> dict[str, Any]:
    """Get a single pharmacy record by ID."""
    # Try DB first
    result = _db_get_pharmacy(pharmacy_id)
    if result is not None:
        if result.get("data") is None:
            raise HTTPException(status_code=404, detail="Pharmacy not found")
        return result

    # JSON fallback
    record = _INDEX.get(pharmacy_id)
    if not record:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
    return {"data": record}


@app.get("/api/stats")
async def get_stats() -> dict[str, Any]:
    """Summary statistics for the registry."""
    # Try DB first
    result = _db_get_stats()
    if result is not None:
        return result

    # JSON fallback
    states = Counter(r.get("state") or "Unknown" for r in _RECORDS)
    sources = Counter(r.get("source_id") or "Unknown" for r in _RECORDS)
    types = Counter(r.get("facility_type") or "Unknown" for r in _RECORDS)
    validation = Counter(r.get("validation_label") or "Unknown" for r in _RECORDS)

    return {
        "total": len(_RECORDS),
        "by_state": dict(states.most_common()),
        "by_source": dict(sources.most_common()),
        "by_facility_type": dict(types.most_common()),
        "by_validation_level": dict(validation.most_common()),
        "states_covered": len(states),
    }


@app.get("/api/geojson")
async def get_geojson(
    state: str | None = Query(None),
    source_id: str | None = Query(None),
    facility_type: str | None = Query(None),
) -> dict[str, Any]:
    """Return records as GeoJSON FeatureCollection for map rendering."""
    # Try DB first
    result = _db_get_geojson(state, source_id, facility_type)
    if result is not None:
        return result

    # JSON fallback
    results = _RECORDS

    if state:
        state_lower = state.lower()
        results = [r for r in results if (r.get("state") or "").lower() == state_lower]
    if source_id:
        results = [r for r in results if r.get("source_id") == source_id]
    if facility_type:
        ft_lower = facility_type.lower()
        results = [r for r in results if (r.get("facility_type") or "").lower() == ft_lower]

    features = []
    for r in results:
        lat = r.get("latitude")
        lon = r.get("longitude")
        if lat is None or lon is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "pharmacy_id": r.get("pharmacy_id"),
                "facility_name": r.get("facility_name"),
                "facility_type": r.get("facility_type"),
                "state": r.get("state"),
                "lga": r.get("lga"),
                "source_id": r.get("source_id"),
                "validation_label": r.get("validation_label"),
                "operational_status": r.get("operational_status"),
                "phone": r.get("phone"),
                "address_line": r.get("address_line"),
            },
        })

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Verification endpoints (database required)
# ---------------------------------------------------------------------------


@app.post("/api/pharmacies/{pharmacy_id}/verify")
async def verify_pharmacy(pharmacy_id: str, req: VerifyRequest):
    """
    Advance a pharmacy through the validation ladder.

    Validates transition rules, records evidence, and updates the
    pharmacy's validation level via the record_validation_change()
    PL/pgSQL function.
    """
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — verification requires a database connection",
        )

    target = req.target_level
    if target not in _LEVEL_INDEX:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_level '{target}'. Valid levels: {VALIDATION_LEVELS}",
        )

    # Validate evidence type matches target level
    required = REQUIRED_EVIDENCE.get(target)
    if required and req.evidence_type != required:
        # Exception: regulator_sync actors can jump L0→L3
        if not (req.actor_type == "regulator_sync" and target == "L3_regulator_verified"):
            raise HTTPException(
                status_code=400,
                detail=f"Target level {target} requires evidence_type='{required}', got '{req.evidence_type}'",
            )

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Get current level
                cur.execute(
                    "SELECT current_validation_level::text FROM pharmacy_locations WHERE id = %s",
                    (pharmacy_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Pharmacy not found")

                current_level = row["current_validation_level"]
                current_idx = _LEVEL_INDEX.get(current_level, 0)
                target_idx = _LEVEL_INDEX[target]

                # Validate transition: must go up one step at a time
                # Exception: regulator_sync can jump L0→L3
                if req.actor_type == "regulator_sync" and target == "L3_regulator_verified":
                    if current_idx >= target_idx:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Cannot transition from {current_level} to {target} — already at or above target",
                        )
                elif target_idx != current_idx + 1:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid transition: {current_level} → {target}. "
                        f"Must advance one level at a time (next: {VALIDATION_LEVELS[current_idx + 1] if current_idx + 1 < len(VALIDATION_LEVELS) else 'max reached'})",
                    )

                # Build evidence reference
                evidence_ref = f"{req.evidence_type}:{req.capture_method or 'unspecified'}"

                # Build evidence detail
                evidence_detail = req.evidence_detail or {}
                evidence_detail["evidence_type"] = req.evidence_type
                evidence_detail["capture_method"] = req.capture_method
                evidence_detail["verified_at"] = datetime.now(timezone.utc).isoformat()

                # Call the PL/pgSQL function
                cur.execute(
                    """
                    SELECT record_validation_change(
                        %s::uuid, %s::validation_level,
                        %s, %s,
                        %s, %s, %s::jsonb
                    ) AS history_id
                    """,
                    (
                        pharmacy_id,
                        target,
                        req.actor_id,
                        req.actor_type,
                        evidence_ref,
                        req.source_description,
                        json.dumps(evidence_detail),
                    ),
                )
                history_id = str(cur.fetchone()["history_id"])

                # Log provenance
                cur.execute(
                    """
                    SELECT log_provenance(
                        'pharmacy_location', %s::uuid, 'verify',
                        %s, %s, NULL, NULL, NULL, %s::jsonb
                    )
                    """,
                    (
                        pharmacy_id,
                        req.actor_id,
                        req.actor_type,
                        json.dumps({
                            "old_level": current_level,
                            "new_level": target,
                            "evidence_type": req.evidence_type,
                            "capture_method": req.capture_method,
                            "history_id": history_id,
                        }),
                    ),
                )

        return {
            "success": True,
            "pharmacy_id": pharmacy_id,
            "old_level": current_level,
            "new_level": target,
            "history_id": history_id,
            "message": f"Pharmacy advanced from {_level_label(current_level)} to {_level_label(target)}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Verification failed for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=f"Verification failed: {e}")


@app.get("/api/pharmacies/{pharmacy_id}/validation-history")
async def get_validation_history(pharmacy_id: str):
    """
    Get the full append-only validation history for a pharmacy.
    Returns all status transitions ordered by most recent first.
    """
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — validation history requires a database connection",
        )

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Verify pharmacy exists
                cur.execute("SELECT id FROM pharmacy_locations WHERE id = %s", (pharmacy_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Pharmacy not found")

                cur.execute(
                    """
                    SELECT id, old_level::text, new_level::text,
                           changed_at, changed_by, actor_type,
                           evidence_reference, source_description,
                           evidence_detail
                    FROM validation_status_history
                    WHERE pharmacy_id = %s
                    ORDER BY changed_at DESC
                    """,
                    (pharmacy_id,),
                )
                rows = cur.fetchall()

        return {
            "pharmacy_id": pharmacy_id,
            "history_count": len(rows),
            "history": [
                {
                    "id": str(r["id"]),
                    "old_level": r["old_level"],
                    "new_level": r["new_level"],
                    "old_label": _level_label(r["old_level"]),
                    "new_label": _level_label(r["new_level"]),
                    "changed_at": _iso(r["changed_at"]),
                    "changed_by": r["changed_by"],
                    "actor_type": r["actor_type"],
                    "evidence_reference": r["evidence_reference"],
                    "source_description": r["source_description"],
                    "evidence_detail": r["evidence_detail"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get validation history for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/validation/summary")
async def get_validation_summary():
    """
    Count records at each validation level.
    Works in both DB and JSON modes.
    """
    if db.is_available():
        try:
            with db.get_conn() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT current_validation_level::text AS level,
                               count(*) AS count
                        FROM pharmacy_locations
                        GROUP BY current_validation_level
                        ORDER BY current_validation_level
                        """
                    )
                    rows = cur.fetchall()

            total = sum(r["count"] for r in rows)
            return {
                "total": total,
                "mode": "database",
                "levels": [
                    {
                        "level": r["level"],
                        "label": _level_label(r["level"]),
                        "count": r["count"],
                        "percentage": round(r["count"] / total * 100, 1) if total > 0 else 0,
                    }
                    for r in rows
                ],
            }
        except Exception as e:
            logger.warning("DB validation summary failed: %s", e)

    # JSON fallback
    levels = Counter(r.get("validation_level", "L0_mapped") for r in _RECORDS)
    total = len(_RECORDS)
    return {
        "total": total,
        "mode": "json_fallback",
        "levels": [
            {
                "level": lvl,
                "label": _level_label(lvl),
                "count": cnt,
                "percentage": round(cnt / total * 100, 1) if total > 0 else 0,
            }
            for lvl, cnt in sorted(levels.items())
        ],
    }


