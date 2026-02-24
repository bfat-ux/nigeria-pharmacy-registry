"""Tests for audit, provenance, and timeline endpoints — all DB-only."""

from __future__ import annotations

from .conftest import SAMPLE_PHARMACIES


class TestEvidence:
    """GET /api/pharmacies/{id}/evidence — requires DB + registry_read."""

    def test_requires_auth(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}/evidence")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = read_client.get(f"/api/pharmacies/{pid}/evidence")
        assert resp.status_code == 503


class TestProvenance:
    """GET /api/provenance — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/provenance")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/provenance")
        assert resp.status_code == 503


class TestAuditLog:
    """GET /api/audit — requires DB + admin."""

    def test_requires_auth(self, client):
        resp = client.get("/api/audit")
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.get("/api/audit")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/audit")
        assert resp.status_code == 503


class TestActorActivity:
    """GET /api/actors/{actor_id}/activity — requires DB + admin."""

    def test_requires_auth(self, client):
        resp = client.get("/api/actors/test-actor/activity")
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.get("/api/actors/test-actor/activity")
        assert resp.status_code == 403


class TestTimeline:
    """GET /api/pharmacies/{id}/timeline — requires DB + registry_read."""

    def test_requires_auth(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}/timeline")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = read_client.get(f"/api/pharmacies/{pid}/timeline")
        assert resp.status_code == 503


class TestAuditStats:
    """GET /api/audit/stats — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/audit/stats")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/audit/stats")
        assert resp.status_code == 503
