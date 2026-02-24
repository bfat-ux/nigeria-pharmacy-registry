"""Tests for authentication and authorization enforcement."""

from __future__ import annotations

from .conftest import SAMPLE_PHARMACIES


class TestPublicAccess:
    """Endpoints accessible without an API key."""

    def test_health_is_public(self, client):
        resp = client.get("/api/health")
        assert resp.status_code in (200, 503)  # 503 in degraded mode

    def test_pharmacies_list_is_public(self, client):
        resp = client.get("/api/pharmacies")
        assert resp.status_code == 200

    def test_pharmacy_detail_is_public(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}")
        assert resp.status_code == 200

    def test_stats_is_public(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200

    def test_geojson_is_public(self, client):
        resp = client.get("/api/geojson")
        assert resp.status_code == 200

    def test_fhir_metadata_is_public(self, client):
        resp = client.get("/api/fhir/metadata")
        assert resp.status_code == 200

    def test_validation_summary_is_public(self, client):
        resp = client.get("/api/validation/summary")
        assert resp.status_code == 200


class TestRegistryReadAccess:
    """Endpoints requiring at least registry_read tier."""

    def test_health_detailed_denied_for_public(self, client):
        resp = client.get("/api/health/detailed")
        assert resp.status_code == 401

    def test_health_detailed_denied_for_read(self, read_client):
        # /api/health/detailed requires admin, not just read
        resp = read_client.get("/api/health/detailed")
        assert resp.status_code == 403

    def test_data_quality_denied_for_public(self, client):
        resp = client.get("/api/health/data-quality")
        assert resp.status_code == 401

    def test_data_quality_allowed_for_read(self, read_client):
        # Will 503 due to no DB, but should not 401/403
        resp = read_client.get("/api/health/data-quality")
        assert resp.status_code == 503  # allowed but DB unavailable


class TestRegistryWriteAccess:
    """Endpoints requiring registry_write tier."""

    def test_verify_denied_for_public(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.post(
            f"/api/pharmacies/{pid}/verify",
            json={
                "target_level": "L1_contact_confirmed",
                "evidence_type": "contact_confirmation",
                "actor_id": "test-user",
                "actor_type": "human_verifier",
            },
        )
        assert resp.status_code == 401

    def test_verify_denied_for_read(self, read_client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = read_client.post(
            f"/api/pharmacies/{pid}/verify",
            json={
                "target_level": "L1_contact_confirmed",
                "evidence_type": "contact_confirmation",
                "actor_id": "test-user",
                "actor_type": "human_verifier",
            },
        )
        assert resp.status_code == 403


class TestAdminAccess:
    """Endpoints requiring admin tier."""

    def test_health_detailed_allowed_for_admin(self, admin_client):
        resp = admin_client.get("/api/health/detailed")
        # Should be 503 (DB unavailable), NOT 401/403
        assert resp.status_code == 503


class TestInvalidApiKey:
    """Requests with invalid API keys."""

    def test_invalid_key_returns_401(self, app):
        from starlette.testclient import TestClient

        c = TestClient(app, raise_server_exceptions=False, headers={
            "X-API-Key": "npr_invalid_key_that_doesnt_exist"
        })
        # The auth middleware: _cache_get returns None, _validate_key returns None → 401
        resp = c.get("/api/pharmacies")
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]


class TestContactRedaction:
    """Public tier should get redacted contacts; higher tiers should not."""

    def test_public_gets_redacted_phone(self, client):
        # First pharmacy has phone: +2348012345678
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        phone = data.get("phone")
        if phone:
            assert "****" in phone

    def test_public_gets_redacted_email(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}")
        data = resp.json()["data"]
        email = data.get("email")
        if email:
            assert "***@" in email

    def test_read_tier_gets_full_phone(self, read_client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = read_client.get(f"/api/pharmacies/{pid}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        phone = data.get("phone")
        if phone:
            assert "****" not in phone
            assert phone == "+2348012345678"
