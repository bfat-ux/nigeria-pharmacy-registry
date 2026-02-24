"""Africa's Talking (AT) webhook adapter for SMS delivery reports and inbound replies.

AT sends form-encoded POST callbacks (not JSON). This adapter translates AT's
field names and status values into our internal format, then delegates to the
shared processing functions in sms_processor.py.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Form, HTTPException, Request

from ..sms_processor import process_delivery_report, process_inbound_reply

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# AT status → internal status mapping
# ---------------------------------------------------------------------------

# Terminal statuses that we process
_AT_STATUS_MAP: dict[str, str] = {
    "Success": "delivered",
    "Failed": "failed",
    "Rejected": "failed",
}

# Intermediate statuses that we skip (AT sends these before final status)
_AT_INTERMEDIATE = {"Sent", "Buffered", "Submitted"}


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _require_at_webhook_secret(request: Request):
    """Validate X-AT-Webhook-Secret header against AT_WEBHOOK_SECRET env var.

    If AT_WEBHOOK_SECRET is not set, webhook auth is bypassed (open mode).
    This allows Africa's Talking callbacks to work without a proxy that injects
    the header. Set AT_WEBHOOK_SECRET in production to enforce authentication.
    """
    secret = os.environ.get("AT_WEBHOOK_SECRET", "")
    if not secret:
        # No secret configured — allow all requests (open mode)
        return
    provided = request.headers.get("X-AT-Webhook-Secret", "")
    if provided != secret:
        raise HTTPException(status_code=401, detail="Invalid or missing AT webhook secret")


# ---------------------------------------------------------------------------
# POST /api/sms/at/delivery — AT delivery status callback
# ---------------------------------------------------------------------------


@router.post("/api/sms/at/delivery")
async def at_delivery_webhook(
    request: Request,
    id: str = Form(..., description="AT message ID (ATXid_xxx)"),
    status: str = Form(..., description="AT delivery status"),
    failureReason: str | None = Form(None, description="Failure reason if failed"),
    phoneNumber: str | None = Form(None, description="Recipient phone number"),
):
    """
    Receive delivery status reports from Africa's Talking.

    AT POSTs form-encoded data: id, status, failureReason, phoneNumber.
    Terminal statuses (Success, Failed, Rejected) are processed.
    Intermediate statuses (Sent, Buffered) are acknowledged and skipped.
    """
    _require_at_webhook_secret(request)

    # Skip intermediate statuses
    if status in _AT_INTERMEDIATE:
        return {"success": True, "skipped": True, "at_status": status}

    # Map AT status to internal
    internal_status = _AT_STATUS_MAP.get(status)
    if internal_status is None:
        logger.warning("Unknown AT delivery status: %s for message %s", status, id)
        return {"success": False, "reason": "unknown_status", "at_status": status}

    return process_delivery_report(
        provider_message_id=id,
        status=internal_status,
        failure_reason=failureReason,
    )


# ---------------------------------------------------------------------------
# POST /api/sms/at/reply — AT inbound SMS callback
# ---------------------------------------------------------------------------


@router.post("/api/sms/at/reply")
async def at_reply_webhook(
    request: Request,
    # 'from' is a Python keyword, so we alias it
    from_: str = Form(..., alias="from", description="Sender phone number"),
    text: str = Form(..., description="Message body"),
    id: str | None = Form(None, description="AT message ID"),
    date: str | None = Form(None, description="Timestamp from AT"),
    linkId: str | None = Form(None, description="AT link ID for premium SMS"),
):
    """
    Receive inbound SMS replies from Africa's Talking.

    AT POSTs form-encoded data: from, text, id, date, linkId.
    """
    _require_at_webhook_secret(request)

    return process_inbound_reply(
        from_number=from_,
        message_text=text,
        provider_message_id=id,
    )
