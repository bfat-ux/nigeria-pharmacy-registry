"""Tests for export endpoints (CSV, JSON, ndjson, summary)."""

from __future__ import annotations


class TestExportCSV:
    """GET /api/export/pharmacies.csv — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/export/pharmacies.csv")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/export/pharmacies.csv")
        assert resp.status_code == 503


class TestExportJSON:
    """GET /api/export/pharmacies.json — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/export/pharmacies.json")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/export/pharmacies.json")
        assert resp.status_code == 503


class TestExportFhirNdjson:
    """GET /api/export/fhir/Location.ndjson — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/export/fhir/Location.ndjson")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/export/fhir/Location.ndjson")
        assert resp.status_code == 503


class TestExportSummary:
    """GET /api/export/summary — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/export/summary")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/export/summary")
        assert resp.status_code == 503
