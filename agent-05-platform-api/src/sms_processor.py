"""
SMS campaign processor — message rendering, reply parsing, and L1 promotion.

Handles bulk SMS verification campaigns for L0 → L1 contact confirmation.
Gateway-agnostic: provides outbox + webhook endpoints for any SMS provider.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from . import db
from .db import extras

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MESSAGE_TEMPLATE = (
    "Nigeria Pharmacy Registry: Is {pharmacy_name} at {address} "
    "currently operating? Reply YES, NO, MOVED, or CLOSED. Ref:{msg_id_short}"
)

# Reply parsing — generous matching
_REPLY_OPERATING = {
    "yes", "y", "1", "yep", "ok", "yeah", "yea",
    "affirmative", "true", "open", "operating",
}
_REPLY_CLOSED = {
    "no", "n", "2", "closed", "close", "nope", "shut",
    "not operating", "not open",
}
_REPLY_RELOCATED = {
    "moved", "move", "3", "relocated", "relocate", "shifted",
}

# Build lookup map
REPLY_STATUS_MAP: dict[str, str] = {}
for _word in _REPLY_OPERATING:
    REPLY_STATUS_MAP[_word] = "operating"
for _word in _REPLY_CLOSED:
    REPLY_STATUS_MAP[_word] = "closed"
for _word in _REPLY_RELOCATED:
    REPLY_STATUS_MAP[_word] = "relocated"


# ---------------------------------------------------------------------------
# Message rendering
# ---------------------------------------------------------------------------


def render_message(template: str, pharmacy_name: str, address: str | None, msg_id: str) -> str:
    """Substitute pharmacy details into the SMS template."""
    msg_id_short = msg_id[:8] if msg_id else "00000000"
    return template.format(
        pharmacy_name=pharmacy_name,
        address=address or "your location",
        msg_id_short=msg_id_short,
    )


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


def parse_reply(reply_text: str) -> str | None:
    """
    Parse an inbound SMS reply into an operating status.

    Returns 'operating', 'closed', 'relocated', or None (unparseable).
    Strips whitespace, lowercases, checks full text then first word.
    """
    if not reply_text or not reply_text.strip():
        return None

    cleaned = reply_text.strip().lower()

    # Try full text match
    result = REPLY_STATUS_MAP.get(cleaned)
    if result:
        return result

    # Try first word only (handles "Yes we are open", etc.)
    first_word = cleaned.split()[0] if cleaned.split() else ""
    return REPLY_STATUS_MAP.get(first_word)


# ---------------------------------------------------------------------------
# Evidence construction
# ---------------------------------------------------------------------------


def build_sms_evidence(
    phone_number: str,
    pharmacy_name: str,
    parsed_status: str,
    reply_text: str,
    campaign_id: str,
    message_id: str,
) -> dict:
    """
    Construct evidence_detail dict that satisfies _validate_contact() requirements.

    The contact_details sub-object contains the 4 required fields.
    The sms_metadata sub-object provides additional audit context.
    """
    return {
        "contact_details": {
            "respondent_name": f"SMS Respondent ({phone_number})",
            "respondent_role": "sms_responder",
            "facility_name_confirmed": pharmacy_name,
            "operating_status_confirmed": parsed_status,
        },
        "sms_metadata": {
            "campaign_id": campaign_id,
            "message_id": message_id,
            "raw_reply": reply_text,
            "phone_number": phone_number,
            "capture_method": "sms_reply",
            "parsed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# L1 promotion via existing verification pipeline
# ---------------------------------------------------------------------------


def promote_pharmacy_via_sms(
    pharmacy_id: str,
    phone_number: str,
    pharmacy_name: str,
    parsed_status: str,
    reply_text: str,
    campaign_id: str,
    message_id: str,
    actor_id: str,
) -> dict:
    """
    Build evidence, construct VerifyRequest, call execute_verification().
    Returns the verification result dict.
    """
    from .models import VerifyRequest
    from .routes.verification import execute_verification
    from .auth import AuthContext

    evidence = build_sms_evidence(
        phone_number=phone_number,
        pharmacy_name=pharmacy_name,
        parsed_status=parsed_status,
        reply_text=reply_text,
        campaign_id=campaign_id,
        message_id=message_id,
    )

    verify_req = VerifyRequest(
        target_level="L1_contact_confirmed",
        evidence_type="contact_confirmation",
        capture_method="sms_reply",
        actor_id=actor_id,
        actor_type="system",
        source_description=f"SMS campaign {campaign_id} — auto-promotion from reply",
        evidence_detail=evidence,
    )

    auth = AuthContext(
        key_id="sms_campaign",
        tier="admin",
        actor_id=actor_id,
        actor_type="system",
        scopes=["*"],
    )

    return execute_verification(pharmacy_id, verify_req, auth)


# ---------------------------------------------------------------------------
# Campaign count aggregation
# ---------------------------------------------------------------------------


def update_campaign_counts(campaign_id: str, cur):
    """Recompute aggregate counts from sms_messages for a campaign."""
    cur.execute(
        """
        UPDATE sms_campaigns sc SET
            total_messages  = sub.total,
            sent_count      = sub.sent,
            delivered_count = sub.delivered,
            replied_count   = sub.replied,
            confirmed_count = sub.confirmed,
            expired_count   = sub.expired,
            failed_count    = sub.failed,
            updated_at      = now()
        FROM (
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'sent')      AS sent,
                count(*) FILTER (WHERE status = 'delivered')  AS delivered,
                count(*) FILTER (WHERE status = 'replied')    AS replied,
                count(*) FILTER (WHERE status = 'confirmed')  AS confirmed,
                count(*) FILTER (WHERE status = 'expired')    AS expired,
                count(*) FILTER (WHERE status = 'failed')     AS failed
            FROM sms_messages
            WHERE campaign_id = %s
        ) sub
        WHERE sc.id = %s
        """,
        (campaign_id, campaign_id),
    )


# ---------------------------------------------------------------------------
# Shared webhook processing (used by generic + AT-specific adapters)
# ---------------------------------------------------------------------------


def process_delivery_report(
    provider_message_id: str,
    status: str,
    failure_reason: str | None = None,
) -> dict:
    """
    Core delivery report processing. Looks up message by provider_message_id,
    updates to delivered/failed, recalculates campaign counts.
    """
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — SMS campaigns require a database connection",
        )

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, campaign_id, status::text
                    FROM sms_messages
                    WHERE provider_message_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (provider_message_id,),
                )
                msg = cur.fetchone()

        if not msg:
            return {"success": False, "reason": "no_matching_message"}

        if msg["status"] not in ("sent", "pending"):
            return {
                "success": True,
                "message_id": str(msg["id"]),
                "status": msg["status"],
                "message": "Message already past sent status",
            }

        new_status = "delivered" if status == "delivered" else "failed"

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                if new_status == "delivered":
                    cur.execute(
                        """
                        UPDATE sms_messages
                        SET status = 'delivered'::sms_message_status,
                            delivered_at = now(),
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (str(msg["id"]),),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE sms_messages
                        SET status = 'failed'::sms_message_status,
                            failure_reason = %s,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (failure_reason or status, str(msg["id"])),
                    )

                update_campaign_counts(str(msg["campaign_id"]), cur)

        return {
            "success": True,
            "message_id": str(msg["id"]),
            "new_status": new_status,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to process delivery report")
        raise HTTPException(status_code=500, detail=str(e))


def process_inbound_reply(
    from_number: str,
    message_text: str,
    provider_message_id: str | None = None,
) -> dict:
    """
    Core inbound reply processing. Finds recent message for phone,
    parses reply, promotes to L1 if valid, updates message record.
    """
    if not db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — SMS campaigns require a database connection",
        )

    try:
        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT sm.id, sm.campaign_id, sm.pharmacy_id,
                           sm.phone_number, sm.pharmacy_name,
                           sm.status::text
                    FROM sms_messages sm
                    JOIN sms_campaigns sc ON sc.id = sm.campaign_id
                    WHERE sm.phone_number = %s
                      AND sc.status = 'active'::sms_campaign_status
                      AND sm.status IN ('sent', 'delivered')
                    ORDER BY sm.created_at DESC
                    LIMIT 1
                    """,
                    (from_number,),
                )
                msg = cur.fetchone()

        if not msg:
            return {"success": False, "reason": "no_matching_message"}

        msg_id = str(msg["id"])
        campaign_id = str(msg["campaign_id"])
        pharmacy_id = str(msg["pharmacy_id"])

        parsed_status = parse_reply(message_text)
        now = datetime.now(timezone.utc)

        if parsed_status is None:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE sms_messages
                        SET status = 'replied'::sms_message_status,
                            reply_text = %s,
                            reply_received_at = %s,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (message_text, now, msg_id),
                    )
                    update_campaign_counts(campaign_id, cur)

            return {
                "success": True,
                "message_id": msg_id,
                "parsed": False,
                "reply_text": message_text,
                "message": "Reply received but could not be parsed",
            }

        # Valid reply — promote to L1
        try:
            result = promote_pharmacy_via_sms(
                pharmacy_id=pharmacy_id,
                phone_number=msg["phone_number"],
                pharmacy_name=msg["pharmacy_name"],
                parsed_status=parsed_status,
                reply_text=message_text,
                campaign_id=campaign_id,
                message_id=msg_id,
                actor_id="sms_campaign_system",
            )
            history_id = result.get("history_id")
            promoted = True
        except HTTPException as e:
            logger.warning(
                "SMS promotion failed for pharmacy %s: %s", pharmacy_id, e.detail
            )
            history_id = None
            promoted = False

        new_status = "confirmed" if promoted else "replied"
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sms_messages
                    SET status = %s::sms_message_status,
                        reply_text = %s,
                        reply_received_at = %s,
                        parsed_status = %s,
                        promoted = %s,
                        promoted_at = CASE WHEN %s THEN %s ELSE NULL END,
                        history_id = %s::uuid,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        new_status,
                        message_text,
                        now,
                        parsed_status,
                        promoted,
                        promoted,
                        now,
                        history_id,
                        msg_id,
                    ),
                )
                update_campaign_counts(campaign_id, cur)

        return {
            "success": True,
            "message_id": msg_id,
            "parsed": True,
            "parsed_status": parsed_status,
            "promoted": promoted,
            "pharmacy_id": pharmacy_id,
            "history_id": history_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to process inbound reply")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Campaign targeting queries
# ---------------------------------------------------------------------------


def get_campaign_targets(filters: dict | None = None) -> list[dict]:
    """
    Query L0 pharmacies that have a primary phone contact.
    Excludes pharmacies already in an active campaign with pending/sent/delivered messages.

    Returns list of dicts: {pharmacy_id, pharmacy_name, address, phone_number, state, lga}
    """
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    conditions = [
        "pl.current_validation_level = 'L0_mapped'",
    ]
    params: list = []

    if filters:
        if filters.get("state"):
            conditions.append("pl.state ILIKE %s")
            params.append(filters["state"])
        if filters.get("lga"):
            conditions.append("pl.lga ILIKE %s")
            params.append(filters["lga"])
        if filters.get("facility_type"):
            conditions.append("pl.facility_type = %s::facility_type")
            params.append(filters["facility_type"])

    where = " AND ".join(conditions)

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT pl.id AS pharmacy_id,
                       pl.name AS pharmacy_name,
                       pl.address_line_1 AS address,
                       pl.state,
                       pl.lga,
                       c.contact_value AS phone_number
                FROM pharmacy_locations pl
                JOIN contacts c
                    ON c.pharmacy_id = pl.id
                    AND c.contact_type = 'phone'
                    AND c.is_primary = true
                WHERE {where}
                  AND pl.id NOT IN (
                      SELECT sm.pharmacy_id FROM sms_messages sm
                      JOIN sms_campaigns sc ON sc.id = sm.campaign_id
                      WHERE sc.status = 'active'::sms_campaign_status
                      AND sm.status IN ('pending', 'sent', 'delivered')
                  )
                ORDER BY pl.state, pl.name
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]


def get_retry_targets(campaign_id: str) -> list[dict]:
    """
    Find messages eligible for retry within a campaign.
    Eligible: status in (sent, delivered), attempt_number < max_attempts,
    sent_at older than retry_interval_hours.
    """
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT sm.id, sm.pharmacy_id, sm.phone_number,
                       sm.pharmacy_name, sm.pharmacy_address,
                       sm.attempt_number, sm.sent_at
                FROM sms_messages sm
                JOIN sms_campaigns sc ON sc.id = sm.campaign_id
                WHERE sm.campaign_id = %s
                  AND sm.status IN ('sent', 'delivered')
                  AND sm.attempt_number < sc.max_attempts
                  AND sm.sent_at < now() - (sc.retry_interval_hours || ' hours')::interval
                ORDER BY sm.created_at
                """,
                (campaign_id,),
            )
            return [dict(r) for r in cur.fetchall()]
