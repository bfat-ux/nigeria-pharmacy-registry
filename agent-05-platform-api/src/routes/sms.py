"""SMS campaign endpoints for bulk L1 phone verification."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from .. import db
from ..auth import ANONYMOUS, AuthContext, require_tier
from ..db import extras
from ..helpers import iso
from ..models import SmsCampaignCreateRequest, SmsDeliveryWebhook, SmsReplyWebhook
from ..sms_processor import (
    DEFAULT_MESSAGE_TEMPLATE,
    get_campaign_targets,
    get_retry_targets,
    process_delivery_report,
    process_inbound_reply,
    render_message,
    update_campaign_counts,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_webhook_secret(request: Request):
    """Validate X-SMS-Webhook-Secret header against SMS_WEBHOOK_SECRET env var."""
    webhook_secret = os.environ.get("SMS_WEBHOOK_SECRET", "")
    provided = request.headers.get("X-SMS-Webhook-Secret", "")
    if not webhook_secret or provided != webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret")


def _require_db():
    """Raise 503 if database is unavailable."""
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — SMS campaigns require a database connection",
        )


def _update_campaign_counts(campaign_id: str, cur):
    """Delegate to shared function in sms_processor."""
    update_campaign_counts(campaign_id, cur)


# ---------------------------------------------------------------------------
# 1. POST /api/sms/campaigns — Create campaign (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/api/sms/campaigns",
    dependencies=[Depends(require_tier("admin"))],
)
async def create_campaign(request: Request, req: SmsCampaignCreateRequest):
    """Create a new SMS verification campaign. Requires: admin + DB."""
    _require_db()
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    template = req.message_template or DEFAULT_MESSAGE_TEMPLATE
    filters = req.filters or {}

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO sms_campaigns
                        (campaign_name, description, message_template,
                         target_filters, max_attempts, retry_interval_hours,
                         created_by, updated_by)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        req.campaign_name,
                        req.description,
                        template,
                        json.dumps(filters),
                        req.max_attempts,
                        req.retry_interval_hours,
                        auth.actor_id,
                        auth.actor_id,
                    ),
                )
                campaign_id = str(cur.fetchone()["id"])

        return {
            "success": True,
            "campaign_id": campaign_id,
            "campaign_name": req.campaign_name,
            "status": "draft",
            "message_template": template,
            "max_attempts": req.max_attempts,
            "retry_interval_hours": req.retry_interval_hours,
            "target_filters": filters,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create SMS campaign")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 2. GET /api/sms/campaigns — List campaigns (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/api/sms/campaigns",
    dependencies=[Depends(require_tier("admin"))],
)
async def list_campaigns(
    request: Request,
    status: str | None = Query(None, description="Filter by status: draft, active, completed"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List SMS campaigns with stats. Requires: admin + DB."""
    _require_db()

    try:
        conditions = []
        params: list[Any] = []

        if status:
            conditions.append("status = %s::sms_campaign_status")
            params.append(status)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) AS total FROM sms_campaigns {where}", params)
                total = cur.fetchone()["total"]

                cur.execute(
                    f"""
                    SELECT id, campaign_name, description,
                           status::text, target_filters,
                           max_attempts, retry_interval_hours,
                           total_messages, sent_count, delivered_count,
                           replied_count, confirmed_count, expired_count, failed_count,
                           launched_at, completed_at, created_at, created_by
                    FROM sms_campaigns
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "campaigns": [
                {
                    "campaign_id": str(r["id"]),
                    "campaign_name": r["campaign_name"],
                    "description": r["description"],
                    "status": r["status"],
                    "target_filters": r["target_filters"],
                    "max_attempts": r["max_attempts"],
                    "retry_interval_hours": r["retry_interval_hours"],
                    "total_messages": r["total_messages"],
                    "sent_count": r["sent_count"],
                    "delivered_count": r["delivered_count"],
                    "replied_count": r["replied_count"],
                    "confirmed_count": r["confirmed_count"],
                    "expired_count": r["expired_count"],
                    "failed_count": r["failed_count"],
                    "launched_at": iso(r["launched_at"]),
                    "completed_at": iso(r["completed_at"]),
                    "created_at": iso(r["created_at"]),
                    "created_by": r["created_by"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list SMS campaigns")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3. GET /api/sms/campaigns/{campaign_id} — Campaign detail (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/api/sms/campaigns/{campaign_id}",
    dependencies=[Depends(require_tier("admin"))],
)
async def get_campaign_detail(
    request: Request,
    campaign_id: str,
    message_status: str | None = Query(None, description="Filter messages by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Campaign detail with paginated messages. Requires: admin + DB."""
    _require_db()

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, campaign_name, description, status::text,
                           target_filters, message_template,
                           max_attempts, retry_interval_hours,
                           total_messages, sent_count, delivered_count,
                           replied_count, confirmed_count, expired_count, failed_count,
                           launched_at, completed_at, created_at, created_by
                    FROM sms_campaigns
                    WHERE id = %s
                    """,
                    (campaign_id,),
                )
                campaign = cur.fetchone()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Fetch messages
        msg_conditions = ["campaign_id = %s"]
        msg_params: list[Any] = [campaign_id]

        if message_status:
            msg_conditions.append("status = %s::sms_message_status")
            msg_params.append(message_status)

        msg_where = " AND ".join(msg_conditions)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM sms_messages WHERE {msg_where}",
                    msg_params,
                )
                msg_total = cur.fetchone()["total"]

                cur.execute(
                    f"""
                    SELECT id, pharmacy_id, phone_number, pharmacy_name,
                           status::text, attempt_number,
                           sent_at, delivered_at,
                           reply_text, reply_received_at, parsed_status,
                           promoted, promoted_at, failure_reason,
                           created_at
                    FROM sms_messages
                    WHERE {msg_where}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    msg_params + [limit, offset],
                )
                messages = cur.fetchall()

        return {
            "campaign_id": str(campaign["id"]),
            "campaign_name": campaign["campaign_name"],
            "description": campaign["description"],
            "status": campaign["status"],
            "target_filters": campaign["target_filters"],
            "message_template": campaign["message_template"],
            "max_attempts": campaign["max_attempts"],
            "retry_interval_hours": campaign["retry_interval_hours"],
            "counts": {
                "total": campaign["total_messages"],
                "sent": campaign["sent_count"],
                "delivered": campaign["delivered_count"],
                "replied": campaign["replied_count"],
                "confirmed": campaign["confirmed_count"],
                "expired": campaign["expired_count"],
                "failed": campaign["failed_count"],
            },
            "launched_at": iso(campaign["launched_at"]),
            "completed_at": iso(campaign["completed_at"]),
            "created_at": iso(campaign["created_at"]),
            "created_by": campaign["created_by"],
            "messages": {
                "total": msg_total,
                "limit": limit,
                "offset": offset,
                "data": [
                    {
                        "message_id": str(m["id"]),
                        "pharmacy_id": str(m["pharmacy_id"]),
                        "phone_number": m["phone_number"],
                        "pharmacy_name": m["pharmacy_name"],
                        "status": m["status"],
                        "attempt_number": m["attempt_number"],
                        "sent_at": iso(m["sent_at"]),
                        "delivered_at": iso(m["delivered_at"]),
                        "reply_text": m["reply_text"],
                        "reply_received_at": iso(m["reply_received_at"]),
                        "parsed_status": m["parsed_status"],
                        "promoted": m["promoted"],
                        "promoted_at": iso(m["promoted_at"]),
                        "failure_reason": m["failure_reason"],
                        "created_at": iso(m["created_at"]),
                    }
                    for m in messages
                ],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get campaign detail")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 4. POST /api/sms/campaigns/{campaign_id}/launch — Generate outbox (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/api/sms/campaigns/{campaign_id}/launch",
    dependencies=[Depends(require_tier("admin"))],
)
async def launch_campaign(request: Request, campaign_id: str):
    """Generate outbox messages for all target pharmacies. Transitions draft → active. Requires: admin + DB."""
    _require_db()
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        # Fetch campaign
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, status::text, message_template, target_filters FROM sms_campaigns WHERE id = %s",
                    (campaign_id,),
                )
                campaign = cur.fetchone()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign["status"] != "draft":
            raise HTTPException(
                status_code=400,
                detail=f"Campaign is '{campaign['status']}' — only draft campaigns can be launched",
            )

        # Get targets
        filters = campaign["target_filters"]
        if isinstance(filters, str):
            filters = json.loads(filters)
        targets = get_campaign_targets(filters)

        if not targets:
            raise HTTPException(
                status_code=400,
                detail="No eligible pharmacies found (L0 with phone contact, not already in active campaign)",
            )

        template = campaign["message_template"]

        # Bulk insert messages
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                values = []
                for t in targets:
                    msg_id = "placeholder"  # will use DB-generated UUID
                    rendered = render_message(
                        template,
                        t["pharmacy_name"],
                        t.get("address"),
                        str(t["pharmacy_id"])[:8],
                    )
                    values.append((
                        campaign_id,
                        str(t["pharmacy_id"]),
                        t["phone_number"],
                        t["pharmacy_name"],
                        t.get("address"),
                        rendered,
                        auth.actor_id,
                        auth.actor_id,
                    ))

                extras.execute_batch(
                    cur,
                    """
                    INSERT INTO sms_messages
                        (campaign_id, pharmacy_id, phone_number,
                         pharmacy_name, pharmacy_address, outbound_message,
                         created_by, updated_by)
                    VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s)
                    """,
                    values,
                    page_size=500,
                )

                # Update campaign status
                cur.execute(
                    """
                    UPDATE sms_campaigns
                    SET status = 'active'::sms_campaign_status,
                        total_messages = %s,
                        launched_at = now(),
                        updated_at = now(),
                        updated_by = %s
                    WHERE id = %s
                    """,
                    (len(targets), auth.actor_id, campaign_id),
                )

                # Log provenance
                cur.execute(
                    """
                    SELECT log_provenance(
                        'sms_campaign', %s::uuid, 'launch',
                        %s, %s, NULL, NULL, NULL, %s::jsonb
                    )
                    """,
                    (
                        campaign_id,
                        auth.actor_id,
                        auth.actor_type,
                        json.dumps({
                            "messages_generated": len(targets),
                            "target_filters": filters,
                        }),
                    ),
                )

        return {
            "success": True,
            "campaign_id": campaign_id,
            "status": "active",
            "messages_generated": len(targets),
            "target_filters": filters,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to launch SMS campaign %s", campaign_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 5. GET /api/sms/campaigns/{campaign_id}/outbox — Pending messages (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/api/sms/campaigns/{campaign_id}/outbox",
    dependencies=[Depends(require_tier("admin"))],
)
async def get_outbox(
    request: Request,
    campaign_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get pending messages for the SMS gateway to consume. Requires: admin + DB."""
    _require_db()

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id FROM sms_campaigns WHERE id = %s",
                    (campaign_id,),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Campaign not found")

                cur.execute(
                    """
                    SELECT count(*) AS total
                    FROM sms_messages
                    WHERE campaign_id = %s AND status = 'pending'
                    """,
                    (campaign_id,),
                )
                total = cur.fetchone()["total"]

                cur.execute(
                    """
                    SELECT id, pharmacy_id, phone_number,
                           pharmacy_name, outbound_message, attempt_number
                    FROM sms_messages
                    WHERE campaign_id = %s AND status = 'pending'
                    ORDER BY created_at
                    LIMIT %s OFFSET %s
                    """,
                    (campaign_id, limit, offset),
                )
                messages = cur.fetchall()

        return {
            "campaign_id": campaign_id,
            "pending_total": total,
            "limit": limit,
            "offset": offset,
            "messages": [
                {
                    "message_id": str(m["id"]),
                    "pharmacy_id": str(m["pharmacy_id"]),
                    "phone_number": m["phone_number"],
                    "pharmacy_name": m["pharmacy_name"],
                    "outbound_message": m["outbound_message"],
                    "attempt_number": m["attempt_number"],
                }
                for m in messages
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get outbox")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 6. POST /api/sms/campaigns/{campaign_id}/mark-sent — Batch mark sent (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/api/sms/campaigns/{campaign_id}/mark-sent",
    dependencies=[Depends(require_tier("admin"))],
)
async def mark_messages_sent(
    request: Request,
    campaign_id: str,
    message_ids: list[str] = Body(..., description="List of message UUIDs to mark as sent"),
    provider_ids: dict[str, str] | None = Body(
        None, description="Optional mapping: {message_id: provider_message_id}"
    ),
):
    """Batch update messages from pending to sent. Requires: admin + DB."""
    _require_db()
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    if not message_ids:
        raise HTTPException(status_code=400, detail="message_ids must not be empty")

    provider_map = provider_ids or {}
    updated = 0

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                for msg_id in message_ids:
                    provider_id = provider_map.get(msg_id)
                    cur.execute(
                        """
                        UPDATE sms_messages
                        SET status = 'sent'::sms_message_status,
                            sent_at = now(),
                            provider_message_id = COALESCE(%s, provider_message_id),
                            updated_at = now(),
                            updated_by = %s
                        WHERE id = %s AND campaign_id = %s AND status = 'pending'
                        """,
                        (provider_id, auth.actor_id, msg_id, campaign_id),
                    )
                    updated += cur.rowcount

                _update_campaign_counts(campaign_id, cur)

        return {
            "success": True,
            "campaign_id": campaign_id,
            "requested": len(message_ids),
            "updated": updated,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to mark messages as sent")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 7. POST /api/sms/campaigns/{campaign_id}/retry — Retry non-responders (admin)
# ---------------------------------------------------------------------------


@router.post(
    "/api/sms/campaigns/{campaign_id}/retry",
    dependencies=[Depends(require_tier("admin"))],
)
async def retry_campaign(request: Request, campaign_id: str):
    """Re-queue non-responders for another attempt. Requires: admin + DB."""
    _require_db()
    auth: AuthContext = getattr(request.state, "auth", ANONYMOUS)

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, status::text, message_template, max_attempts FROM sms_campaigns WHERE id = %s",
                    (campaign_id,),
                )
                campaign = cur.fetchone()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Campaign is '{campaign['status']}' — only active campaigns can be retried",
            )

        targets = get_retry_targets(campaign_id)

        if not targets:
            return {
                "success": True,
                "campaign_id": campaign_id,
                "retried": 0,
                "expired": 0,
                "message": "No messages eligible for retry",
            }

        template = campaign["message_template"]
        max_attempts = campaign["max_attempts"]
        retried = 0
        expired = 0

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                for t in targets:
                    new_attempt = t["attempt_number"] + 1

                    if new_attempt > max_attempts:
                        # Mark as expired
                        cur.execute(
                            """
                            UPDATE sms_messages
                            SET status = 'expired'::sms_message_status,
                                updated_at = now(),
                                updated_by = %s
                            WHERE id = %s
                            """,
                            (auth.actor_id, str(t["id"])),
                        )
                        expired += 1
                        continue

                    # Mark old message as expired
                    cur.execute(
                        """
                        UPDATE sms_messages
                        SET status = 'expired'::sms_message_status,
                            updated_at = now(),
                            updated_by = %s
                        WHERE id = %s
                        """,
                        (auth.actor_id, str(t["id"])),
                    )

                    # Create new retry message
                    rendered = render_message(
                        template,
                        t["pharmacy_name"],
                        t.get("pharmacy_address"),
                        str(t["pharmacy_id"])[:8],
                    )
                    cur.execute(
                        """
                        INSERT INTO sms_messages
                            (campaign_id, pharmacy_id, phone_number,
                             pharmacy_name, pharmacy_address, outbound_message,
                             attempt_number, created_by, updated_by)
                        VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            campaign_id,
                            str(t["pharmacy_id"]),
                            t["phone_number"],
                            t["pharmacy_name"],
                            t.get("pharmacy_address"),
                            rendered,
                            new_attempt,
                            auth.actor_id,
                            auth.actor_id,
                        ),
                    )
                    retried += 1

                _update_campaign_counts(campaign_id, cur)

        return {
            "success": True,
            "campaign_id": campaign_id,
            "retried": retried,
            "expired": expired,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retry campaign %s", campaign_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 8. POST /api/sms/webhook/delivery — Delivery status webhook
# ---------------------------------------------------------------------------


@router.post("/api/sms/webhook/delivery")
async def webhook_delivery(request: Request, payload: SmsDeliveryWebhook):
    """Process delivery status reports from SMS provider. Auth: X-SMS-Webhook-Secret."""
    _require_webhook_secret(request)
    return process_delivery_report(
        payload.provider_message_id, payload.status, payload.failure_reason
    )


# ---------------------------------------------------------------------------
# 9. POST /api/sms/webhook/reply — Inbound reply webhook
# ---------------------------------------------------------------------------


@router.post("/api/sms/webhook/reply")
async def webhook_reply(request: Request, payload: SmsReplyWebhook):
    """Process inbound SMS replies. Parses reply and auto-promotes to L1 if valid. Auth: X-SMS-Webhook-Secret."""
    _require_webhook_secret(request)
    return process_inbound_reply(
        payload.from_number, payload.message_text, payload.provider_message_id
    )


# ---------------------------------------------------------------------------
# 10. GET /api/sms/campaigns/{campaign_id}/results — Export results (admin)
# ---------------------------------------------------------------------------


@router.get(
    "/api/sms/campaigns/{campaign_id}/results",
    dependencies=[Depends(require_tier("admin"))],
)
async def get_campaign_results(
    request: Request,
    campaign_id: str,
    status_filter: str | None = Query(None, description="Filter by message status"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Export campaign results. Requires: admin + DB."""
    _require_db()

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, campaign_name, status::text FROM sms_campaigns WHERE id = %s",
                    (campaign_id,),
                )
                campaign = cur.fetchone()

        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        conditions = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]

        if status_filter:
            conditions.append("status = %s::sms_message_status")
            params.append(status_filter)

        where = " AND ".join(conditions)

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM sms_messages WHERE {where}",
                    params,
                )
                total = cur.fetchone()["total"]

                cur.execute(
                    f"""
                    SELECT id, pharmacy_id, phone_number, pharmacy_name,
                           pharmacy_address,
                           status::text, attempt_number,
                           sent_at, delivered_at,
                           reply_text, reply_received_at, parsed_status,
                           promoted, promoted_at, history_id,
                           failure_reason
                    FROM sms_messages
                    WHERE {where}
                    ORDER BY status, pharmacy_name
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                rows = cur.fetchall()

        return {
            "campaign_id": str(campaign["id"]),
            "campaign_name": campaign["campaign_name"],
            "campaign_status": campaign["status"],
            "total_results": total,
            "limit": limit,
            "offset": offset,
            "results": [
                {
                    "message_id": str(r["id"]),
                    "pharmacy_id": str(r["pharmacy_id"]),
                    "phone_number": r["phone_number"],
                    "pharmacy_name": r["pharmacy_name"],
                    "pharmacy_address": r["pharmacy_address"],
                    "status": r["status"],
                    "attempt_number": r["attempt_number"],
                    "sent_at": iso(r["sent_at"]),
                    "delivered_at": iso(r["delivered_at"]),
                    "reply_text": r["reply_text"],
                    "reply_received_at": iso(r["reply_received_at"]),
                    "parsed_status": r["parsed_status"],
                    "promoted": r["promoted"],
                    "promoted_at": iso(r["promoted_at"]),
                    "history_id": str(r["history_id"]) if r["history_id"] else None,
                    "failure_reason": r["failure_reason"],
                }
                for r in rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get campaign results")
        raise HTTPException(status_code=500, detail=str(e))
