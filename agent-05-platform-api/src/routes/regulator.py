"""Regulator sync endpoints — batch upload, matching, and L3 promotion pipeline."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from .. import db
from ..auth import ANONYMOUS, AuthContext, require_tier
from ..db import extras
from ..helpers import iso
from ..models import RegulatorBatchApproveRequest, RegulatorReviewRequest
from ..regulator_sync import (
    REGULATOR_ID_TYPE_MAP,
    VALID_SOURCES,
    approve_auto_matches,
    compute_file_hash,
    create_batch,
    match_staged_records,
    parse_csv,
    review_single_record,
    stage_records,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/regulator/upload — Upload CSV batch
# ---------------------------------------------------------------------------


@router.post(
    "/api/regulator/upload",
    dependencies=[Depends(require_tier("admin"))],
)
async def upload_regulator_batch(
    request: Request,
    file: UploadFile = File(...),
    regulator_source: str = Query(..., description="pcn, nhia, or nafdac"),
    extract_date: str | None = Query(None, description="YYYY-MM-DD"),
    max_records: int = Query(5000, ge=1, le=25000),
):
    """Upload a regulator CSV file for processing. Requires admin tier."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    if regulator_source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid regulator_source '{regulator_source}'. Valid: {sorted(VALID_SOURCES)}",
        )

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)
    actor_id = auth.actor_id

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file")

    file_hash = compute_file_hash(file_content)
    records = parse_csv(file_content, regulator_source, max_records)

    batch_id = create_batch(
        regulator_source=regulator_source,
        file_name=file.filename or "upload.csv",
        file_hash=file_hash,
        extract_date=extract_date,
        record_count=len(records),
        actor_id=actor_id,
    )

    stage_records(batch_id, records, regulator_source, actor_id)

    try:
        match_summary = match_staged_records(batch_id, regulator_source)
    except Exception as e:
        logger.error("Matching failed for batch %s: %s", batch_id, e)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE regulator_sync_batches SET status = 'failed', error_message = %s WHERE id = %s",
                    (str(e), batch_id),
                )
        raise HTTPException(status_code=500, detail=f"Matching pipeline failed: {e}")

    return {
        "success": True,
        "batch_id": batch_id,
        "regulator_source": regulator_source,
        "file_name": file.filename,
        "record_count": len(records),
        "match_summary": match_summary,
        "message": f"Batch processed. {match_summary.get('auto_matched', 0)} auto-matches ready for approval.",
    }


# ---------------------------------------------------------------------------
# GET /api/regulator/batches — List batches
# ---------------------------------------------------------------------------


@router.get(
    "/api/regulator/batches",
    dependencies=[Depends(require_tier("admin"))],
)
async def list_batches(
    request: Request,
    regulator_source: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List regulator sync batches with aggregate statistics. Requires admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    conditions = []
    params: list[Any] = []

    if regulator_source:
        conditions.append("regulator_source = %s::regulator_source_type")
        params.append(regulator_source)
    if status:
        conditions.append("status = %s")
        params.append(status)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(f"SELECT count(*) AS total FROM regulator_sync_batches {where}", params)
            total = cur.fetchone()["total"]

            cur.execute(
                f"""
                SELECT id, regulator_source, file_name, extract_date,
                       record_count, auto_matched_count, probable_count,
                       no_match_count, promoted_count, status, error_message,
                       created_at, created_by
                FROM regulator_sync_batches
                {where}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            batches = cur.fetchall()

    return {
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": [
            {
                **{k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in b.items()},
                "id": str(b["id"]),
            }
            for b in batches
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/regulator/batches/{batch_id} — Batch detail
# ---------------------------------------------------------------------------


@router.get(
    "/api/regulator/batches/{batch_id}",
    dependencies=[Depends(require_tier("admin"))],
)
async def get_batch_detail(
    request: Request,
    batch_id: str,
    match_status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get batch detail with paginated staging records. Requires admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, regulator_source, file_name, extract_date,
                       record_count, auto_matched_count, probable_count,
                       no_match_count, promoted_count, status, error_message,
                       created_at, created_by
                FROM regulator_sync_batches
                WHERE id = %s::uuid
                """,
                (batch_id,),
            )
            batch = cur.fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")

            conditions = ["batch_id = %s::uuid"]
            params: list[Any] = [batch_id]

            if match_status:
                conditions.append("match_status = %s::regulator_match_status")
                params.append(match_status)

            where = "WHERE " + " AND ".join(conditions)

            cur.execute(
                f"SELECT count(*) AS total FROM regulator_staging_records {where}",
                params,
            )
            total = cur.fetchone()["total"]

            cur.execute(
                f"""
                SELECT id, raw_name, raw_registration_id, raw_state, raw_lga,
                       match_status, matched_pharmacy_id, match_score,
                       promoted, reviewed_by, review_notes, created_at
                FROM regulator_staging_records
                {where}
                ORDER BY match_score DESC NULLS LAST, id
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            records = cur.fetchall()

    return {
        "batch": {
            **{k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in batch.items()},
            "id": str(batch["id"]),
        },
        "records": {
            "meta": {"total": total, "limit": limit, "offset": offset},
            "data": [
                {
                    **{k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in r.items()},
                    "id": str(r["id"]),
                    "matched_pharmacy_id": str(r["matched_pharmacy_id"]) if r["matched_pharmacy_id"] else None,
                }
                for r in records
            ],
        },
    }


# ---------------------------------------------------------------------------
# POST /api/regulator/batches/{batch_id}/approve — Bulk approve
# ---------------------------------------------------------------------------


@router.post(
    "/api/regulator/batches/{batch_id}/approve",
    dependencies=[Depends(require_tier("admin"))],
)
async def approve_batch(
    request: Request,
    batch_id: str,
    req: RegulatorBatchApproveRequest,
):
    """Approve all auto-matched records in a batch for L3 promotion. Requires admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    result = approve_auto_matches(
        batch_id=batch_id,
        actor_id=auth.actor_id,
        dry_run=req.dry_run,
    )

    return {"success": True, "batch_id": batch_id, **result}


# ---------------------------------------------------------------------------
# POST /api/regulator/batches/{batch_id}/review/{record_id} — Manual review
# ---------------------------------------------------------------------------


@router.post(
    "/api/regulator/batches/{batch_id}/review/{record_id}",
    dependencies=[Depends(require_tier("admin"))],
)
async def review_record(
    request: Request,
    batch_id: str,
    record_id: str,
    req: RegulatorReviewRequest,
):
    """Manual review of a probable_match or no_match record. Requires admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    result = review_single_record(
        record_id=record_id,
        action=req.action,
        actor_id=auth.actor_id,
        matched_pharmacy_id=req.matched_pharmacy_id,
        notes=req.notes,
    )

    return result


# ---------------------------------------------------------------------------
# GET /api/regulator/unmatched — View unmatched records
# ---------------------------------------------------------------------------


@router.get(
    "/api/regulator/unmatched",
    dependencies=[Depends(require_tier("admin"))],
)
async def list_unmatched(
    request: Request,
    regulator_source: str | None = Query(None),
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """View unmatched regulator records across all batches. Requires admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    conditions = ["r.match_status = 'no_match'"]
    params: list[Any] = []

    if regulator_source:
        conditions.append("r.regulator_source = %s::regulator_source_type")
        params.append(regulator_source)
    if state:
        conditions.append("lower(r.raw_state) = lower(%s)")
        params.append(state)

    where = "WHERE " + " AND ".join(conditions)

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT count(*) AS total FROM regulator_staging_records r {where}",
                params,
            )
            total = cur.fetchone()["total"]

            cur.execute(
                f"""
                SELECT r.id, r.batch_id, r.regulator_source,
                       r.raw_name, r.raw_registration_id, r.raw_state,
                       r.raw_lga, r.raw_address, r.raw_phone,
                       r.raw_facility_category, r.match_score, r.created_at,
                       b.file_name AS batch_file_name
                FROM regulator_staging_records r
                JOIN regulator_sync_batches b ON b.id = r.batch_id
                {where}
                ORDER BY r.regulator_source, r.raw_state, r.raw_name
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            records = cur.fetchall()

    return {
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": [
            {
                **{k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in r.items()},
                "id": str(r["id"]),
                "batch_id": str(r["batch_id"]),
            }
            for r in records
        ],
    }
