"""Tests for health and monitoring endpoints."""

from __future__ import annotations


class TestHealthEndpoint:
    """GET /api/health — always public, no auth required."""

    def test_returns_200_in_json_fallback(self, client):
        resp = client.get("/api/health")
        # In fallback mode (DB patched away) health reports degraded / 503
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status" in data
        assert "mode" in data
        assert data["mode"] == "json_fallback"
        assert data["record_count"] == 5  # our sample data
        assert data["version"] == "0.3.0"
        assert data["database_connected"] is False

    def test_contains_uptime(self, client):
        resp = client.get("/api/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], int)
        assert data["uptime_seconds"] >= 0

    def test_contains_checks_block(self, client):
        resp = client.get("/api/health")
        data = resp.json()
        assert "checks" in data
        assert "database" in data["checks"]
        assert data["checks"]["database"]["status"] == "down"


class TestDashboardRoute:
    """GET / — serves the static HTML dashboard."""

    def test_serves_html(self, client):
        resp = client.get("/")
        # Should return 200 with HTML content
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


class TestHealthDetailedEndpoint:
    """GET /api/health/detailed — admin only, requires DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/health/detailed")
        assert resp.status_code == 401

    def test_requires_admin_tier(self, read_client):
        resp = read_client.get("/api/health/detailed")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        # DB is patched off, so this should return 503
        resp = admin_client.get("/api/health/detailed")
        assert resp.status_code == 503


class TestDataQualityEndpoint:
    """GET /api/health/data-quality — requires registry_read, requires DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/health/data-quality")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/health/data-quality")
        assert resp.status_code == 503
