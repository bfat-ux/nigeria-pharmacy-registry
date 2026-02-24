"""Audit, provenance, evidence, and timeline endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..auth import require_tier
from ..db import extras
from ..helpers import iso, level_label, parse_date_param

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
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
                "validation_label": level_label(r["new_level"]),
                "evidence_type": ed.get("evidence_type"),
                "capture_method": ed.get("capture_method"),
                "actor": r["changed_by"],
                "actor_type": r["actor_type"],
                "evidence_reference": r["evidence_reference"],
                "source_description": r["source_description"],
                "timestamp": iso(r["changed_at"]),
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


@router.get(
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

    parse_date_param(date_from, "date_from")
    parse_date_param(date_to, "date_to")

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
                    "happened_at": iso(r["happened_at"]),
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


@router.get(
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

    parse_date_param(date_from, "date_from")
    parse_date_param(date_to, "date_to")

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
                    "happened_at": iso(r["happened_at"]),
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Audit log search failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
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

    parse_date_param(date_from, "date_from")
    parse_date_param(date_to, "date_to")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
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
                "first_action_at": iso(stats["first_action_at"]),
                "last_action_at": iso(stats["last_action_at"]),
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
                    "happened_at": iso(r["happened_at"]),
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


@router.get(
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
                    "timestamp": iso(r["timestamp"]),
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


@router.get(
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
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
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
                        "label": level_label(r["target_level"]),
                        "count": r["count"],
                    }
                    for r in cur.fetchall()
                ]

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
                        "label": level_label(r["target_level"]),
                        "completed_count": r["completed_count"],
                        "avg_hours": float(r["avg_hours"]) if r["avg_hours"] else None,
                        "min_hours": float(r["min_hours"]) if r["min_hours"] else None,
                        "max_hours": float(r["max_hours"]) if r["max_hours"] else None,
                    }
                    for r in cur.fetchall()
                ]

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
            "cutoff_date": iso(cutoff),
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
