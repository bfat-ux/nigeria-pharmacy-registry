"""Verification endpoints and core verification logic."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..auth import ANONYMOUS, AuthContext, require_tier
from ..db import extras
from ..evidence_validator import validate_evidence_detail
from ..helpers import (
    CROSSREF_AUTO_APPROVE_THRESHOLD,
    CROSSREF_MANUAL_REVIEW_THRESHOLD,
    DOWNGRADE_MAP,
    GRACE_PERIOD_DAYS,
    LEVEL_INDEX,
    REQUIRED_EVIDENCE,
    REVERIFICATION_INTERVALS,
    VALIDATION_LEVELS,
    get_records,
    iso,
    level_label,
)
from ..models import DowngradeRequest, VerifyRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def execute_verification(pharmacy_id: str, req: VerifyRequest, auth: AuthContext) -> dict:
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
    if target not in LEVEL_INDEX:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_level '{target}'. Valid levels: {VALIDATION_LEVELS}",
        )

    required = REQUIRED_EVIDENCE.get(target)
    if required and req.evidence_type != required:
        if not (req.actor_type == "regulator_sync" and target == "L3_regulator_verified"):
            raise HTTPException(
                status_code=400,
                detail=f"Target level {target} requires evidence_type='{required}', got '{req.evidence_type}'",
            )

    # --- Evidence detail validation (Phase 1a) ---
    if req.evidence_detail is not None:
        ev_errors = validate_evidence_detail(req.evidence_type, req.evidence_detail)
        if ev_errors:
            raise HTTPException(
                status_code=400,
                detail={"message": "Evidence validation failed", "errors": ev_errors},
            )

    # --- Regulator crossref threshold enforcement (Phase 1c) ---
    review_required = False
    if target == "L3_regulator_verified" and req.evidence_type == "regulator_crossref":
        rd = (req.evidence_detail or {}).get("regulator_details", {})
        score = rd.get("match_score") if isinstance(rd, dict) else None
        if score is not None:
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = None
            if score is not None:
                if score < CROSSREF_MANUAL_REVIEW_THRESHOLD:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Match score {score:.2f} is below the minimum threshold "
                        f"({CROSSREF_MANUAL_REVIEW_THRESHOLD}). Cannot auto-verify.",
                    )
                if score < CROSSREF_AUTO_APPROVE_THRESHOLD:
                    review_required = True

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT current_validation_level::text FROM pharmacy_locations WHERE id = %s",
                    (pharmacy_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Pharmacy not found")

                current_level = row["current_validation_level"]
                current_idx = LEVEL_INDEX.get(current_level, 0)
                target_idx = LEVEL_INDEX[target]

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

                evidence_ref = f"{req.evidence_type}:{req.capture_method or 'unspecified'}"

                evidence_detail = req.evidence_detail or {}
                evidence_detail["evidence_type"] = req.evidence_type
                evidence_detail["capture_method"] = req.capture_method
                evidence_detail["verified_at"] = datetime.now(timezone.utc).isoformat()

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

        # Compute re-verification expiry
        now = datetime.now(timezone.utc)
        interval_days = REVERIFICATION_INTERVALS.get(target, 365)
        expires_at = now + timedelta(days=interval_days)

        result = {
            "success": True,
            "pharmacy_id": pharmacy_id,
            "old_level": current_level,
            "new_level": target,
            "history_id": history_id,
            "message": f"Pharmacy advanced from {level_label(current_level)} to {level_label(target)}",
            "expires_at": expires_at.isoformat(),
            "reverification_due_at": (expires_at - timedelta(days=GRACE_PERIOD_DAYS)).isoformat(),
        }
        if review_required:
            result["review_required"] = True
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Verification failed for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=f"Verification failed: {e}")


@router.post(
    "/api/pharmacies/{pharmacy_id}/verify",
    dependencies=[Depends(require_tier("registry_write"))],
)
async def verify_pharmacy(request: Request, pharmacy_id: str, req: VerifyRequest):
    """Advance a pharmacy through the validation ladder. Requires: registry_write tier or higher."""
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)
    return execute_verification(pharmacy_id, req, auth)


@router.get(
    "/api/pharmacies/{pharmacy_id}/validation-history",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def get_validation_history(pharmacy_id: str):
    """Get the full append-only validation history for a pharmacy. Requires: registry_read."""
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — validation history requires a database connection",
        )

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
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
                    "old_label": level_label(r["old_level"]),
                    "new_label": level_label(r["new_level"]),
                    "changed_at": iso(r["changed_at"]),
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


@router.get("/api/validation/summary")
async def get_validation_summary():
    """Count records at each validation level. Works in both DB and JSON modes."""
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
                        "label": level_label(r["level"]),
                        "count": r["count"],
                        "percentage": round(r["count"] / total * 100, 1) if total > 0 else 0,
                    }
                    for r in rows
                ],
            }
        except Exception as e:
            logger.warning("DB validation summary failed: %s", e)

    # JSON fallback
    records = get_records()
    levels = Counter(r.get("validation_level", "L0_mapped") for r in records)
    total = len(records)
    return {
        "total": total,
        "mode": "json_fallback",
        "levels": [
            {
                "level": lvl,
                "label": level_label(lvl),
                "count": cnt,
                "percentage": round(cnt / total * 100, 1) if total > 0 else 0,
            }
            for lvl, cnt in sorted(levels.items())
        ],
    }


@router.get(
    "/api/validation/expiry-report",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def get_expiry_report():
    """Report pharmacies with expired or soon-expiring verifications. Requires: registry_read + DB."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable — expiry report requires a database connection")

    try:
        now = datetime.now(timezone.utc)
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                # For each level with a reverification interval, find pharmacies
                # whose latest verification is older than the interval
                expired: list[dict] = []
                expiring_soon: list[dict] = []
                healthy_count = 0

                for level, interval_days in REVERIFICATION_INTERVALS.items():
                    cutoff_expired = now - timedelta(days=interval_days + GRACE_PERIOD_DAYS)
                    cutoff_expiring = now - timedelta(days=interval_days)

                    cur.execute(
                        """
                        SELECT pl.id, pl.name, pl.state, pl.lga,
                               pl.current_validation_level::text AS level,
                               MAX(vsh.changed_at) AS last_verified_at
                        FROM pharmacy_locations pl
                        JOIN validation_status_history vsh
                            ON vsh.pharmacy_id = pl.id
                            AND vsh.new_level = %s::validation_level
                        WHERE pl.current_validation_level = %s::validation_level
                        GROUP BY pl.id, pl.name, pl.state, pl.lga, pl.current_validation_level
                        """,
                        (level, level),
                    )
                    rows = cur.fetchall()

                    for r in rows:
                        last_at = r["last_verified_at"]
                        if last_at is None:
                            continue
                        entry = {
                            "pharmacy_id": str(r["id"]),
                            "facility_name": r["name"],
                            "state": r["state"],
                            "lga": r["lga"],
                            "level": r["level"],
                            "last_verified_at": iso(last_at),
                            "expires_at": iso(last_at + timedelta(days=interval_days)),
                        }
                        if last_at < cutoff_expired:
                            expired.append(entry)
                        elif last_at < cutoff_expiring:
                            expiring_soon.append(entry)
                        else:
                            healthy_count += 1

        return {
            "generated_at": now.isoformat(),
            "expired_count": len(expired),
            "expired": expired,
            "expiring_soon_count": len(expiring_soon),
            "expiring_soon": expiring_soon,
            "healthy_count": healthy_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to generate expiry report")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Downgrade workflow
# ---------------------------------------------------------------------------


def execute_downgrade(pharmacy_id: str, reason: str, actor_id: str) -> dict:
    """Downgrade a pharmacy one level.  Returns result dict.  Raises HTTPException on error."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable — downgrade requires a database connection")

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT current_validation_level::text FROM pharmacy_locations WHERE id = %s",
                    (pharmacy_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Pharmacy not found")

                current_level = row["current_validation_level"]
                new_level = DOWNGRADE_MAP.get(current_level)
                if new_level is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot downgrade from {current_level} — already at lowest level or not downgradable",
                    )

                evidence_ref = f"downgrade:{reason[:80]}"

                # Triple log — same pattern as execute_verification
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
                        new_level,
                        actor_id,
                        "system",
                        evidence_ref,
                        f"Downgrade: {reason}",
                        json.dumps({
                            "action": "downgrade",
                            "reason": reason,
                            "old_level": current_level,
                            "new_level": new_level,
                            "downgraded_at": datetime.now(timezone.utc).isoformat(),
                        }),
                    ),
                )
                history_id = str(cur.fetchone()["history_id"])

                cur.execute(
                    """
                    SELECT log_provenance(
                        'pharmacy_location', %s::uuid, 'downgrade',
                        %s, %s, NULL, NULL, NULL, %s::jsonb
                    )
                    """,
                    (
                        pharmacy_id,
                        actor_id,
                        "system",
                        json.dumps({
                            "old_level": current_level,
                            "new_level": new_level,
                            "reason": reason,
                            "history_id": history_id,
                        }),
                    ),
                )

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
                        actor_id,
                        "system",
                        pharmacy_id,
                        f"/api/pharmacies/{pharmacy_id}/downgrade",
                        json.dumps({
                            "action": "downgrade",
                            "old_level": current_level,
                            "new_level": new_level,
                            "reason": reason,
                        }),
                    ),
                )

        return {
            "success": True,
            "pharmacy_id": pharmacy_id,
            "old_level": current_level,
            "new_level": new_level,
            "history_id": history_id,
            "message": f"Pharmacy downgraded from {level_label(current_level)} to {level_label(new_level)}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Downgrade failed for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=f"Downgrade failed: {e}")


@router.post(
    "/api/pharmacies/{pharmacy_id}/downgrade",
    dependencies=[Depends(require_tier("admin"))],
)
async def downgrade_pharmacy(pharmacy_id: str, req: DowngradeRequest):
    """Downgrade a pharmacy one validation level. Requires: admin."""
    return execute_downgrade(pharmacy_id, req.reason, req.actor_id)


# ---------------------------------------------------------------------------
# Verification progress dashboard
# ---------------------------------------------------------------------------


@router.get("/api/validation/progress")
async def get_validation_progress():
    """Verification funnel progress. Public access. Works in both DB and JSON modes."""
    if db.is_available():
        try:
            now = datetime.now(timezone.utc)
            with db.get_conn() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    # Level counts
                    cur.execute(
                        """
                        SELECT current_validation_level::text AS level, count(*) AS count
                        FROM pharmacy_locations
                        GROUP BY current_validation_level
                        ORDER BY current_validation_level
                        """
                    )
                    level_rows = cur.fetchall()
                    total = sum(r["count"] for r in level_rows)
                    by_level = {r["level"]: r["count"] for r in level_rows}

                    l0_count = by_level.get("L0_mapped", 0)
                    verified_above_l0 = total - l0_count

                    # Recent verification activity
                    activity = {}
                    for days, label in [(7, "last_7_days"), (30, "last_30_days"), (90, "last_90_days")]:
                        cutoff = now - timedelta(days=days)
                        cur.execute(
                            "SELECT count(*) AS cnt FROM validation_status_history WHERE changed_at > %s",
                            (cutoff,),
                        )
                        activity[label] = cur.fetchone()["cnt"]

                    # Pending tasks
                    cur.execute(
                        "SELECT count(*) AS cnt FROM verification_tasks WHERE status IN ('pending', 'assigned')"
                    )
                    pending_tasks = cur.fetchone()["cnt"]

            return {
                "total_pharmacies": total,
                "by_level": by_level,
                "verified_above_L0": verified_above_l0,
                "verified_percentage": round(verified_above_l0 / total * 100, 1) if total > 0 else 0,
                "recent_activity": activity,
                "pending_tasks": pending_tasks,
                "mode": "database",
            }
        except Exception as e:
            logger.warning("DB validation progress failed: %s", e)

    # JSON fallback
    records = get_records()
    total = len(records)
    levels = Counter(r.get("validation_level", "L0_mapped") for r in records)
    by_level = dict(sorted(levels.items()))
    l0_count = by_level.get("L0_mapped", 0)
    verified_above_l0 = total - l0_count

    return {
        "total_pharmacies": total,
        "by_level": by_level,
        "verified_above_L0": verified_above_l0,
        "verified_percentage": round(verified_above_l0 / total * 100, 1) if total > 0 else 0,
        "recent_activity": None,
        "pending_tasks": None,
        "mode": "json_fallback",
    }
