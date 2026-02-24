"""Bulk export endpoints — CSV, JSON, FHIR ndjson, and summary."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import db
from ..auth import require_tier
from ..db import extras
from ..helpers import iso, level_label
from .fhir import build_fhir_location

logger = logging.getLogger(__name__)

router = APIRouter()

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
        "validation_label": level_label(row["current_validation_level"]),
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
        "created_at": iso(row.get("created_at")) or "",
        "updated_at": iso(row.get("updated_at")) or "",
    }


@router.get(
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


@router.get(
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

        content = json.dumps({"export_date": iso(datetime.now(timezone.utc)), "count": len(data), "pharmacies": data}, default=str)

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


@router.get(
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
                loc = build_fhir_location(r, contacts_map[pid], ext_ids_map[pid])
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


@router.get(
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
                    {"level": r["lvl"], "label": level_label(r["lvl"]), "count": r["cnt"]}
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
