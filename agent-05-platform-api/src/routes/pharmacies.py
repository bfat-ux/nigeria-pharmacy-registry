"""Pharmacy read endpoints (list, detail, nearby, stats, geojson)."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .. import db
from ..auth import ANONYMOUS, AuthContext, redact_contacts_in_response
from ..db import extras
from ..helpers import (
    db_get_geojson,
    db_get_pharmacy,
    db_get_stats,
    db_list_pharmacies,
    db_row_to_pharmacy,
    get_index,
    get_records,
    level_label,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/pharmacies")
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

    result = db_list_pharmacies(state, lga, facility_type, source_id, q, limit, offset)
    if result is not None:
        redact_contacts_in_response(result.get("data", []), auth)
        return result

    # JSON fallback
    results = get_records()

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
    page = [dict(r) for r in results[offset : offset + limit]]
    redact_contacts_in_response(page, auth)

    return {
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": page,
    }


@router.get("/api/pharmacies/nearby")
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


@router.get("/api/pharmacies/{pharmacy_id}")
async def get_pharmacy(request: Request, pharmacy_id: str) -> dict[str, Any]:
    """Get a single pharmacy record by ID. Contacts redacted for public tier."""
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    result = db_get_pharmacy(pharmacy_id)
    if result is not None:
        if result.get("data") is None:
            raise HTTPException(status_code=404, detail="Pharmacy not found")
        redact_contacts_in_response(result.get("data"), auth)
        return result

    # JSON fallback
    record = get_index().get(pharmacy_id)
    if not record:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
    data = dict(record)
    redact_contacts_in_response(data, auth)
    return {"data": data}


@router.get("/api/stats")
async def get_stats() -> dict[str, Any]:
    """Summary statistics for the registry."""
    result = db_get_stats()
    if result is not None:
        return result

    # JSON fallback
    records = get_records()
    states = Counter(r.get("state") or "Unknown" for r in records)
    sources = Counter(r.get("source_id") or "Unknown" for r in records)
    types = Counter(r.get("facility_type") or "Unknown" for r in records)
    validation = Counter(r.get("validation_label") or "Unknown" for r in records)

    return {
        "total": len(records),
        "by_state": dict(states.most_common()),
        "by_source": dict(sources.most_common()),
        "by_facility_type": dict(types.most_common()),
        "by_validation_level": dict(validation.most_common()),
        "states_covered": len(states),
    }


@router.get("/api/geojson")
async def get_geojson(
    request: Request,
    state: str | None = Query(None),
    source_id: str | None = Query(None),
    facility_type: str | None = Query(None),
) -> dict[str, Any]:
    """Return records as GeoJSON FeatureCollection for map rendering. Contacts redacted for public."""
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    result = db_get_geojson(state, source_id, facility_type)
    if result is not None:
        redact_contacts_in_response(result.get("features", []), auth)
        return result

    # JSON fallback
    results = get_records()

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
