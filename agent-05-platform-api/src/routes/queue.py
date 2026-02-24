"""Verification queue endpoints — task management for the validation pipeline."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import db
from ..auth import ANONYMOUS, AuthContext, require_tier
from ..db import extras
from ..helpers import (
    DOWNGRADE_MAP,
    GRACE_PERIOD_DAYS,
    LEVEL_INDEX,
    REVERIFICATION_INTERVALS,
    VALIDATION_LEVELS,
    iso,
    level_label,
)
from ..models import (
    ReverificationGenerateRequest,
    TaskGenerateRequest,
    TaskSkipRequest,
    VerifyRequest,
)
from .verification import execute_downgrade, execute_verification

logger = logging.getLogger(__name__)

router = APIRouter()

# Map target level → task_type enum value
_TASK_TYPE_MAP = {
    "L1_contact_confirmed": "verify_L1",
    "L2_evidence_documented": "verify_L2",
    "L3_regulator_verified": "verify_L3",
    "L4_high_assurance": "verify_L4",
}

# Map level → reverify task_type
_REVERIFY_TASK_TYPE_MAP = {
    "L1_contact_confirmed": "reverify_L1",
    "L2_evidence_documented": "reverify_L2",
    "L3_regulator_verified": "reverify_L3",
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
        "target_label": level_label(row.get("target_level")),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "assigned_to": row.get("assigned_to"),
        "assigned_at": iso(row.get("assigned_at")),
        "completed_at": iso(row.get("completed_at")),
        "due_date": str(row["due_date"]) if row.get("due_date") else None,
        "attempt_count": row.get("attempt_count"),
        "max_attempts": row.get("max_attempts"),
        "notes": row.get("notes"),
        "result_detail": row.get("result_detail"),
        "created_at": iso(row.get("created_at")),
        "updated_at": iso(row.get("updated_at")),
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


@router.get(
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


@router.get(
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
                cur.execute(
                    "SELECT status::text, count(*) AS cnt FROM verification_tasks GROUP BY status"
                )
                by_status = {r["status"]: r["cnt"] for r in cur.fetchall()}

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


@router.get(
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


@router.post(
    "/api/queue/generate",
    dependencies=[Depends(require_tier("admin"))],
)
async def generate_queue(request: Request, req: TaskGenerateRequest):
    """Batch-generate verification tasks for pharmacies at the prerequisite level. Requires: admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    target = req.target_level
    if target not in LEVEL_INDEX:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_level '{target}'. Valid levels: {VALIDATION_LEVELS}",
        )

    target_idx = LEVEL_INDEX[target]
    if target_idx == 0:
        raise HTTPException(status_code=400, detail="Cannot generate tasks for L0 — that's the initial state")

    prereq_level = VALIDATION_LEVELS[target_idx - 1]
    task_type = _TASK_TYPE_MAP.get(target)
    if not task_type:
        raise HTTPException(status_code=400, detail=f"No task_type mapping for {target}")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        conditions: list[str] = [
            "pl.current_validation_level = %s::validation_level",
        ]
        params: list[Any] = [prereq_level]

        conditions.append(
            """NOT EXISTS (
                SELECT 1 FROM verification_tasks vt
                WHERE vt.pharmacy_id = pl.id
                  AND vt.target_level = %s::validation_level
                  AND vt.status NOT IN ('completed', 'failed', 'skipped')
            )"""
        )
        params.append(target)

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


@router.post(
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
            "assigned_at": iso(row["assigned_at"]),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Claim task failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
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


@router.post(
    "/api/queue/{task_id}/complete",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def complete_task(request: Request, task_id: str, req: VerifyRequest):
    """Complete a verification task with evidence. Requires registry_write. Only assignee or admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
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

        if task["assigned_to"] != auth.actor_id and auth.tier != "admin":
            raise HTTPException(
                status_code=403,
                detail="Can only complete your own tasks (or be admin)",
            )

        if req.target_level != task["target_level"]:
            raise HTTPException(
                status_code=400,
                detail=f"target_level mismatch: task expects '{task['target_level']}', request says '{req.target_level}'",
            )

        pharmacy_id = str(task["pharmacy_id"])
        verification_result = execute_verification(pharmacy_id, req, auth)

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


@router.post(
    "/api/queue/{task_id}/skip",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def skip_task(request: Request, task_id: str, req: TaskSkipRequest):
    """Skip a verification task with a reason. Optionally reschedule. Requires registry_write."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
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
# Re-verification task generation
# ---------------------------------------------------------------------------


@router.post(
    "/api/queue/generate-reverification",
    dependencies=[Depends(require_tier("admin"))],
)
async def generate_reverification_tasks(request: Request, req: ReverificationGenerateRequest):
    """Scan for pharmacies needing re-verification and generate tasks. Requires: admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)
    now = datetime.now(timezone.utc)

    # Determine which levels to scan
    levels_to_scan = REVERIFICATION_INTERVALS.keys()
    if req.target_level:
        if req.target_level not in REVERIFICATION_INTERVALS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid target_level '{req.target_level}'. "
                f"Valid: {list(REVERIFICATION_INTERVALS.keys())}",
            )
        levels_to_scan = [req.target_level]

    tasks_created = 0
    already_active = 0
    scanned = 0

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                for level in levels_to_scan:
                    interval_days = REVERIFICATION_INTERVALS[level]
                    task_type = _REVERIFY_TASK_TYPE_MAP.get(level)
                    if not task_type:
                        continue

                    # Find pharmacies at this level whose last verification is expired
                    # (or expiring soon if requested)
                    if req.include_expiring_soon:
                        cutoff = now - timedelta(days=interval_days)
                    else:
                        cutoff = now - timedelta(days=interval_days + GRACE_PERIOD_DAYS)

                    cur.execute(
                        """
                        SELECT pl.id AS pharmacy_id
                        FROM pharmacy_locations pl
                        WHERE pl.current_validation_level = %s::validation_level
                          AND NOT EXISTS (
                              SELECT 1 FROM validation_status_history vsh
                              WHERE vsh.pharmacy_id = pl.id
                                AND vsh.new_level = %s::validation_level
                                AND vsh.changed_at > %s
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM verification_tasks vt
                              WHERE vt.pharmacy_id = pl.id
                                AND vt.task_type = %s::task_type
                                AND vt.status IN ('pending', 'assigned')
                          )
                        """,
                        (level, level, cutoff, task_type),
                    )
                    candidates = cur.fetchall()
                    scanned += len(candidates)

                    for row in candidates:
                        cur.execute(
                            """
                            INSERT INTO verification_tasks
                                (pharmacy_id, task_type, target_level, status, priority, created_by)
                            VALUES (%s, %s::task_type, %s::validation_level, 'pending'::task_status, 3, %s)
                            ON CONFLICT DO NOTHING
                            RETURNING id
                            """,
                            (row["pharmacy_id"], task_type, level, auth.actor_id),
                        )
                        result = cur.fetchone()
                        if result:
                            tasks_created += 1
                        else:
                            already_active += 1

        return {
            "success": True,
            "tasks_created": tasks_created,
            "already_active": already_active,
            "scanned": scanned,
            "levels_scanned": list(levels_to_scan),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Generate reverification tasks failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Bulk downgrade processing
# ---------------------------------------------------------------------------


@router.post(
    "/api/queue/process-downgrades",
    dependencies=[Depends(require_tier("admin"))],
)
async def process_downgrades(request: Request):
    """Scan for pharmacies past expiry + grace period and downgrade them. Requires: admin."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    now = datetime.now(timezone.utc)
    downgraded = 0
    skipped = 0
    errors = 0
    all_candidates: list[str] = []
    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                for level, interval_days in REVERIFICATION_INTERVALS.items():
                    if level not in DOWNGRADE_MAP:
                        continue
                    cutoff = now - timedelta(days=interval_days + GRACE_PERIOD_DAYS)
                    cur.execute(
                        """
                        SELECT pl.id AS pharmacy_id
                        FROM pharmacy_locations pl
                        WHERE pl.current_validation_level = %s::validation_level
                          AND NOT EXISTS (
                              SELECT 1 FROM validation_status_history vsh
                              WHERE vsh.pharmacy_id = pl.id
                                AND vsh.new_level = %s::validation_level
                                AND vsh.changed_at > %s
                          )
                        """,
                        (level, level, cutoff),
                    )
                    for row in cur.fetchall():
                        all_candidates.append(str(row["pharmacy_id"]))

        for pid in all_candidates:
            try:
                execute_downgrade(pid, "Re-verification expired", "system")
                downgraded += 1
            except HTTPException as he:
                if he.status_code == 400:
                    skipped += 1
                else:
                    errors += 1
                    logger.warning("Downgrade failed for %s: %s", pid, he.detail)
            except Exception:
                errors += 1
                logger.exception("Downgrade failed for %s", pid)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Process downgrades failed")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "success": True,
        "downgraded": downgraded,
        "skipped": skipped,
        "errors": errors,
        "candidates_found": len(all_candidates),
    }
