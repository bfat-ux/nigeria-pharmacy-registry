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

import csv
import glob
import io
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .auth import (
    ANONYMOUS,
    AuthContext,
    auth_middleware,
    redact_contacts_in_response,
    require_tier,
)
from .db import extras
from .rate_limiter import rate_limit_middleware

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


class TaskGenerateRequest(BaseModel):
    target_level: str = Field(
        ...,
        description="Target validation level (e.g. L1_contact_confirmed)",
    )
    filters: dict | None = Field(
        None,
        description="Optional filters: {state, lga, facility_type}",
    )
    priority: int = Field(
        3,
        ge=1,
        le=5,
        description="Task priority: 1=highest (urgent), 5=lowest (routine)",
    )
    due_date: str | None = Field(
        None,
        description="Due date in YYYY-MM-DD format",
    )
    max_attempts: int = Field(
        3,
        ge=1,
        le=10,
        description="Maximum attempts before task is abandoned",
    )


class TaskSkipRequest(BaseModel):
    reason: str = Field(
        ...,
        description="Reason for skipping the task",
    )
    reschedule: bool = Field(
        False,
        description="If true, create a new task with attempt_count+1",
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nigeria Pharmacy Registry",
    version="0.3.0",
    description="Dashboard + Verification API for the Nigeria Pharmacy Registry",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth + rate limiting middleware (order matters: auth first, then rate limit)
# Starlette middleware executes in reverse registration order,
# so we register rate_limit first (runs second) then auth (runs first).
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(auth_middleware)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


_SERVER_STARTED_AT = datetime.now(timezone.utc)


@app.on_event("startup")
async def startup():
    # Always load JSON (fallback data)
    load_all_canonical()
    # Try to connect to DB (best-effort)
    if db.init_pool():
        logger.info("Running in DATABASE mode")
        _ensure_api_keys_table()
        _ensure_verification_tasks_table()
    else:
        logger.info("Running in JSON FALLBACK mode")


def _ensure_api_keys_table():
    """Run 005_api_keys.sql if the table doesn't exist yet."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'api_keys')"
                )
                exists = cur.fetchone()[0]
                if exists:
                    return

        sql_path = ROOT / "agent-01-data-architecture" / "sql" / "005_api_keys.sql"
        if sql_path.exists():
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_path.read_text())
            logger.info("Applied 005_api_keys.sql migration")
        else:
            logger.warning("005_api_keys.sql not found at %s", sql_path)
    except Exception as e:
        logger.warning("Could not ensure api_keys table: %s", e)


def _ensure_verification_tasks_table():
    """Run 006_verification_tasks.sql if the table doesn't exist yet."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'verification_tasks')"
                )
                exists = cur.fetchone()[0]
                if exists:
                    return

        sql_path = ROOT / "agent-01-data-architecture" / "sql" / "006_verification_tasks.sql"
        if sql_path.exists():
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_path.read_text())
            logger.info("Applied 006_verification_tasks.sql migration")
        else:
            logger.warning("006_verification_tasks.sql not found at %s", sql_path)
    except Exception as e:
        logger.warning("Could not ensure verification_tasks table: %s", e)


@app.on_event("shutdown")
async def shutdown():
    db.close_pool()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    """Health check — reports mode, record count, version, DB latency. Always open."""
    import time

    mode = "database" if db.is_available() else "json_fallback"
    count = 0
    db_ok = False
    db_latency_ms: float | None = None

    if db.is_available():
        try:
            t0 = time.monotonic()
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM pharmacy_locations")
                    count = cur.fetchone()[0]
            db_latency_ms = round((time.monotonic() - t0) * 1000, 1)
            db_ok = True
        except Exception:
            count = len(_RECORDS)
            mode = "json_fallback"
    else:
        count = len(_RECORDS)

    uptime_seconds = round((datetime.now(timezone.utc) - _SERVER_STARTED_AT).total_seconds())

    overall_status = "healthy" if db_ok else "degraded"
    http_status = 200 if db_ok else 503

    from starlette.responses import JSONResponse

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall_status,
            "mode": mode,
            "record_count": count,
            "version": app.version,
            "database_connected": db_ok,
            "auth_enabled": True,
            "started_at": _iso(_SERVER_STARTED_AT),
            "uptime_seconds": uptime_seconds,
            "checks": {
                "database": {
                    "status": "up" if db_ok else "down",
                    "latency_ms": db_latency_ms,
                },
            },
        },
    )


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
    request: Request,
    state: str | None = Query(None, description="Filter by state name"),
    lga: str | None = Query(None, description="Filter by LGA"),
    facility_type: str | None = Query(None, description="Filter by facility type"),
    source_id: str | None = Query(None, description="Filter by data source"),
    q: str | None = Query(None, description="Search facility name (case-insensitive)"),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List pharmacy records with optional filters. Contacts redacted for public tier."""
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    # Try DB first
    result = _db_list_pharmacies(state, lga, facility_type, source_id, q, limit, offset)
    if result is not None:
        redact_contacts_in_response(result.get("data", []), auth)
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
    page = [dict(r) for r in results[offset : offset + limit]]  # shallow copy for redaction
    redact_contacts_in_response(page, auth)

    return {
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": page,
    }


@app.get("/api/pharmacies/nearby")
async def nearby_pharmacies(
    request: Request,
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
async def get_pharmacy(request: Request, pharmacy_id: str) -> dict[str, Any]:
    """Get a single pharmacy record by ID. Contacts redacted for public tier."""
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    # Try DB first
    result = _db_get_pharmacy(pharmacy_id)
    if result is not None:
        if result.get("data") is None:
            raise HTTPException(status_code=404, detail="Pharmacy not found")
        redact_contacts_in_response(result.get("data"), auth)
        return result

    # JSON fallback
    record = _INDEX.get(pharmacy_id)
    if not record:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
    data = dict(record)  # shallow copy for redaction
    redact_contacts_in_response(data, auth)
    return {"data": data}


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
    request: Request,
    state: str | None = Query(None),
    source_id: str | None = Query(None),
    facility_type: str | None = Query(None),
) -> dict[str, Any]:
    """Return records as GeoJSON FeatureCollection for map rendering. Contacts redacted for public."""
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    # Try DB first
    result = _db_get_geojson(state, source_id, facility_type)
    if result is not None:
        redact_contacts_in_response(result.get("features", []), auth)
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
# Verification core logic (reused by direct endpoint and queue completion)
# ---------------------------------------------------------------------------


def _execute_verification(pharmacy_id: str, req: VerifyRequest, auth: AuthContext) -> dict:
    """
    Core verification logic. Validates transition rules, records evidence,
    and updates the pharmacy's validation level.

    Returns: {success, pharmacy_id, old_level, new_level, history_id, message}
    Raises HTTPException on validation/business-rule errors.
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

                # Audit log
                cur.execute(
                    """
                    SELECT log_audit(
                        'api_request', 'POST',
                        %s, %s,
                        'pharmacy_location', %s::uuid,
                        %s, 'POST', NULL, 200, NULL,
                        %s::jsonb
                    )
                    """,
                    (
                        auth.actor_id,
                        auth.actor_type,
                        pharmacy_id,
                        f"/api/pharmacies/{pharmacy_id}/verify",
                        json.dumps({
                            "action": "verify",
                            "old_level": current_level,
                            "new_level": target,
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


# ---------------------------------------------------------------------------
# Verification endpoints (database required)
# ---------------------------------------------------------------------------


@app.post(
    "/api/pharmacies/{pharmacy_id}/verify",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def verify_pharmacy(request: Request, pharmacy_id: str, req: VerifyRequest):
    """
    Advance a pharmacy through the validation ladder.

    Requires: registry_write tier or higher.
    """
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)
    return _execute_verification(pharmacy_id, req, auth)


@app.get(
    "/api/pharmacies/{pharmacy_id}/validation-history",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def get_validation_history(pharmacy_id: str):
    """
    Get the full append-only validation history for a pharmacy.

    Requires: registry_read tier or higher.
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


# ---------------------------------------------------------------------------
# Verification queue endpoints
# ---------------------------------------------------------------------------

# Map target level → task_type enum value
_TASK_TYPE_MAP = {
    "L1_contact_confirmed": "verify_L1",
    "L2_evidence_documented": "verify_L2",
    "L3_regulator_verified": "verify_L3",
    "L4_high_assurance": "verify_L4",
}


def _task_row_to_dict(row: dict) -> dict:
    """Convert a verification_tasks JOIN row to API dict."""
    return {
        "id": str(row["id"]),
        "pharmacy_id": str(row["pharmacy_id"]),
        "pharmacy_name": row.get("pharmacy_name"),
        "pharmacy_state": row.get("pharmacy_state"),
        "pharmacy_lga": row.get("pharmacy_lga"),
        "pharmacy_facility_type": row.get("pharmacy_facility_type"),
        "pharmacy_current_level": row.get("pharmacy_current_level"),
        "task_type": row.get("task_type"),
        "target_level": row.get("target_level"),
        "target_label": _level_label(row.get("target_level")),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "assigned_to": row.get("assigned_to"),
        "assigned_at": _iso(row.get("assigned_at")),
        "completed_at": _iso(row.get("completed_at")),
        "due_date": str(row["due_date"]) if row.get("due_date") else None,
        "attempt_count": row.get("attempt_count"),
        "max_attempts": row.get("max_attempts"),
        "notes": row.get("notes"),
        "result_detail": row.get("result_detail"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "created_by": row.get("created_by"),
    }


_TASK_QUERY_BASE = """
    SELECT
        vt.id, vt.pharmacy_id, vt.task_type::text, vt.target_level::text,
        vt.status::text, vt.priority, vt.assigned_to, vt.assigned_at,
        vt.completed_at, vt.due_date, vt.attempt_count, vt.max_attempts,
        vt.notes, vt.result_detail,
        vt.created_at, vt.updated_at, vt.created_by,
        pl.name AS pharmacy_name,
        pl.state AS pharmacy_state,
        pl.lga AS pharmacy_lga,
        pl.facility_type::text AS pharmacy_facility_type,
        pl.current_validation_level::text AS pharmacy_current_level
    FROM verification_tasks vt
    JOIN pharmacy_locations pl ON pl.id = vt.pharmacy_id
"""


@app.get(
    "/api/queue",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def list_queue(
    request: Request,
    status: str | None = Query(None, description="Filter by task status"),
    target_level: str | None = Query(None, description="Filter by target level"),
    state: str | None = Query(None, description="Filter by pharmacy state"),
    lga: str | None = Query(None, description="Filter by pharmacy LGA"),
    assigned_to: str | None = Query(None, description="Filter by assignee"),
    priority: int | None = Query(None, ge=1, le=5, description="Filter by priority"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List verification tasks with optional filters. Requires registry_read."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("vt.status = %s::task_status")
            params.append(status)
        if target_level:
            conditions.append("vt.target_level = %s::validation_level")
            params.append(target_level)
        if state:
            conditions.append("pl.state ILIKE %s")
            params.append(state)
        if lga:
            conditions.append("pl.lga ILIKE %s")
            params.append(lga)
        if assigned_to:
            conditions.append("vt.assigned_to = %s")
            params.append(assigned_to)
        if priority is not None:
            conditions.append("vt.priority = %s")
            params.append(priority)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT count(*) FROM verification_tasks vt JOIN pharmacy_locations pl ON pl.id = vt.pharmacy_id{where}",
                    params,
                )
                total = cur.fetchone()["count"]

                cur.execute(
                    f"{_TASK_QUERY_BASE}{where} ORDER BY vt.priority ASC, vt.created_at ASC LIMIT %s OFFSET %s",
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "meta": {"total": total, "limit": limit, "offset": offset},
            "data": [_task_row_to_dict(r) for r in rows],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Queue list failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/queue/stats",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def queue_stats():
    """Queue statistics: counts by status, level, assignee, state."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Total by status
                cur.execute(
                    "SELECT status::text, count(*) AS cnt FROM verification_tasks GROUP BY status"
                )
                by_status = {r["status"]: r["cnt"] for r in cur.fetchall()}

                # By target_level x status
                cur.execute(
                    """
                    SELECT target_level::text, status::text, count(*) AS cnt
                    FROM verification_tasks
                    GROUP BY target_level, status
                    ORDER BY target_level, status
                    """
                )
                by_level: dict[str, dict[str, int]] = {}
                for r in cur.fetchall():
                    by_level.setdefault(r["target_level"], {})[r["status"]] = r["cnt"]

                # By assignee (active only)
                cur.execute(
                    """
                    SELECT assigned_to, count(*) AS cnt
                    FROM verification_tasks
                    WHERE assigned_to IS NOT NULL
                      AND status IN ('assigned', 'in_progress')
                    GROUP BY assigned_to
                    ORDER BY cnt DESC
                    """
                )
                by_assignee = [
                    {"assigned_to": r["assigned_to"], "count": r["cnt"]}
                    for r in cur.fetchall()
                ]

                # By state (active only)
                cur.execute(
                    """
                    SELECT pl.state, vt.status::text, count(*) AS cnt
                    FROM verification_tasks vt
                    JOIN pharmacy_locations pl ON pl.id = vt.pharmacy_id
                    WHERE vt.status IN ('pending', 'assigned', 'in_progress')
                    GROUP BY pl.state, vt.status
                    ORDER BY pl.state
                    """
                )
                by_state: dict[str, dict[str, int]] = {}
                for r in cur.fetchall():
                    by_state.setdefault(r["state"], {})[r["status"]] = r["cnt"]

                # Overdue count
                cur.execute(
                    """
                    SELECT count(*) AS cnt
                    FROM verification_tasks
                    WHERE status IN ('pending', 'assigned', 'in_progress')
                      AND due_date IS NOT NULL
                      AND due_date < CURRENT_DATE
                    """
                )
                overdue = cur.fetchone()["cnt"]

        total = sum(by_status.values())
        return {
            "total_tasks": total,
            "by_status": by_status,
            "by_target_level": by_level,
            "by_assignee": by_assignee,
            "by_state": by_state,
            "overdue_count": overdue,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Queue stats failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/queue/{task_id}",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def get_task(task_id: str):
    """Get a single verification task with pharmacy info."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    f"{_TASK_QUERY_BASE} WHERE vt.id = %s",
                    (task_id,),
                )
                row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"data": _task_row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Get task failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/api/queue/generate",
    dependencies=[Depends(require_tier("admin"))],
)
async def generate_queue(request: Request, req: TaskGenerateRequest):
    """
    Batch-generate verification tasks for pharmacies at the prerequisite level.

    Requires: admin tier.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    target = req.target_level
    if target not in _LEVEL_INDEX:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_level '{target}'. Valid levels: {VALIDATION_LEVELS}",
        )

    target_idx = _LEVEL_INDEX[target]
    if target_idx == 0:
        raise HTTPException(status_code=400, detail="Cannot generate tasks for L0 — that's the initial state")

    prereq_level = VALIDATION_LEVELS[target_idx - 1]
    task_type = _TASK_TYPE_MAP.get(target)
    if not task_type:
        raise HTTPException(status_code=400, detail=f"No task_type mapping for {target}")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        # Build filter conditions
        conditions: list[str] = [
            "pl.current_validation_level = %s::validation_level",
        ]
        params: list[Any] = [prereq_level]

        # Exclude pharmacies with existing active tasks for this target
        conditions.append(
            """NOT EXISTS (
                SELECT 1 FROM verification_tasks vt
                WHERE vt.pharmacy_id = pl.id
                  AND vt.target_level = %s::validation_level
                  AND vt.status NOT IN ('completed', 'failed', 'skipped')
            )"""
        )
        params.append(target)

        # Optional geographic/type filters
        filters = req.filters or {}
        if filters.get("state"):
            conditions.append("pl.state ILIKE %s")
            params.append(filters["state"])
        if filters.get("lga"):
            conditions.append("pl.lga ILIKE %s")
            params.append(filters["lga"])
        if filters.get("facility_type"):
            conditions.append("pl.facility_type = %s::facility_type")
            params.append(filters["facility_type"])

        where = " WHERE " + " AND ".join(conditions)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Find eligible pharmacies
                cur.execute(f"SELECT pl.id FROM pharmacy_locations pl{where}", params)
                pharmacy_ids = [row["id"] for row in cur.fetchall()]

                if not pharmacy_ids:
                    return {
                        "success": True,
                        "target_level": target,
                        "tasks_created": 0,
                        "filters_applied": filters,
                        "message": f"No eligible pharmacies found at {prereq_level}",
                    }

                # Batch insert
                values = [
                    (
                        pid,
                        task_type,
                        target,
                        req.priority,
                        req.due_date,
                        req.max_attempts,
                        auth.actor_id,
                        auth.actor_id,
                    )
                    for pid in pharmacy_ids
                ]

                extras.execute_values(
                    cur,
                    """
                    INSERT INTO verification_tasks
                        (pharmacy_id, task_type, target_level, priority, due_date,
                         max_attempts, created_by, updated_by)
                    VALUES %s
                    """,
                    values,
                    template="(%s, %s::task_type, %s::validation_level, %s, %s::date, %s, %s, %s)",
                )

                created = len(pharmacy_ids)

                # Log provenance for the batch
                cur.execute(
                    """
                    SELECT log_provenance(
                        'verification_task', gen_random_uuid(), 'batch_generate',
                        %s, %s, NULL, NULL, NULL, %s::jsonb
                    )
                    """,
                    (
                        auth.actor_id,
                        auth.actor_type,
                        json.dumps({
                            "target_level": target,
                            "task_type": task_type,
                            "tasks_created": created,
                            "filters": filters,
                        }),
                    ),
                )

        return {
            "success": True,
            "target_level": target,
            "prerequisite_level": prereq_level,
            "task_type": task_type,
            "tasks_created": created,
            "filters_applied": filters,
            "message": f"Created {created} {task_type} tasks for pharmacies at {prereq_level}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Queue generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/api/queue/{task_id}/claim",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def claim_task(request: Request, task_id: str):
    """Claim a pending task. Requires registry_write."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE verification_tasks
                    SET status = 'assigned'::task_status,
                        assigned_to = %s,
                        assigned_at = now(),
                        updated_at = now(),
                        updated_by = %s
                    WHERE id = %s
                      AND status = 'pending'::task_status
                    RETURNING id, status::text, assigned_to, assigned_at
                    """,
                    (auth.actor_id, auth.actor_id, task_id),
                )
                row = cur.fetchone()

        if not row:
            # Check if task exists at all
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT status::text FROM verification_tasks WHERE id = %s", (task_id,))
                    existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Task not found")
            raise HTTPException(
                status_code=409,
                detail=f"Task cannot be claimed — current status: {existing[0]}",
            )

        return {
            "success": True,
            "task_id": str(row["id"]),
            "status": row["status"],
            "assigned_to": row["assigned_to"],
            "assigned_at": _iso(row["assigned_at"]),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Claim task failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/api/queue/{task_id}/release",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def release_task(request: Request, task_id: str):
    """Release a claimed task back to pending. Only assignee or admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Get current task state
                cur.execute(
                    "SELECT id, status::text, assigned_to FROM verification_tasks WHERE id = %s",
                    (task_id,),
                )
                task = cur.fetchone()
                if not task:
                    raise HTTPException(status_code=404, detail="Task not found")

                if task["status"] not in ("assigned", "in_progress"):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Cannot release — task status is '{task['status']}', expected 'assigned' or 'in_progress'",
                    )

                # Only assignee or admin
                if task["assigned_to"] != auth.actor_id and auth.tier != "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="Can only release your own tasks (or be admin)",
                    )

                cur.execute(
                    """
                    UPDATE verification_tasks
                    SET status = 'pending'::task_status,
                        assigned_to = NULL,
                        assigned_at = NULL,
                        updated_at = now(),
                        updated_by = %s
                    WHERE id = %s
                    RETURNING id
                    """,
                    (auth.actor_id, task_id),
                )

        return {
            "success": True,
            "task_id": task_id,
            "status": "pending",
            "message": "Task released back to queue",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Release task failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/api/queue/{task_id}/complete",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def complete_task(request: Request, task_id: str, req: VerifyRequest):
    """
    Complete a verification task with evidence.
    Internally calls the same verification logic as POST /verify.
    Requires registry_write. Only assignee or admin.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        # Get task
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, pharmacy_id, target_level::text, status::text, assigned_to FROM verification_tasks WHERE id = %s",
                    (task_id,),
                )
                task = cur.fetchone()

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["status"] not in ("assigned", "in_progress"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot complete — task status is '{task['status']}', expected 'assigned' or 'in_progress'",
            )

        # Only assignee or admin
        if task["assigned_to"] != auth.actor_id and auth.tier != "admin":
            raise HTTPException(
                status_code=403,
                detail="Can only complete your own tasks (or be admin)",
            )

        # Ensure target_level matches
        if req.target_level != task["target_level"]:
            raise HTTPException(
                status_code=400,
                detail=f"target_level mismatch: task expects '{task['target_level']}', request says '{req.target_level}'",
            )

        # Execute the verification
        pharmacy_id = str(task["pharmacy_id"])
        verification_result = _execute_verification(pharmacy_id, req, auth)

        # Mark task as completed
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE verification_tasks
                    SET status = 'completed'::task_status,
                        completed_at = now(),
                        result_detail = %s::jsonb,
                        updated_at = now(),
                        updated_by = %s
                    WHERE id = %s
                    """,
                    (
                        json.dumps(verification_result),
                        auth.actor_id,
                        task_id,
                    ),
                )

        return {
            "success": True,
            "task_id": task_id,
            "status": "completed",
            "verification": verification_result,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Complete task failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/api/queue/{task_id}/skip",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def skip_task(request: Request, task_id: str, req: TaskSkipRequest):
    """
    Skip a verification task with a reason.
    Optionally reschedule with attempt_count+1.
    Requires registry_write. Only assignee or admin.
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Get task
                cur.execute(
                    """
                    SELECT id, pharmacy_id, task_type::text, target_level::text,
                           status::text, assigned_to, priority, due_date,
                           attempt_count, max_attempts
                    FROM verification_tasks WHERE id = %s
                    """,
                    (task_id,),
                )
                task = cur.fetchone()

                if not task:
                    raise HTTPException(status_code=404, detail="Task not found")

                if task["status"] not in ("assigned", "in_progress"):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Cannot skip — task status is '{task['status']}', expected 'assigned' or 'in_progress'",
                    )

                if task["assigned_to"] != auth.actor_id and auth.tier != "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="Can only skip your own tasks (or be admin)",
                    )

                # Mark as skipped
                result_detail = {
                    "skip_reason": req.reason,
                    "skipped_by": auth.actor_id,
                }
                cur.execute(
                    """
                    UPDATE verification_tasks
                    SET status = 'skipped'::task_status,
                        completed_at = now(),
                        result_detail = %s::jsonb,
                        updated_at = now(),
                        updated_by = %s
                    WHERE id = %s
                    """,
                    (json.dumps(result_detail), auth.actor_id, task_id),
                )

                # Optionally reschedule
                new_task_id = None
                if req.reschedule and task["attempt_count"] < task["max_attempts"]:
                    cur.execute(
                        """
                        INSERT INTO verification_tasks
                            (pharmacy_id, task_type, target_level, priority,
                             due_date, attempt_count, max_attempts,
                             notes, created_by, updated_by)
                        VALUES (%s, %s::task_type, %s::validation_level, %s,
                                %s::date, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            str(task["pharmacy_id"]),
                            task["task_type"],
                            task["target_level"],
                            task["priority"],
                            str(task["due_date"]) if task["due_date"] else None,
                            task["attempt_count"] + 1,
                            task["max_attempts"],
                            f"Rescheduled from {task_id}: {req.reason}",
                            auth.actor_id,
                            auth.actor_id,
                        ),
                    )
                    new_task_id = str(cur.fetchone()["id"])

        return {
            "success": True,
            "task_id": task_id,
            "status": "skipped",
            "reason": req.reason,
            "rescheduled": new_task_id is not None,
            "new_task_id": new_task_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Skip task failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Audit / Provenance endpoints (Phase 2)
# ---------------------------------------------------------------------------


def _parse_date_param(value: str | None, param_name: str) -> str | None:
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


# --- 1. Pharmacy Evidence ---


@app.get(
    "/api/pharmacies/{pharmacy_id}/evidence",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def get_pharmacy_evidence(
    pharmacy_id: str,
    include_detail: bool = Query(True, description="Include full evidence_detail JSONB"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """All evidence submitted for a pharmacy, extracted from validation_status_history."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT id FROM pharmacy_locations WHERE id = %s", (pharmacy_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Pharmacy not found")

                cur.execute(
                    "SELECT count(*) FROM validation_status_history WHERE pharmacy_id = %s AND evidence_detail IS NOT NULL",
                    (pharmacy_id,),
                )
                total = cur.fetchone()["count"]

                cur.execute(
                    """
                    SELECT id, new_level::text, changed_at, changed_by, actor_type,
                           evidence_reference, source_description, evidence_detail
                    FROM validation_status_history
                    WHERE pharmacy_id = %s AND evidence_detail IS NOT NULL
                    ORDER BY changed_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (pharmacy_id, limit, offset),
                )
                rows = cur.fetchall()

        data = []
        for r in rows:
            ed = r["evidence_detail"] or {}
            item = {
                "id": str(r["id"]),
                "validation_level": r["new_level"],
                "validation_label": _level_label(r["new_level"]),
                "evidence_type": ed.get("evidence_type"),
                "capture_method": ed.get("capture_method"),
                "actor": r["changed_by"],
                "actor_type": r["actor_type"],
                "evidence_reference": r["evidence_reference"],
                "source_description": r["source_description"],
                "timestamp": _iso(r["changed_at"]),
            }
            if include_detail:
                item["evidence_detail"] = ed
            data.append(item)

        return {
            "pharmacy_id": pharmacy_id,
            "meta": {"total": total, "limit": limit, "offset": offset},
            "data": data,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get evidence for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- 2. Provenance Search ---


@app.get(
    "/api/provenance",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def search_provenance(
    actor: str | None = Query(None),
    actor_type: str | None = Query(None),
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    source_system: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO 8601 start date"),
    date_to: str | None = Query(None, description="ISO 8601 end date"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Search provenance records with filters."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    _parse_date_param(date_from, "date_from")
    _parse_date_param(date_to, "date_to")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if actor:
            conditions.append("actor = %s")
            params.append(actor)
        if actor_type:
            conditions.append("actor_type = %s")
            params.append(actor_type)
        if action:
            conditions.append("action = %s")
            params.append(action)
        if entity_type:
            conditions.append("entity_type = %s")
            params.append(entity_type)
        if entity_id:
            conditions.append("entity_id = %s::uuid")
            params.append(entity_id)
        if source_system:
            conditions.append("source_system = %s")
            params.append(source_system)
        if date_from:
            conditions.append("happened_at >= %s::timestamptz")
            params.append(date_from)
        if date_to:
            conditions.append("happened_at <= %s::timestamptz")
            params.append(date_to)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM provenance_records{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT id, entity_type, entity_id, action, actor, actor_type,
                           source_system, source_dataset, source_record_id,
                           happened_at, detail
                    FROM provenance_records{where}
                    ORDER BY happened_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "meta": {"total": total, "limit": limit, "offset": offset},
            "data": [
                {
                    "id": str(r["id"]),
                    "entity_type": r["entity_type"],
                    "entity_id": str(r["entity_id"]),
                    "action": r["action"],
                    "actor": r["actor"],
                    "actor_type": r["actor_type"],
                    "source_system": r["source_system"],
                    "source_dataset": r["source_dataset"],
                    "source_record_id": r["source_record_id"],
                    "happened_at": _iso(r["happened_at"]),
                    "detail": r["detail"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Provenance search failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- 3. Audit Log Search ---


@app.get(
    "/api/audit",
    dependencies=[Depends(require_tier("admin"))],
)
async def search_audit_log(
    actor: str | None = Query(None),
    actor_type: str | None = Query(None),
    event_type: str | None = Query(None),
    event_action: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO 8601 start date"),
    date_to: str | None = Query(None, description="ISO 8601 end date"),
    response_status: int | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Search audit log (admin only). Excludes IP/user-agent for privacy."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    _parse_date_param(date_from, "date_from")
    _parse_date_param(date_to, "date_to")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if actor:
            conditions.append("actor = %s")
            params.append(actor)
        if actor_type:
            conditions.append("actor_type = %s")
            params.append(actor_type)
        if event_type:
            conditions.append("event_type = %s")
            params.append(event_type)
        if event_action:
            conditions.append("event_action = %s")
            params.append(event_action)
        if resource_type:
            conditions.append("resource_type = %s")
            params.append(resource_type)
        if resource_id:
            conditions.append("resource_id = %s::uuid")
            params.append(resource_id)
        if date_from:
            conditions.append("happened_at >= %s::timestamptz")
            params.append(date_from)
        if date_to:
            conditions.append("happened_at <= %s::timestamptz")
            params.append(date_to)
        if response_status is not None:
            conditions.append("response_status = %s")
            params.append(response_status)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM audit_log{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT id, event_type, event_action, actor, actor_type,
                           resource_type, resource_id, request_path, request_method,
                           response_status, duration_ms, detail, happened_at
                    FROM audit_log{where}
                    ORDER BY happened_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "meta": {"total": total, "limit": limit, "offset": offset},
            "data": [
                {
                    "id": str(r["id"]),
                    "event_type": r["event_type"],
                    "event_action": r["event_action"],
                    "actor": r["actor"],
                    "actor_type": r["actor_type"],
                    "resource_type": r["resource_type"],
                    "resource_id": str(r["resource_id"]) if r["resource_id"] else None,
                    "request_path": r["request_path"],
                    "request_method": r["request_method"],
                    "response_status": r["response_status"],
                    "duration_ms": r["duration_ms"],
                    "detail": r["detail"],
                    "happened_at": _iso(r["happened_at"]),
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Audit log search failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- 4. Actor Activity ---


@app.get(
    "/api/actors/{actor_id}/activity",
    dependencies=[Depends(require_tier("admin"))],
)
async def get_actor_activity(
    actor_id: str,
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO 8601 start date"),
    date_to: str | None = Query(None, description="ISO 8601 end date"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Activity log and summary stats for a specific actor (admin only)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    _parse_date_param(date_from, "date_from")
    _parse_date_param(date_to, "date_to")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # Summary stats
                cur.execute(
                    """
                    SELECT
                        count(*) AS total_actions,
                        count(*) FILTER (WHERE action = 'verify') AS verifications_completed,
                        count(*) FILTER (WHERE action = 'create') AS records_created,
                        count(*) FILTER (WHERE action = 'update') AS records_updated,
                        count(*) FILTER (WHERE action = 'merge') AS records_merged,
                        count(*) FILTER (WHERE action = 'import') AS records_imported,
                        min(happened_at) AS first_action_at,
                        max(happened_at) AS last_action_at,
                        count(DISTINCT entity_id) AS unique_entities_touched
                    FROM provenance_records
                    WHERE actor = %s
                    """,
                    (actor_id,),
                )
                stats = cur.fetchone()

                # Paginated activity with optional filters
                conditions: list[str] = ["actor = %s"]
                params: list[Any] = [actor_id]

                if action:
                    conditions.append("action = %s")
                    params.append(action)
                if entity_type:
                    conditions.append("entity_type = %s")
                    params.append(entity_type)
                if date_from:
                    conditions.append("happened_at >= %s::timestamptz")
                    params.append(date_from)
                if date_to:
                    conditions.append("happened_at <= %s::timestamptz")
                    params.append(date_to)

                where = " WHERE " + " AND ".join(conditions)

                cur.execute(f"SELECT count(*) FROM provenance_records{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT id, entity_type, entity_id, action, actor_type,
                           source_system, happened_at, detail
                    FROM provenance_records{where}
                    ORDER BY happened_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "actor_id": actor_id,
            "summary": {
                "total_actions": stats["total_actions"],
                "verifications_completed": stats["verifications_completed"],
                "records_created": stats["records_created"],
                "records_updated": stats["records_updated"],
                "records_merged": stats["records_merged"],
                "records_imported": stats["records_imported"],
                "unique_entities_touched": stats["unique_entities_touched"],
                "first_action_at": _iso(stats["first_action_at"]),
                "last_action_at": _iso(stats["last_action_at"]),
            },
            "meta": {"total": total, "limit": limit, "offset": offset},
            "activity": [
                {
                    "id": str(r["id"]),
                    "entity_type": r["entity_type"],
                    "entity_id": str(r["entity_id"]),
                    "action": r["action"],
                    "actor_type": r["actor_type"],
                    "source_system": r["source_system"],
                    "happened_at": _iso(r["happened_at"]),
                    "detail": r["detail"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Actor activity lookup failed for %s", actor_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- 5. Pharmacy Timeline ---


@app.get(
    "/api/pharmacies/{pharmacy_id}/timeline",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def get_pharmacy_timeline(
    pharmacy_id: str,
    event_type: str | None = Query(None, description="Filter by event type"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Unified chronological timeline merging validation, provenance, and operational history."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT id FROM pharmacy_locations WHERE id = %s", (pharmacy_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Pharmacy not found")

                # Build CTE
                cte = """
                    WITH timeline AS (
                        SELECT
                            id,
                            changed_at AS timestamp,
                            'validation_change' AS event_type,
                            'Validation: ' || COALESCE(old_level::text, 'none') || ' → ' || new_level::text AS summary,
                            changed_by AS actor,
                            actor_type,
                            jsonb_build_object(
                                'old_level', old_level::text,
                                'new_level', new_level::text,
                                'evidence_reference', evidence_reference,
                                'source_description', source_description
                            ) AS detail
                        FROM validation_status_history
                        WHERE pharmacy_id = %s

                        UNION ALL

                        SELECT
                            id,
                            happened_at AS timestamp,
                            'provenance_' || action AS event_type,
                            action || ' on ' || entity_type AS summary,
                            actor,
                            actor_type,
                            detail
                        FROM provenance_records
                        WHERE entity_id = %s AND entity_type = 'pharmacy_location'

                        UNION ALL

                        SELECT
                            id,
                            changed_at AS timestamp,
                            'operational_change' AS event_type,
                            'Status: ' || COALESCE(old_status::text, 'none') || ' → ' || new_status::text AS summary,
                            changed_by AS actor,
                            'system' AS actor_type,
                            jsonb_build_object(
                                'old_status', old_status::text,
                                'new_status', new_status::text,
                                'reason', reason,
                                'source_description', source_description
                            ) AS detail
                        FROM operational_status_history
                        WHERE pharmacy_id = %s
                    )
                """
                base_params = [pharmacy_id, pharmacy_id, pharmacy_id]

                event_filter = ""
                filter_params: list[Any] = []
                if event_type:
                    event_filter = " WHERE event_type = %s"
                    filter_params = [event_type]

                cur.execute(
                    f"{cte} SELECT count(*) FROM timeline{event_filter}",
                    base_params + filter_params,
                )
                total = cur.fetchone()["count"]

                cur.execute(
                    f"{cte} SELECT * FROM timeline{event_filter} ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                    base_params + filter_params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "pharmacy_id": pharmacy_id,
            "meta": {"total": total, "limit": limit, "offset": offset},
            "timeline": [
                {
                    "id": str(r["id"]),
                    "timestamp": _iso(r["timestamp"]),
                    "event_type": r["event_type"],
                    "summary": r["summary"],
                    "actor": r["actor"],
                    "actor_type": r["actor_type"],
                    "detail": r["detail"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Timeline failed for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- 6. Audit Stats ---


@app.get(
    "/api/audit/stats",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def audit_stats(
    days: int = Query(30, ge=1, le=365, description="Lookback period in days"),
):
    """System-wide verification metrics and data quality stats."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # 1. Verifications by day
                cur.execute(
                    """
                    SELECT date_trunc('day', changed_at)::date AS day, count(*) AS count
                    FROM validation_status_history
                    WHERE changed_at >= %s
                    GROUP BY day ORDER BY day
                    """,
                    (cutoff,),
                )
                by_day = [{"day": str(r["day"]), "count": r["count"]} for r in cur.fetchall()]

                # 2. Verifications by actor_type
                cur.execute(
                    """
                    SELECT actor_type, count(*) AS count
                    FROM validation_status_history
                    WHERE changed_at >= %s
                    GROUP BY actor_type ORDER BY count DESC
                    """,
                    (cutoff,),
                )
                by_actor_type = {r["actor_type"]: r["count"] for r in cur.fetchall()}

                # 3. Verifications by target level
                cur.execute(
                    """
                    SELECT new_level::text AS target_level, count(*) AS count
                    FROM validation_status_history
                    WHERE changed_at >= %s
                    GROUP BY new_level ORDER BY new_level
                    """,
                    (cutoff,),
                )
                by_level = [
                    {
                        "level": r["target_level"],
                        "label": _level_label(r["target_level"]),
                        "count": r["count"],
                    }
                    for r in cur.fetchall()
                ]

                # 4. Task completion time stats
                cur.execute(
                    """
                    SELECT
                        target_level::text,
                        count(*) AS completed_count,
                        round(avg(EXTRACT(EPOCH FROM (completed_at - created_at)) / 3600)::numeric, 1) AS avg_hours,
                        round(min(EXTRACT(EPOCH FROM (completed_at - created_at)) / 3600)::numeric, 1) AS min_hours,
                        round(max(EXTRACT(EPOCH FROM (completed_at - created_at)) / 3600)::numeric, 1) AS max_hours
                    FROM verification_tasks
                    WHERE status = 'completed' AND completed_at IS NOT NULL AND created_at >= %s
                    GROUP BY target_level ORDER BY target_level
                    """,
                    (cutoff,),
                )
                completion_stats = [
                    {
                        "target_level": r["target_level"],
                        "label": _level_label(r["target_level"]),
                        "completed_count": r["completed_count"],
                        "avg_hours": float(r["avg_hours"]) if r["avg_hours"] else None,
                        "min_hours": float(r["min_hours"]) if r["min_hours"] else None,
                        "max_hours": float(r["max_hours"]) if r["max_hours"] else None,
                    }
                    for r in cur.fetchall()
                ]

                # 5. Evidence types used
                cur.execute(
                    """
                    SELECT
                        evidence_detail->>'evidence_type' AS evidence_type,
                        evidence_detail->>'capture_method' AS capture_method,
                        count(*) AS count
                    FROM validation_status_history
                    WHERE evidence_detail IS NOT NULL AND changed_at >= %s
                    GROUP BY evidence_type, capture_method
                    ORDER BY count DESC
                    """,
                    (cutoff,),
                )
                evidence_types = [
                    {
                        "evidence_type": r["evidence_type"],
                        "capture_method": r["capture_method"],
                        "count": r["count"],
                    }
                    for r in cur.fetchall()
                ]

        return {
            "period_days": days,
            "cutoff_date": _iso(cutoff),
            "verifications_by_day": by_day,
            "verifications_by_actor_type": by_actor_type,
            "verifications_by_target_level": by_level,
            "task_completion_stats": completion_stats,
            "evidence_types_used": evidence_types,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Audit stats failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# FHIR R4 Interoperability endpoints (Phase 2)
# ---------------------------------------------------------------------------

# --- FHIR value-map helpers ---

_FHIR_LOCATION_STATUS = {
    "operational": "active",
    "temporarily_closed": "suspended",
    "permanently_closed": "inactive",
    "unknown": "active",
}

_FHIR_FACILITY_TYPE = {
    "pharmacy": {"code": "PHARM", "display": "Community Pharmacy"},
    "ppmv": {"code": "PPMV", "display": "Patent and Proprietary Medicine Vendor"},
    "hospital_pharmacy": {"code": "HOSPHARM", "display": "Hospital Pharmacy"},
}

_FHIR_VALIDATION_LEVEL = {
    "L0_mapped": {"code": "L0", "display": "Mapped"},
    "L1_contact_confirmed": {"code": "L1", "display": "Contact Confirmed"},
    "L2_evidence_documented": {"code": "L2", "display": "Evidence Documented"},
    "L3_regulator_verified": {"code": "L3", "display": "Regulator/Partner Verified"},
    "L4_high_assurance": {"code": "L4", "display": "High-Assurance"},
}

_FHIR_ORG_ACTIVE = {
    "operational": True,
    "temporarily_closed": True,
    "permanently_closed": False,
    "unknown": True,
}

_FHIR_EXT_ID_SYSTEM = {
    "pcn_premises_id": "https://pcn.gov.ng/premises",
    "nhia_facility_id": "https://nhia.gov.ng/facilities",
    "osm_node_id": "https://www.openstreetmap.org/node",
    "grid3_id": "https://grid3.gov.ng/facilities",
    "google_place_id": "https://maps.google.com/place",
}

_FHIR_CONTACT_SYSTEM = {
    "phone": "phone",
    "email": "email",
    "whatsapp": "other",
}

NPR_BASE = "https://nigeria-pharmacy-registry.internal/fhir"


def _build_fhir_location(
    row: dict,
    contacts: list[dict],
    ext_ids: list[dict],
) -> dict:
    """Build a FHIR R4 Location resource from DB rows."""
    pharmacy_id = str(row["id"])
    op_status = row.get("operational_status") or "unknown"
    fac_type = row.get("facility_type") or "pharmacy"
    val_level = row.get("current_validation_level") or "L0_mapped"

    resource: dict[str, Any] = {
        "resourceType": "Location",
        "id": pharmacy_id,
        "meta": {
            "profile": [f"{NPR_BASE}/StructureDefinition/NPR-Location"],
            "lastUpdated": _iso(row.get("updated_at")),
        },
        "name": row["name"],
        "status": _FHIR_LOCATION_STATUS.get(op_status, "active"),
    }

    # --- type (CodeableConcept) ---
    ft = _FHIR_FACILITY_TYPE.get(fac_type, {"code": fac_type, "display": fac_type})
    resource["type"] = [
        {
            "coding": [
                {
                    "system": f"{NPR_BASE}/CodeSystem/facility-type",
                    "code": ft["code"],
                    "display": ft["display"],
                }
            ],
            "text": ft["display"],
        }
    ]

    # --- address ---
    address: dict[str, Any] = {"use": "work", "country": row.get("country") or "NG"}
    lines = []
    if row.get("address_line_1"):
        lines.append(row["address_line_1"])
    if row.get("address_line_2"):
        lines.append(row["address_line_2"])
    if lines:
        address["line"] = lines
    if row.get("lga"):
        address["district"] = row["lga"]
    if row.get("state"):
        address["state"] = row["state"]
    if row.get("postal_code"):
        address["postalCode"] = row["postal_code"]

    # ward extension
    if row.get("ward"):
        address["extension"] = [
            {
                "url": f"{NPR_BASE}/StructureDefinition/address-ward",
                "valueString": row["ward"],
            }
        ]

    resource["address"] = address

    # --- position (lat/lon) ---
    lat = row.get("latitude")
    lon = row.get("longitude")
    if lat is not None and lon is not None:
        resource["position"] = {
            "latitude": float(lat),
            "longitude": float(lon),
        }

    # --- telecom (contacts) ---
    telecoms = []
    for c in contacts:
        sys = _FHIR_CONTACT_SYSTEM.get(c["contact_type"], "other")
        tc: dict[str, Any] = {
            "system": sys,
            "value": c["contact_value"],
            "use": "work",
        }
        if c.get("is_primary"):
            tc["rank"] = 1
        if sys == "other" and c["contact_type"] == "whatsapp":
            tc["extension"] = [
                {
                    "url": f"{NPR_BASE}/StructureDefinition/telecom-platform",
                    "valueString": "whatsapp",
                }
            ]
        telecoms.append(tc)
    if telecoms:
        resource["telecom"] = telecoms

    # --- identifier (external IDs) ---
    identifiers = [
        {
            "system": f"{NPR_BASE}/pharmacy-id",
            "value": pharmacy_id,
        }
    ]
    for eid in ext_ids:
        sys = _FHIR_EXT_ID_SYSTEM.get(eid["identifier_type"], f"{NPR_BASE}/id/{eid['identifier_type']}")
        identifiers.append(
            {
                "system": sys,
                "value": eid["identifier_value"],
            }
        )
    resource["identifier"] = identifiers

    # --- managingOrganization reference ---
    resource["managingOrganization"] = {
        "reference": f"Organization/org-{pharmacy_id}",
        "display": row["name"],
    }

    # --- extensions ---
    extensions = []

    # validation level extension
    vl = _FHIR_VALIDATION_LEVEL.get(val_level, {"code": val_level, "display": val_level})
    extensions.append(
        {
            "url": f"{NPR_BASE}/StructureDefinition/validation-level",
            "valueCoding": {
                "system": f"{NPR_BASE}/CodeSystem/validation-level",
                "code": vl["code"],
                "display": vl["display"],
            },
        }
    )

    # primary source extension
    if row.get("primary_source"):
        extensions.append(
            {
                "url": f"{NPR_BASE}/StructureDefinition/primary-source",
                "valueString": row["primary_source"],
            }
        )

    # data-absent-reason for unknown status
    if op_status == "unknown":
        extensions.append(
            {
                "url": "http://hl7.org/fhir/StructureDefinition/data-absent-reason",
                "valueCode": "unknown",
            }
        )

    resource["extension"] = extensions

    return resource


def _build_fhir_organization(
    row: dict,
    contacts: list[dict],
    ext_ids: list[dict],
) -> dict:
    """Build a FHIR R4 Organization resource from DB rows."""
    pharmacy_id = str(row["id"])
    op_status = row.get("operational_status") or "unknown"
    fac_type = row.get("facility_type") or "pharmacy"

    resource: dict[str, Any] = {
        "resourceType": "Organization",
        "id": f"org-{pharmacy_id}",
        "meta": {
            "profile": [f"{NPR_BASE}/StructureDefinition/NPR-Organization"],
            "lastUpdated": _iso(row.get("updated_at")),
        },
        "name": row["name"],
        "active": _FHIR_ORG_ACTIVE.get(op_status, True),
    }

    # --- type (dual coding: HL7 + NPR) ---
    ft = _FHIR_FACILITY_TYPE.get(fac_type, {"code": fac_type, "display": fac_type})
    resource["type"] = [
        {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                    "code": "prov",
                    "display": "Healthcare Provider",
                },
                {
                    "system": f"{NPR_BASE}/CodeSystem/facility-type",
                    "code": ft["code"],
                    "display": ft["display"],
                },
            ],
            "text": ft["display"],
        }
    ]

    # --- identifier ---
    identifiers = [
        {
            "system": f"{NPR_BASE}/organization-id",
            "value": f"org-{pharmacy_id}",
        }
    ]
    for eid in ext_ids:
        sys = _FHIR_EXT_ID_SYSTEM.get(eid["identifier_type"], f"{NPR_BASE}/id/{eid['identifier_type']}")
        identifiers.append({"system": sys, "value": eid["identifier_value"]})
    resource["identifier"] = identifiers

    # --- telecom ---
    telecoms = []
    for c in contacts:
        sys = _FHIR_CONTACT_SYSTEM.get(c["contact_type"], "other")
        tc: dict[str, Any] = {"system": sys, "value": c["contact_value"], "use": "work"}
        if c.get("is_primary"):
            tc["rank"] = 1
        telecoms.append(tc)
    if telecoms:
        resource["telecom"] = telecoms

    # --- contact (named contacts) ---
    named_contacts = []
    for c in contacts:
        if c.get("contact_person"):
            named_contacts.append(
                {
                    "name": {"text": c["contact_person"]},
                    "telecom": [
                        {
                            "system": _FHIR_CONTACT_SYSTEM.get(c["contact_type"], "other"),
                            "value": c["contact_value"],
                        }
                    ],
                }
            )
    if named_contacts:
        resource["contact"] = named_contacts

    # --- address ---
    address: dict[str, Any] = {"use": "work", "country": row.get("country") or "NG"}
    lines = []
    if row.get("address_line_1"):
        lines.append(row["address_line_1"])
    if row.get("address_line_2"):
        lines.append(row["address_line_2"])
    if lines:
        address["line"] = lines
    if row.get("lga"):
        address["district"] = row["lga"]
    if row.get("state"):
        address["state"] = row["state"]
    if row.get("postal_code"):
        address["postalCode"] = row["postal_code"]
    resource["address"] = [address]

    return resource


def _fhir_bundle(
    resources: list[dict],
    total: int,
    base_url: str,
    resource_type: str,
) -> dict:
    """Wrap a list of FHIR resources in a searchset Bundle."""
    entries = []
    for r in resources:
        entries.append(
            {
                "fullUrl": f"{base_url}/api/fhir/{resource_type}/{r['id']}",
                "resource": r,
                "search": {"mode": "match"},
            }
        )
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "entry": entries,
    }


# --- FHIR 1. Capability Statement ---


@app.get("/api/fhir/metadata")
async def fhir_metadata(request: Request):
    """FHIR R4 CapabilityStatement — describes what this server supports."""
    base = str(request.base_url).rstrip("/")
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": "2026-02-24",
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "implementation": {
            "description": "Nigeria Pharmacy Registry FHIR R4 read-only endpoint",
            "url": f"{base}/api/fhir",
        },
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "Location",
                        "profile": f"{NPR_BASE}/StructureDefinition/NPR-Location",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "name", "type": "string"},
                            {"name": "address-state", "type": "string"},
                            {"name": "type", "type": "token"},
                            {"name": "status", "type": "token"},
                            {"name": "_count", "type": "number"},
                            {"name": "_offset", "type": "number"},
                        ],
                    },
                    {
                        "type": "Organization",
                        "profile": f"{NPR_BASE}/StructureDefinition/NPR-Organization",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "name", "type": "string"},
                            {"name": "address-state", "type": "string"},
                            {"name": "type", "type": "token"},
                            {"name": "active", "type": "token"},
                            {"name": "_count", "type": "number"},
                            {"name": "_offset", "type": "number"},
                        ],
                    },
                ],
            }
        ],
    }


# --- FHIR DB query helper ---


def _fhir_query_pharmacy(
    pharmacy_id: str,
) -> tuple[dict | None, list[dict], list[dict]]:
    """Fetch a pharmacy row with lat/lon, its contacts, and external IDs."""
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
                return None, [], []

            cur.execute(
                "SELECT * FROM contacts WHERE pharmacy_id = %s ORDER BY is_primary DESC",
                (pharmacy_id,),
            )
            contacts = cur.fetchall()

            cur.execute(
                "SELECT * FROM external_identifiers WHERE pharmacy_id = %s AND is_current = true",
                (pharmacy_id,),
            )
            ext_ids = cur.fetchall()

    return row, list(contacts), list(ext_ids)


# --- FHIR 2. Location read ---


@app.get(
    "/api/fhir/Location/{pharmacy_id}",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_location_read(pharmacy_id: str):
    """Read a single pharmacy as a FHIR R4 Location resource."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        row, contacts, ext_ids = _fhir_query_pharmacy(pharmacy_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail={
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "not-found",
                            "diagnostics": f"Location/{pharmacy_id} not found",
                        }
                    ],
                },
            )
        return _build_fhir_location(row, contacts, ext_ids)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Location read failed for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- FHIR 3. Location search ---


@app.get(
    "/api/fhir/Location",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_location_search(
    request: Request,
    name: str | None = Query(None, description="Name search (partial match)"),
    address_state: str | None = Query(None, alias="address-state", description="State filter"),
    type: str | None = Query(None, description="Facility type code (PHARM, PPMV, HOSPHARM)"),
    status: str | None = Query(None, description="FHIR status (active, suspended, inactive)"),
    _count: int = Query(50, ge=1, le=200, alias="_count"),
    _offset: int = Query(0, ge=0, alias="_offset"),
):
    """Search pharmacies returned as FHIR R4 Location Bundle."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if name:
            conditions.append("pl.name ILIKE %s")
            params.append(f"%{name}%")
        if address_state:
            conditions.append("pl.state ILIKE %s")
            params.append(address_state)
        if type:
            # Map FHIR type code back to DB enum
            code_to_enum = {"PHARM": "pharmacy", "PPMV": "ppmv", "HOSPHARM": "hospital_pharmacy"}
            db_type = code_to_enum.get(type.upper(), type)
            conditions.append("pl.facility_type = %s::facility_type")
            params.append(db_type)
        if status:
            # Map FHIR status back to DB enum
            status_to_enum = {"active": "operational", "suspended": "temporarily_closed", "inactive": "permanently_closed"}
            db_status = status_to_enum.get(status.lower())
            if db_status:
                conditions.append("pl.operational_status = %s::operational_status")
                params.append(db_status)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude
                    FROM pharmacy_locations pl
                    {where}
                    ORDER BY pl.state, pl.name
                    LIMIT %s OFFSET %s
                    """,
                    params + [_count, _offset],
                )
                rows = cur.fetchall()

                # Batch-load contacts and ext IDs for returned pharmacy IDs
                pharmacy_ids = [str(r["id"]) for r in rows]

                contacts_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}
                ext_ids_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}

                if pharmacy_ids:
                    cur.execute(
                        "SELECT * FROM contacts WHERE pharmacy_id = ANY(%s::uuid[]) ORDER BY is_primary DESC",
                        (pharmacy_ids,),
                    )
                    for c in cur.fetchall():
                        contacts_map[str(c["pharmacy_id"])].append(c)

                    cur.execute(
                        "SELECT * FROM external_identifiers WHERE pharmacy_id = ANY(%s::uuid[]) AND is_current = true",
                        (pharmacy_ids,),
                    )
                    for e in cur.fetchall():
                        ext_ids_map[str(e["pharmacy_id"])].append(e)

        resources = []
        for r in rows:
            pid = str(r["id"])
            resources.append(
                _build_fhir_location(r, contacts_map[pid], ext_ids_map[pid])
            )

        base = str(request.base_url).rstrip("/")
        return _fhir_bundle(resources, total, base, "Location")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Location search failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- FHIR 4. Organization read ---


@app.get(
    "/api/fhir/Organization/{org_id}",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_organization_read(org_id: str):
    """Read a single pharmacy organization as a FHIR R4 Organization resource."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Strip "org-" prefix if present
    pharmacy_id = org_id.removeprefix("org-")

    try:
        row, contacts, ext_ids = _fhir_query_pharmacy(pharmacy_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail={
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "not-found",
                            "diagnostics": f"Organization/{org_id} not found",
                        }
                    ],
                },
            )
        return _build_fhir_organization(row, contacts, ext_ids)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Organization read failed for %s", org_id)
        raise HTTPException(status_code=500, detail=str(e))


# --- FHIR 5. Organization search ---


@app.get(
    "/api/fhir/Organization",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_organization_search(
    request: Request,
    name: str | None = Query(None, description="Name search (partial match)"),
    address_state: str | None = Query(None, alias="address-state", description="State filter"),
    type: str | None = Query(None, description="Facility type code (PHARM, PPMV, HOSPHARM)"),
    active: str | None = Query(None, description="true or false"),
    _count: int = Query(50, ge=1, le=200, alias="_count"),
    _offset: int = Query(0, ge=0, alias="_offset"),
):
    """Search pharmacy organizations as FHIR R4 Organization Bundle."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if name:
            conditions.append("pl.name ILIKE %s")
            params.append(f"%{name}%")
        if address_state:
            conditions.append("pl.state ILIKE %s")
            params.append(address_state)
        if type:
            code_to_enum = {"PHARM": "pharmacy", "PPMV": "ppmv", "HOSPHARM": "hospital_pharmacy"}
            db_type = code_to_enum.get(type.upper(), type)
            conditions.append("pl.facility_type = %s::facility_type")
            params.append(db_type)
        if active is not None:
            if active.lower() == "false":
                conditions.append("pl.operational_status = 'permanently_closed'::operational_status")
            elif active.lower() == "true":
                conditions.append("pl.operational_status != 'permanently_closed'::operational_status")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude
                    FROM pharmacy_locations pl
                    {where}
                    ORDER BY pl.state, pl.name
                    LIMIT %s OFFSET %s
                    """,
                    params + [_count, _offset],
                )
                rows = cur.fetchall()

                pharmacy_ids = [str(r["id"]) for r in rows]
                contacts_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}
                ext_ids_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}

                if pharmacy_ids:
                    cur.execute(
                        "SELECT * FROM contacts WHERE pharmacy_id = ANY(%s::uuid[]) ORDER BY is_primary DESC",
                        (pharmacy_ids,),
                    )
                    for c in cur.fetchall():
                        contacts_map[str(c["pharmacy_id"])].append(c)

                    cur.execute(
                        "SELECT * FROM external_identifiers WHERE pharmacy_id = ANY(%s::uuid[]) AND is_current = true",
                        (pharmacy_ids,),
                    )
                    for e in cur.fetchall():
                        ext_ids_map[str(e["pharmacy_id"])].append(e)

        resources = []
        for r in rows:
            pid = str(r["id"])
            resources.append(
                _build_fhir_organization(r, contacts_map[pid], ext_ids_map[pid])
            )

        base = str(request.base_url).rstrip("/")
        return _fhir_bundle(resources, total, base, "Organization")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Organization search failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Bulk Export endpoints (Phase 2)
# ---------------------------------------------------------------------------

_EXPORT_CSV_COLUMNS = [
    "pharmacy_id",
    "name",
    "facility_type",
    "operational_status",
    "validation_level",
    "validation_label",
    "address_line_1",
    "address_line_2",
    "ward",
    "lga",
    "state",
    "country",
    "postal_code",
    "latitude",
    "longitude",
    "phone",
    "email",
    "primary_source",
    "external_ids",
    "created_at",
    "updated_at",
]


def _export_query_pharmacies(
    state: str | None,
    lga: str | None,
    facility_type: str | None,
    validation_level: str | None,
    source: str | None,
) -> tuple[str, list[Any]]:
    """Build the filtered export query.  Returns (sql, params)."""
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
    if validation_level:
        conditions.append("pl.current_validation_level = %s::validation_level")
        params.append(validation_level)
    if source:
        conditions.append("pl.primary_source = %s")
        params.append(source)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            pl.id,
            pl.name,
            pl.facility_type::text,
            pl.operational_status::text,
            pl.current_validation_level::text,
            pl.address_line_1,
            pl.address_line_2,
            pl.ward,
            pl.lga,
            pl.state,
            pl.country,
            pl.postal_code,
            ST_Y(pl.geolocation::geometry) AS latitude,
            ST_X(pl.geolocation::geometry) AS longitude,
            pl.primary_source,
            pl.created_at,
            pl.updated_at,
            (
                SELECT string_agg(c.contact_value, '; ')
                FROM contacts c
                WHERE c.pharmacy_id = pl.id AND c.contact_type = 'phone'
            ) AS phone,
            (
                SELECT string_agg(c.contact_value, '; ')
                FROM contacts c
                WHERE c.pharmacy_id = pl.id AND c.contact_type = 'email'
            ) AS email,
            (
                SELECT string_agg(ei.identifier_type || ':' || ei.identifier_value, '; ')
                FROM external_identifiers ei
                WHERE ei.pharmacy_id = pl.id AND ei.is_current = true
            ) AS external_ids
        FROM pharmacy_locations pl
        {where}
        ORDER BY pl.state, pl.lga, pl.name
    """
    return sql, params


def _row_to_export_dict(row: dict) -> dict:
    """Normalise a DB row into the flat export dict."""
    return {
        "pharmacy_id": str(row["id"]),
        "name": row["name"],
        "facility_type": row["facility_type"],
        "operational_status": row["operational_status"],
        "validation_level": row["current_validation_level"],
        "validation_label": _level_label(row["current_validation_level"]),
        "address_line_1": row.get("address_line_1") or "",
        "address_line_2": row.get("address_line_2") or "",
        "ward": row.get("ward") or "",
        "lga": row.get("lga") or "",
        "state": row.get("state") or "",
        "country": row.get("country") or "NG",
        "postal_code": row.get("postal_code") or "",
        "latitude": str(row["latitude"]) if row.get("latitude") is not None else "",
        "longitude": str(row["longitude"]) if row.get("longitude") is not None else "",
        "phone": row.get("phone") or "",
        "email": row.get("email") or "",
        "primary_source": row.get("primary_source") or "",
        "external_ids": row.get("external_ids") or "",
        "created_at": _iso(row.get("created_at")) or "",
        "updated_at": _iso(row.get("updated_at")) or "",
    }


# --- Export 1. CSV ---


@app.get(
    "/api/export/pharmacies.csv",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def export_pharmacies_csv(
    state: str | None = Query(None),
    lga: str | None = Query(None),
    facility_type: str | None = Query(None),
    validation_level: str | None = Query(None),
    source: str | None = Query(None),
):
    """Bulk export pharmacies as CSV.  Streams the result for large datasets."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        sql, params = _export_query_pharmacies(state, lga, facility_type, validation_level, source)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        def generate_csv():
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_EXPORT_CSV_COLUMNS)
            writer.writeheader()
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

            for row in rows:
                writer.writerow(_row_to_export_dict(row))
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"npr_pharmacies_{ts}.csv"

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("CSV export failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- Export 2. JSON ---


@app.get(
    "/api/export/pharmacies.json",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def export_pharmacies_json(
    state: str | None = Query(None),
    lga: str | None = Query(None),
    facility_type: str | None = Query(None),
    validation_level: str | None = Query(None),
    source: str | None = Query(None),
):
    """Bulk export pharmacies as a JSON array."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        sql, params = _export_query_pharmacies(state, lga, facility_type, validation_level, source)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        data = [_row_to_export_dict(r) for r in rows]

        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"npr_pharmacies_{ts}.json"

        content = json.dumps({"export_date": _iso(datetime.now(timezone.utc)), "count": len(data), "pharmacies": data}, default=str)

        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("JSON export failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- Export 3. FHIR ndjson (Bulk Data) ---


@app.get(
    "/api/export/fhir/Location.ndjson",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def export_fhir_ndjson(
    state: str | None = Query(None),
    lga: str | None = Query(None),
    facility_type: str | None = Query(None),
    validation_level: str | None = Query(None),
):
    """FHIR Bulk Data export — one Location resource per line (ndjson)."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
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
        if validation_level:
            conditions.append("pl.current_validation_level = %s::validation_level")
            params.append(validation_level)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude
                    FROM pharmacy_locations pl
                    {where}
                    ORDER BY pl.state, pl.name
                    """,
                    params,
                )
                rows = cur.fetchall()

                # Batch contacts and ext IDs
                pharmacy_ids = [str(r["id"]) for r in rows]
                contacts_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}
                ext_ids_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}

                if pharmacy_ids:
                    cur.execute(
                        "SELECT * FROM contacts WHERE pharmacy_id = ANY(%s::uuid[]) ORDER BY is_primary DESC",
                        (pharmacy_ids,),
                    )
                    for c in cur.fetchall():
                        contacts_map[str(c["pharmacy_id"])].append(c)

                    cur.execute(
                        "SELECT * FROM external_identifiers WHERE pharmacy_id = ANY(%s::uuid[]) AND is_current = true",
                        (pharmacy_ids,),
                    )
                    for e in cur.fetchall():
                        ext_ids_map[str(e["pharmacy_id"])].append(e)

        def generate_ndjson():
            for r in rows:
                pid = str(r["id"])
                loc = _build_fhir_location(r, contacts_map[pid], ext_ids_map[pid])
                yield json.dumps(loc, default=str) + "\n"

        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"npr_fhir_location_{ts}.ndjson"

        return StreamingResponse(
            generate_ndjson(),
            media_type="application/ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR ndjson export failed")
        raise HTTPException(status_code=500, detail=str(e))


# --- Export 4. Summary/metadata ---


@app.get(
    "/api/export/summary",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def export_summary(
    state: str | None = Query(None),
    lga: str | None = Query(None),
    facility_type: str | None = Query(None),
    validation_level: str | None = Query(None),
    source: str | None = Query(None),
):
    """Preview what an export would contain — counts and breakdowns without downloading data."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
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
        if validation_level:
            conditions.append("pl.current_validation_level = %s::validation_level")
            params.append(validation_level)
        if source:
            conditions.append("pl.primary_source = %s")
            params.append(source)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"SELECT state, count(*) AS cnt FROM pharmacy_locations pl{where} GROUP BY state ORDER BY cnt DESC",
                    params,
                )
                by_state = {r["state"]: r["cnt"] for r in cur.fetchall()}

                cur.execute(
                    f"SELECT facility_type::text, count(*) AS cnt FROM pharmacy_locations pl{where} GROUP BY facility_type ORDER BY cnt DESC",
                    params,
                )
                by_type = {r["facility_type"]: r["cnt"] for r in cur.fetchall()}

                cur.execute(
                    f"SELECT current_validation_level::text AS lvl, count(*) AS cnt FROM pharmacy_locations pl{where} GROUP BY lvl ORDER BY lvl",
                    params,
                )
                by_level = [
                    {"level": r["lvl"], "label": _level_label(r["lvl"]), "count": r["cnt"]}
                    for r in cur.fetchall()
                ]

        return {
            "filters_applied": {
                "state": state,
                "lga": lga,
                "facility_type": facility_type,
                "validation_level": validation_level,
                "source": source,
            },
            "total_records": total,
            "by_state": by_state,
            "by_facility_type": by_type,
            "by_validation_level": by_level,
            "available_formats": [
                {"format": "csv", "url": "/api/export/pharmacies.csv"},
                {"format": "json", "url": "/api/export/pharmacies.json"},
                {"format": "fhir_ndjson", "url": "/api/export/fhir/Location.ndjson"},
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Export summary failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Monitoring / Health endpoints (Phase 2)
# ---------------------------------------------------------------------------



@app.get(
    "/api/health/detailed",
    dependencies=[Depends(require_tier("admin"))],
)
async def health_detailed():
    """Detailed system health — admin only.  Includes table counts, DB size, data quality."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        import time

        t0 = time.monotonic()

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # DB connectivity
                cur.execute("SELECT 1")
                db_latency_ms = round((time.monotonic() - t0) * 1000, 1)

                # Database size
                cur.execute("SELECT pg_database_size(current_database()) AS db_size")
                db_size_bytes = cur.fetchone()["db_size"]

                # Table row counts
                tables = [
                    "pharmacy_locations",
                    "contacts",
                    "external_identifiers",
                    "validation_status_history",
                    "provenance_records",
                    "audit_log",
                    "operational_status_history",
                    "verification_tasks",
                    "raw_ingested_records",
                ]
                table_counts = {}
                for t in tables:
                    try:
                        cur.execute(f"SELECT count(*) FROM {t}")  # noqa: S608
                        table_counts[t] = cur.fetchone()["count"]
                    except Exception:
                        table_counts[t] = None
                        conn.rollback()

                # Data quality metrics
                cur.execute("""
                    SELECT
                        count(*) AS total,
                        count(*) FILTER (WHERE geolocation IS NOT NULL) AS has_geo,
                        count(*) FILTER (WHERE address_line_1 IS NOT NULL AND address_line_1 != '') AS has_address,
                        count(*) FILTER (WHERE current_validation_level != 'L0_mapped') AS above_l0
                    FROM pharmacy_locations
                """)
                quality = cur.fetchone()

                cur.execute("""
                    SELECT count(DISTINCT pharmacy_id) AS pharmacies_with_contact
                    FROM contacts
                """)
                contact_coverage = cur.fetchone()["pharmacies_with_contact"]

                cur.execute("""
                    SELECT count(DISTINCT pharmacy_id) AS pharmacies_with_ext_id
                    FROM external_identifiers WHERE is_current = true
                """)
                ext_id_coverage = cur.fetchone()["pharmacies_with_ext_id"]

                # Verification pipeline stats
                cur.execute("""
                    SELECT
                        count(*) AS total_tasks,
                        count(*) FILTER (WHERE status = 'pending') AS pending,
                        count(*) FILTER (WHERE status = 'assigned') AS assigned,
                        count(*) FILTER (WHERE status = 'completed') AS completed,
                        count(*) FILTER (WHERE status = 'skipped') AS skipped
                    FROM verification_tasks
                """)
                pipeline = cur.fetchone()

        total = quality["total"] or 1  # avoid div-by-zero

        uptime_seconds = (datetime.now(timezone.utc) - _SERVER_STARTED_AT).total_seconds()

        return {
            "status": "healthy",
            "version": app.version,
            "started_at": _iso(_SERVER_STARTED_AT),
            "uptime_seconds": round(uptime_seconds),
            "database": {
                "status": "up",
                "latency_ms": db_latency_ms,
                "size_mb": round(db_size_bytes / (1024 * 1024), 1),
            },
            "table_counts": table_counts,
            "data_quality": {
                "total_pharmacies": quality["total"],
                "geocoded": quality["has_geo"],
                "geocoded_pct": round(quality["has_geo"] / total * 100, 1),
                "has_address": quality["has_address"],
                "has_address_pct": round(quality["has_address"] / total * 100, 1),
                "has_contact": contact_coverage,
                "has_contact_pct": round(contact_coverage / total * 100, 1),
                "has_external_id": ext_id_coverage,
                "has_external_id_pct": round(ext_id_coverage / total * 100, 1),
                "above_l0": quality["above_l0"],
                "above_l0_pct": round(quality["above_l0"] / total * 100, 1),
            },
            "verification_pipeline": {
                "total_tasks": pipeline["total_tasks"],
                "pending": pipeline["pending"],
                "assigned": pipeline["assigned"],
                "completed": pipeline["completed"],
                "skipped": pipeline["skipped"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Detailed health check failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/health/data-quality",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def data_quality_report():
    """Data quality breakdown by state — completeness metrics for each field."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        pl.state,
                        count(*) AS total,
                        count(*) FILTER (WHERE pl.geolocation IS NOT NULL) AS has_geo,
                        count(*) FILTER (WHERE pl.address_line_1 IS NOT NULL AND pl.address_line_1 != '') AS has_address,
                        count(*) FILTER (WHERE c.pharmacy_id IS NOT NULL) AS has_contact,
                        count(*) FILTER (WHERE pl.current_validation_level != 'L0_mapped') AS above_l0
                    FROM pharmacy_locations pl
                    LEFT JOIN (
                        SELECT DISTINCT pharmacy_id FROM contacts
                    ) c ON c.pharmacy_id = pl.id
                    GROUP BY pl.state
                    ORDER BY pl.state
                """)
                rows = cur.fetchall()

                # Overall totals
                cur.execute("""
                    SELECT
                        count(*) AS total,
                        count(*) FILTER (WHERE geolocation IS NOT NULL) AS has_geo,
                        count(*) FILTER (WHERE address_line_1 IS NOT NULL AND address_line_1 != '') AS has_address,
                        count(*) FILTER (WHERE current_validation_level != 'L0_mapped') AS above_l0
                    FROM pharmacy_locations
                """)
                totals = cur.fetchone()

        by_state = []
        for r in rows:
            t = r["total"] or 1
            by_state.append({
                "state": r["state"],
                "total": r["total"],
                "geocoded_pct": round(r["has_geo"] / t * 100, 1),
                "has_address_pct": round(r["has_address"] / t * 100, 1),
                "has_contact_pct": round(r["has_contact"] / t * 100, 1),
                "above_l0_pct": round(r["above_l0"] / t * 100, 1),
            })

        grand_total = totals["total"] or 1
        return {
            "overall": {
                "total_pharmacies": totals["total"],
                "geocoded_pct": round(totals["has_geo"] / grand_total * 100, 1),
                "has_address_pct": round(totals["has_address"] / grand_total * 100, 1),
                "above_l0_pct": round(totals["above_l0"] / grand_total * 100, 1),
            },
            "by_state": by_state,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Data quality report failed")
        raise HTTPException(status_code=500, detail=str(e))
