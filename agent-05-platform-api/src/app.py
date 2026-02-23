#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Dashboard API

Lightweight FastAPI server that loads canonical JSON records from the
output/ directory and serves them via a REST API + static HTML dashboard.

Usage:
    uvicorn agent-05-platform-api.src.app:app --reload --port 8000
"""

from __future__ import annotations

import glob
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"

_RECORDS: list[dict[str, Any]] = []
_INDEX: dict[str, dict[str, Any]] = {}


def load_all_canonical() -> None:
    """Load all canonical_*.json files from the output directory tree."""
    global _RECORDS, _INDEX  # noqa: PLW0603

    records = []
    pattern = str(OUTPUT_DIR / "**" / "canonical_*.json")
    files = glob.glob(pattern, recursive=True)

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            batch = json.load(f)
        if isinstance(batch, list):
            records.extend(batch)
        logger.info("Loaded %d records from %s", len(batch) if isinstance(batch, list) else 0, fpath)

    # Deduplicate by pharmacy_id (in case of overlapping batches)
    seen = set()
    unique = []
    for r in records:
        pid = r.get("pharmacy_id")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(r)

    _RECORDS = unique
    _INDEX = {r["pharmacy_id"]: r for r in _RECORDS}
    logger.info("Total unique records loaded: %d", len(_RECORDS))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nigeria Pharmacy Registry",
    version="0.1.0",
    description="Dashboard API for exploring ingested pharmacy records",
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
    load_all_canonical()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    """Serve the dashboard HTML."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/pharmacies")
async def list_pharmacies(
    state: str | None = Query(None, description="Filter by state name"),
    lga: str | None = Query(None, description="Filter by LGA"),
    facility_type: str | None = Query(None, description="Filter by facility type"),
    source_id: str | None = Query(None, description="Filter by data source"),
    q: str | None = Query(None, description="Search facility name (case-insensitive)"),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List pharmacy records with optional filters."""
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


@app.get("/api/pharmacies/{pharmacy_id}")
async def get_pharmacy(pharmacy_id: str) -> dict[str, Any]:
    """Get a single pharmacy record by ID."""
    record = _INDEX.get(pharmacy_id)
    if not record:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
    return {"data": record}


@app.get("/api/stats")
async def get_stats() -> dict[str, Any]:
    """Summary statistics for the registry."""
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
