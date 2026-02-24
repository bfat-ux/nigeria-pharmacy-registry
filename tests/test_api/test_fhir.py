"""Tests for FHIR R4 interoperability endpoints."""

from __future__ import annotations


class TestFhirMetadata:
    """GET /api/fhir/metadata — CapabilityStatement, public."""

    def test_returns_capability_statement(self, client):
        resp = client.get("/api/fhir/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resourceType"] == "CapabilityStatement"
        assert data["fhirVersion"] == "4.0.1"
        assert data["status"] == "active"

    def test_declares_location_resource(self, client):
        resp = client.get("/api/fhir/metadata")
        rest = resp.json()["rest"][0]
        resource_types = [r["type"] for r in rest["resource"]]
        assert "Location" in resource_types
        assert "Organization" in resource_types

    def test_declares_search_params(self, client):
        resp = client.get("/api/fhir/metadata")
        rest = resp.json()["rest"][0]
        location = next(r for r in rest["resource"] if r["type"] == "Location")
        param_names = [p["name"] for p in location["searchParam"]]
        assert "name" in param_names
        assert "address-state" in param_names
        assert "_count" in param_names

    def test_format_is_json(self, client):
        resp = client.get("/api/fhir/metadata")
        assert "json" in resp.json()["format"]


class TestFhirLocationRead:
    """GET /api/fhir/Location/{id} — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/fhir/Location/aaaaaaaa-0001-0001-0001-000000000001")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/fhir/Location/aaaaaaaa-0001-0001-0001-000000000001")
        assert resp.status_code == 503


class TestFhirLocationSearch:
    """GET /api/fhir/Location — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/fhir/Location")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/fhir/Location")
        assert resp.status_code == 503


class TestFhirOrganizationRead:
    """GET /api/fhir/Organization/{id} — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/fhir/Organization/org-aaaaaaaa-0001-0001-0001-000000000001")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/fhir/Organization/org-aaaaaaaa-0001-0001-0001-000000000001")
        assert resp.status_code == 503


class TestFhirOrganizationSearch:
    """GET /api/fhir/Organization — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/fhir/Organization")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/fhir/Organization")
        assert resp.status_code == 503


class TestFhirResourceBuilders:
    """Unit tests for FHIR Location/Organization builder functions."""

    def test_build_fhir_location_shape(self):
        from agent_05_platform_api.src.routes.fhir import build_fhir_location

        row = {
            "id": "aaaaaaaa-0001-0001-0001-000000000001",
            "name": "Test Pharmacy",
            "facility_type": "pharmacy",
            "operational_status": "operational",
            "current_validation_level": "L0_mapped",
            "address_line_1": "123 Main St",
            "address_line_2": None,
            "ward": "Ward A",
            "lga": "Test LGA",
            "state": "Lagos",
            "country": "NG",
            "postal_code": None,
            "latitude": 6.5,
            "longitude": 3.4,
            "primary_source": "src-test",
            "updated_at": "2026-02-20T10:00:00+00:00",
        }
        result = build_fhir_location(row, [], [])

        assert result["resourceType"] == "Location"
        assert result["id"] == "aaaaaaaa-0001-0001-0001-000000000001"
        assert result["name"] == "Test Pharmacy"
        assert result["status"] == "active"
        assert result["address"]["state"] == "Lagos"
        assert result["address"]["country"] == "NG"
        assert result["position"]["latitude"] == 6.5
        assert result["position"]["longitude"] == 3.4
        assert len(result["identifier"]) >= 1  # At least the NPR ID
        assert any("pharmacy-id" in i["system"] for i in result["identifier"])

    def test_build_fhir_location_with_contacts(self):
        from agent_05_platform_api.src.routes.fhir import build_fhir_location

        row = {
            "id": "test-id",
            "name": "Test",
            "facility_type": "pharmacy",
            "operational_status": "operational",
            "current_validation_level": "L0_mapped",
            "address_line_1": None,
            "address_line_2": None,
            "ward": None,
            "lga": None,
            "state": None,
            "country": "NG",
            "postal_code": None,
            "latitude": None,
            "longitude": None,
            "primary_source": None,
            "updated_at": None,
        }
        contacts = [
            {"contact_type": "phone", "contact_value": "+2341234567", "is_primary": True},
            {"contact_type": "email", "contact_value": "test@test.ng", "is_primary": False},
        ]
        result = build_fhir_location(row, contacts, [])

        assert "telecom" in result
        assert len(result["telecom"]) == 2
        phone = next(t for t in result["telecom"] if t["system"] == "phone")
        assert phone["value"] == "+2341234567"
        assert phone["rank"] == 1

    def test_build_fhir_organization_shape(self):
        from agent_05_platform_api.src.routes.fhir import build_fhir_organization

        row = {
            "id": "test-org-id",
            "name": "Test Org Pharmacy",
            "facility_type": "ppmv",
            "operational_status": "operational",
            "address_line_1": "Addr 1",
            "address_line_2": None,
            "lga": "LGA",
            "state": "Kano",
            "country": "NG",
            "postal_code": None,
            "updated_at": None,
        }
        result = build_fhir_organization(row, [], [])

        assert result["resourceType"] == "Organization"
        assert result["id"] == "org-test-org-id"
        assert result["name"] == "Test Org Pharmacy"
        assert result["active"] is True
        assert any("PPMV" in c.get("code", "") for t in result["type"] for c in t["coding"])
