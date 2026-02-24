"""Tests for SMS campaign endpoints and processor logic."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ===================================================================
# Campaign CRUD — POST /api/sms/campaigns
# ===================================================================


class TestSmsCampaignCreate:
    """POST /api/sms/campaigns — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.post("/api/sms/campaigns", json={"campaign_name": "Test"})
        assert resp.status_code == 401

    def test_requires_admin_not_read(self, read_client):
        resp = read_client.post("/api/sms/campaigns", json={"campaign_name": "Test"})
        assert resp.status_code == 403

    def test_requires_admin_not_write(self, write_client):
        resp = write_client.post("/api/sms/campaigns", json={"campaign_name": "Test"})
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post(
            "/api/sms/campaigns",
            json={"campaign_name": "Test Campaign"},
        )
        assert resp.status_code == 503

    def test_validates_max_attempts_range(self, admin_client):
        resp = admin_client.post(
            "/api/sms/campaigns",
            json={"campaign_name": "Test", "max_attempts": 10},
        )
        assert resp.status_code == 422  # pydantic validation (le=5)

    def test_validates_retry_interval_range(self, admin_client):
        resp = admin_client.post(
            "/api/sms/campaigns",
            json={"campaign_name": "Test", "retry_interval_hours": 5},
        )
        assert resp.status_code == 422  # pydantic validation (ge=12)


# ===================================================================
# Campaign List — GET /api/sms/campaigns
# ===================================================================


class TestSmsCampaignList:
    """GET /api/sms/campaigns — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/sms/campaigns")
        assert resp.status_code == 401

    def test_requires_admin_not_read(self, read_client):
        resp = read_client.get("/api/sms/campaigns")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/sms/campaigns")
        assert resp.status_code == 503


# ===================================================================
# Campaign Detail — GET /api/sms/campaigns/{campaign_id}
# ===================================================================


class TestSmsCampaignDetail:
    """GET /api/sms/campaigns/{campaign_id} — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/sms/campaigns/some-id")
        assert resp.status_code == 401

    def test_requires_admin(self, write_client):
        resp = write_client.get("/api/sms/campaigns/some-id")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/sms/campaigns/some-id")
        assert resp.status_code == 503


# ===================================================================
# Campaign Launch — POST /api/sms/campaigns/{campaign_id}/launch
# ===================================================================


class TestSmsCampaignLaunch:
    """POST /api/sms/campaigns/{campaign_id}/launch — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.post("/api/sms/campaigns/some-id/launch")
        assert resp.status_code == 401

    def test_requires_admin(self, write_client):
        resp = write_client.post("/api/sms/campaigns/some-id/launch")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post("/api/sms/campaigns/some-id/launch")
        assert resp.status_code == 503


# ===================================================================
# Outbox — GET /api/sms/campaigns/{campaign_id}/outbox
# ===================================================================


class TestSmsOutbox:
    """GET /api/sms/campaigns/{campaign_id}/outbox — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/sms/campaigns/some-id/outbox")
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.get("/api/sms/campaigns/some-id/outbox")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/sms/campaigns/some-id/outbox")
        assert resp.status_code == 503


# ===================================================================
# Mark Sent — POST /api/sms/campaigns/{campaign_id}/mark-sent
# ===================================================================


class TestSmsMarkSent:
    """POST /api/sms/campaigns/{campaign_id}/mark-sent — requires admin + DB."""

    _body = {"message_ids": ["msg-001"], "provider_ids": None}

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/sms/campaigns/some-id/mark-sent",
            json=self._body,
        )
        assert resp.status_code == 401

    def test_requires_admin(self, write_client):
        resp = write_client.post(
            "/api/sms/campaigns/some-id/mark-sent",
            json=self._body,
        )
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post(
            "/api/sms/campaigns/some-id/mark-sent",
            json=self._body,
        )
        assert resp.status_code == 503


# ===================================================================
# Retry — POST /api/sms/campaigns/{campaign_id}/retry
# ===================================================================


class TestSmsRetry:
    """POST /api/sms/campaigns/{campaign_id}/retry — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.post("/api/sms/campaigns/some-id/retry")
        assert resp.status_code == 401

    def test_requires_admin(self, write_client):
        resp = write_client.post("/api/sms/campaigns/some-id/retry")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post("/api/sms/campaigns/some-id/retry")
        assert resp.status_code == 503


# ===================================================================
# Results — GET /api/sms/campaigns/{campaign_id}/results
# ===================================================================


class TestSmsResults:
    """GET /api/sms/campaigns/{campaign_id}/results — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/sms/campaigns/some-id/results")
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.get("/api/sms/campaigns/some-id/results")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/sms/campaigns/some-id/results")
        assert resp.status_code == 503


# ===================================================================
# Webhook — delivery status
# ===================================================================


class TestSmsDeliveryWebhook:
    """POST /api/sms/webhook/delivery — webhook auth."""

    def test_rejects_without_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/webhook/delivery",
            json={
                "provider_message_id": "msg-001",
                "status": "delivered",
            },
        )
        assert resp.status_code == 401

    def test_rejects_wrong_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/webhook/delivery",
            json={
                "provider_message_id": "msg-001",
                "status": "delivered",
            },
            headers={"X-SMS-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_returns_503_with_valid_secret_but_no_db(self, client):
        """Even with correct secret, needs DB."""
        with patch.dict(os.environ, {"SMS_WEBHOOK_SECRET": "test-secret-123"}):
            resp = client.post(
                "/api/sms/webhook/delivery",
                json={
                    "provider_message_id": "msg-001",
                    "status": "delivered",
                },
                headers={"X-SMS-Webhook-Secret": "test-secret-123"},
            )
            assert resp.status_code == 503


# ===================================================================
# Webhook — inbound reply
# ===================================================================


class TestSmsReplyWebhook:
    """POST /api/sms/webhook/reply — webhook auth."""

    def test_rejects_without_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/webhook/reply",
            json={
                "from_number": "+2348012345678",
                "message_text": "YES",
            },
        )
        assert resp.status_code == 401

    def test_rejects_wrong_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/webhook/reply",
            json={
                "from_number": "+2348012345678",
                "message_text": "YES",
            },
            headers={"X-SMS-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_returns_503_with_valid_secret_but_no_db(self, client):
        with patch.dict(os.environ, {"SMS_WEBHOOK_SECRET": "test-secret-123"}):
            resp = client.post(
                "/api/sms/webhook/reply",
                json={
                    "from_number": "+2348012345678",
                    "message_text": "YES",
                },
                headers={"X-SMS-Webhook-Secret": "test-secret-123"},
            )
            assert resp.status_code == 503


# ===================================================================
# SMS Processor — unit tests (no DB required)
# ===================================================================


class TestReplyParsing:
    """Unit tests for parse_reply()."""

    def test_parse_yes_variants(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        for word in ["YES", "yes", "Yes", "Y", "y", "1", "yep", "ok", "yeah", "yea"]:
            assert parse_reply(word) == "operating", f"Failed for '{word}'"

    def test_parse_no_variants(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        for word in ["NO", "no", "No", "N", "n", "2", "CLOSED", "closed", "nope"]:
            assert parse_reply(word) == "closed", f"Failed for '{word}'"

    def test_parse_moved_variants(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        for word in ["MOVED", "moved", "3", "relocated", "RELOCATED"]:
            assert parse_reply(word) == "relocated", f"Failed for '{word}'"

    def test_parse_with_whitespace(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        assert parse_reply("  YES  ") == "operating"
        assert parse_reply("\nyes\n") == "operating"
        assert parse_reply("  no  ") == "closed"

    def test_parse_first_word_extraction(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        assert parse_reply("Yes we are open") == "operating"
        assert parse_reply("No we closed last month") == "closed"
        assert parse_reply("Moved to new location") == "relocated"

    def test_parse_unknown_returns_none(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        assert parse_reply("hello world") is None
        assert parse_reply("maybe") is None
        assert parse_reply("what is this") is None

    def test_parse_empty_returns_none(self):
        from agent_05_platform_api.src.sms_processor import parse_reply

        assert parse_reply("") is None
        assert parse_reply("   ") is None
        assert parse_reply(None) is None


class TestMessageRendering:
    """Unit tests for render_message()."""

    def test_basic_rendering(self):
        from agent_05_platform_api.src.sms_processor import (
            DEFAULT_MESSAGE_TEMPLATE,
            render_message,
        )

        msg = render_message(
            DEFAULT_MESSAGE_TEMPLATE,
            "MedPlus Pharmacy",
            "10 Allen Avenue, Ikeja",
            "aaaaaaaa-0001-0001-0001-000000000001",
        )
        assert "MedPlus Pharmacy" in msg
        assert "10 Allen Avenue" in msg
        assert "aaaaaaaa" in msg  # msg_id_short

    def test_handles_none_address(self):
        from agent_05_platform_api.src.sms_processor import (
            DEFAULT_MESSAGE_TEMPLATE,
            render_message,
        )

        msg = render_message(DEFAULT_MESSAGE_TEMPLATE, "Test Pharm", None, "abc12345")
        assert "Test Pharm" in msg
        assert "your location" in msg  # fallback for None address

    def test_custom_template(self):
        from agent_05_platform_api.src.sms_processor import render_message

        template = "Is {pharmacy_name} open? Ref:{msg_id_short}"
        msg = render_message(template, "Kano PPMV", "5 Main St", "aabbccdd-1234")
        assert msg == "Is Kano PPMV open? Ref:aabbccdd"


class TestEvidenceConstruction:
    """Unit tests for build_sms_evidence()."""

    def test_evidence_has_required_contact_fields(self):
        from agent_05_platform_api.src.sms_processor import build_sms_evidence

        evidence = build_sms_evidence(
            phone_number="+2348012345678",
            pharmacy_name="Test Pharmacy",
            parsed_status="operating",
            reply_text="YES",
            campaign_id="camp-001",
            message_id="msg-001",
        )
        cd = evidence["contact_details"]
        assert "respondent_name" in cd
        assert "respondent_role" in cd
        assert "facility_name_confirmed" in cd
        assert "operating_status_confirmed" in cd
        assert cd["operating_status_confirmed"] == "operating"
        assert cd["facility_name_confirmed"] == "Test Pharmacy"

    def test_evidence_has_sms_metadata(self):
        from agent_05_platform_api.src.sms_processor import build_sms_evidence

        evidence = build_sms_evidence(
            phone_number="+2348012345678",
            pharmacy_name="Test Pharmacy",
            parsed_status="operating",
            reply_text="YES",
            campaign_id="camp-001",
            message_id="msg-001",
        )
        meta = evidence["sms_metadata"]
        assert meta["campaign_id"] == "camp-001"
        assert meta["message_id"] == "msg-001"
        assert meta["raw_reply"] == "YES"
        assert meta["phone_number"] == "+2348012345678"

    def test_evidence_passes_contact_validator(self):
        from agent_05_platform_api.src.evidence_validator import validate_evidence_detail
        from agent_05_platform_api.src.sms_processor import build_sms_evidence

        evidence = build_sms_evidence(
            phone_number="+2348012345678",
            pharmacy_name="Test Pharmacy",
            parsed_status="operating",
            reply_text="YES",
            campaign_id="camp-001",
            message_id="msg-001",
        )
        errors = validate_evidence_detail("contact_confirmation", evidence)
        assert errors == [], f"Validation errors: {errors}"

    def test_evidence_passes_validator_for_closed(self):
        from agent_05_platform_api.src.evidence_validator import validate_evidence_detail
        from agent_05_platform_api.src.sms_processor import build_sms_evidence

        evidence = build_sms_evidence(
            phone_number="+2348012345678",
            pharmacy_name="Test Pharmacy",
            parsed_status="closed",
            reply_text="CLOSED",
            campaign_id="camp-001",
            message_id="msg-001",
        )
        errors = validate_evidence_detail("contact_confirmation", evidence)
        assert errors == []

    def test_evidence_passes_validator_for_relocated(self):
        from agent_05_platform_api.src.evidence_validator import validate_evidence_detail
        from agent_05_platform_api.src.sms_processor import build_sms_evidence

        evidence = build_sms_evidence(
            phone_number="+2348012345678",
            pharmacy_name="Test Pharmacy",
            parsed_status="relocated",
            reply_text="MOVED",
            campaign_id="camp-001",
            message_id="msg-001",
        )
        errors = validate_evidence_detail("contact_confirmation", evidence)
        assert errors == []

    def test_respondent_name_includes_phone(self):
        from agent_05_platform_api.src.sms_processor import build_sms_evidence

        evidence = build_sms_evidence(
            phone_number="+2348099887766",
            pharmacy_name="Test",
            parsed_status="operating",
            reply_text="YES",
            campaign_id="c1",
            message_id="m1",
        )
        assert "+2348099887766" in evidence["contact_details"]["respondent_name"]
