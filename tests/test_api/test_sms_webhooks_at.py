"""Tests for Africa's Talking SMS webhook adapter endpoints."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ===================================================================
# AT Delivery Webhook — POST /api/sms/at/delivery
# ===================================================================


class TestAtDeliveryWebhook:
    """POST /api/sms/at/delivery — form-encoded AT delivery callbacks."""

    def test_rejects_without_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/at/delivery",
            data={"id": "ATXid_123", "status": "Success"},
        )
        assert resp.status_code == 401

    def test_rejects_wrong_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/at/delivery",
            data={"id": "ATXid_123", "status": "Success"},
            headers={"X-AT-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_skips_intermediate_status_sent(self, client):
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={"id": "ATXid_123", "status": "Sent"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["skipped"] is True
        assert body["at_status"] == "Sent"

    def test_skips_intermediate_status_buffered(self, client):
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={"id": "ATXid_123", "status": "Buffered"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["skipped"] is True

    def test_skips_intermediate_status_submitted(self, client):
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={"id": "ATXid_123", "status": "Submitted"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 200
        assert resp.json()["skipped"] is True

    def test_unknown_status_returns_failure(self, client):
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={"id": "ATXid_123", "status": "SomeNewStatus"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["reason"] == "unknown_status"

    def test_success_status_returns_503_without_db(self, client):
        """Success maps to 'delivered' which requires DB processing."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={
                    "id": "ATXid_123",
                    "status": "Success",
                    "phoneNumber": "+2348012345678",
                },
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 503

    def test_failed_status_returns_503_without_db(self, client):
        """Failed maps to 'failed' which requires DB processing."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={
                    "id": "ATXid_123",
                    "status": "Failed",
                    "failureReason": "InsufficientCredit",
                },
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 503

    def test_rejected_status_returns_503_without_db(self, client):
        """Rejected maps to 'failed' which requires DB processing."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={
                    "id": "ATXid_123",
                    "status": "Rejected",
                    "failureReason": "InvalidPhoneNumber",
                },
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 503

    def test_requires_id_field(self, client):
        """AT must send the 'id' field."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={"status": "Success"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 422

    def test_requires_status_field(self, client):
        """AT must send the 'status' field."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/delivery",
                data={"id": "ATXid_123"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 422


# ===================================================================
# AT Reply Webhook — POST /api/sms/at/reply
# ===================================================================


class TestAtReplyWebhook:
    """POST /api/sms/at/reply — form-encoded AT inbound SMS callbacks."""

    def test_rejects_without_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/at/reply",
            data={"from": "+2348012345678", "text": "YES"},
        )
        assert resp.status_code == 401

    def test_rejects_wrong_webhook_secret(self, client):
        resp = client.post(
            "/api/sms/at/reply",
            data={"from": "+2348012345678", "text": "YES"},
            headers={"X-AT-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_returns_503_with_valid_secret_but_no_db(self, client):
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/reply",
                data={
                    "from": "+2348012345678",
                    "text": "YES",
                    "id": "ATXid_456",
                },
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 503

    def test_requires_from_field(self, client):
        """AT must send the 'from' field."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/reply",
                data={"text": "YES"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 422

    def test_requires_text_field(self, client):
        """AT must send the 'text' field."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/reply",
                data={"from": "+2348012345678"},
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        assert resp.status_code == 422

    def test_accepts_optional_fields(self, client):
        """AT may send optional fields: id, date, linkId."""
        with patch.dict(os.environ, {"AT_WEBHOOK_SECRET": "at-test-secret"}):
            resp = client.post(
                "/api/sms/at/reply",
                data={
                    "from": "+2348012345678",
                    "text": "YES we are open",
                    "id": "ATXid_789",
                    "date": "2026-02-24T10:00:00Z",
                    "linkId": "SampleLinkId",
                },
                headers={"X-AT-Webhook-Secret": "at-test-secret"},
            )
        # 503 because no DB, but proves the fields were accepted (not 422)
        assert resp.status_code == 503


# ===================================================================
# Status Mapping Unit Tests
# ===================================================================


class TestAtStatusMapping:
    """Verify AT status values map correctly to internal statuses."""

    def test_success_maps_to_delivered(self):
        from agent_05_platform_api.src.routes.sms_webhooks_at import _AT_STATUS_MAP
        assert _AT_STATUS_MAP["Success"] == "delivered"

    def test_failed_maps_to_failed(self):
        from agent_05_platform_api.src.routes.sms_webhooks_at import _AT_STATUS_MAP
        assert _AT_STATUS_MAP["Failed"] == "failed"

    def test_rejected_maps_to_failed(self):
        from agent_05_platform_api.src.routes.sms_webhooks_at import _AT_STATUS_MAP
        assert _AT_STATUS_MAP["Rejected"] == "failed"

    def test_sent_is_intermediate(self):
        from agent_05_platform_api.src.routes.sms_webhooks_at import _AT_INTERMEDIATE
        assert "Sent" in _AT_INTERMEDIATE

    def test_buffered_is_intermediate(self):
        from agent_05_platform_api.src.routes.sms_webhooks_at import _AT_INTERMEDIATE
        assert "Buffered" in _AT_INTERMEDIATE
