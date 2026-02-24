"""Verification endpoints and core verification logic."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..auth import ANONYMOUS, AuthContext, require_tier
from ..db import extras
from ..helpers import (
    LEVEL_INDEX,
    REQUIRED_EVIDENCE,
    VALIDATION_LEVELS,
    get_records,
    iso,
    level_label,
)
from ..models import VerifyRequest

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

        return {
            "success": True,
            "pharmacy_id": pharmacy_id,
            "old_level": current_level,
            "new_level": target,
            "history_id": history_id,
            "message": f"Pharmacy advanced from {level_label(current_level)} to {level_label(target)}",
        }

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
