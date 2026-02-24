"""Health check and monitoring endpoints."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse

from .. import db
from ..auth import require_tier
from ..db import extras
from ..helpers import get_records, iso, level_label

logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/api/health")
async def health(request: Request):
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
            count = len(get_records())
            mode = "json_fallback"
    else:
        count = len(get_records())

    server_started_at = request.app.state.server_started_at
    uptime_seconds = round((datetime.now(timezone.utc) - server_started_at).total_seconds())

    overall_status = "healthy" if db_ok else "degraded"
    http_status = 200 if db_ok else 503

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall_status,
            "mode": mode,
            "record_count": count,
            "version": request.app.version,
            "database_connected": db_ok,
            "auth_enabled": True,
            "started_at": iso(server_started_at),
            "uptime_seconds": uptime_seconds,
            "checks": {
                "database": {
                    "status": "up" if db_ok else "down",
                    "latency_ms": db_latency_ms,
                },
            },
        },
    )


@router.get("/")
async def index():
    """Serve the dashboard HTML."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get(
    "/api/health/detailed",
    dependencies=[Depends(require_tier("admin"))],
)
async def health_detailed(request: Request):
    """Detailed system health — admin only.  Includes table counts, DB size, data quality."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        import time

        t0 = time.monotonic()

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT 1")
                db_latency_ms = round((time.monotonic() - t0) * 1000, 1)

                cur.execute("SELECT pg_database_size(current_database()) AS db_size")
                db_size_bytes = cur.fetchone()["db_size"]

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

        total = quality["total"] or 1

        server_started_at = request.app.state.server_started_at
        uptime_seconds = (datetime.now(timezone.utc) - server_started_at).total_seconds()

        return {
            "status": "healthy",
            "version": request.app.version,
            "started_at": iso(server_started_at),
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


@router.get(
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
