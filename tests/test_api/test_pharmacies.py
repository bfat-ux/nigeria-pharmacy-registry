"""Tests for pharmacy read endpoints (list, detail, nearby, stats, geojson)."""

from __future__ import annotations

from .conftest import SAMPLE_PHARMACIES


class TestListPharmacies:
    """GET /api/pharmacies — list with filters, pagination."""

    def test_returns_all_records(self, client):
        resp = client.get("/api/pharmacies")
        assert resp.status_code == 200
        data = resp.json()
        assert "meta" in data
        assert "data" in data
        assert data["meta"]["total"] == 5

    def test_pagination_limit(self, client):
        resp = client.get("/api/pharmacies?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 5
        assert data["meta"]["limit"] == 2
        assert len(data["data"]) == 2

    def test_pagination_offset(self, client):
        resp = client.get("/api/pharmacies?limit=2&offset=4")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1  # only 1 record left after offset 4

    def test_filter_by_state(self, client):
        resp = client.get("/api/pharmacies?state=Lagos")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 2
        for r in data["data"]:
            assert r["state"].lower() == "lagos"

    def test_filter_by_state_case_insensitive(self, client):
        resp = client.get("/api/pharmacies?state=lagos")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 2

    def test_filter_by_facility_type(self, client):
        resp = client.get("/api/pharmacies?facility_type=ppmv")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 1
        assert data["data"][0]["facility_type"] == "ppmv"

    def test_filter_by_source(self, client):
        resp = client.get("/api/pharmacies?source_id=src-osm")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 1

    def test_search_by_name(self, client):
        resp = client.get("/api/pharmacies?q=MedPlus")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 1
        assert "MedPlus" in data["data"][0]["facility_name"]

    def test_search_case_insensitive(self, client):
        resp = client.get("/api/pharmacies?q=medplus")
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 1

    def test_combined_filters(self, client):
        resp = client.get("/api/pharmacies?state=Lagos&facility_type=pharmacy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 2

    def test_no_results(self, client):
        resp = client.get("/api/pharmacies?state=NonExistentState")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 0
        assert data["data"] == []

    def test_contact_redaction_for_public(self, client):
        """Public tier should get redacted phone/email."""
        resp = client.get("/api/pharmacies?state=Lagos")
        assert resp.status_code == 200
        data = resp.json()
        for r in data["data"]:
            if r.get("phone"):
                # Should be redacted: +234****5678 pattern
                assert "****" in r["phone"]


class TestGetPharmacy:
    """GET /api/pharmacies/{pharmacy_id}"""

    def test_returns_record(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["pharmacy_id"] == pid
        assert data["facility_name"] == "MedPlus Pharmacy Ikeja"

    def test_404_for_missing(self, client):
        resp = client.get("/api/pharmacies/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_contact_redaction_for_public(self, client):
        pid = SAMPLE_PHARMACIES[0]["pharmacy_id"]
        resp = client.get(f"/api/pharmacies/{pid}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        if data.get("phone"):
            assert "****" in data["phone"]


class TestNearbyPharmacies:
    """GET /api/pharmacies/nearby — requires DB."""

    def test_returns_503_without_db(self, client):
        resp = client.get("/api/pharmacies/nearby?lat=6.5&lon=3.4")
        assert resp.status_code == 503


class TestStats:
    """GET /api/stats"""

    def test_returns_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert "by_state" in data
        assert "by_source" in data
        assert "by_facility_type" in data
        assert "states_covered" in data

    def test_state_counts(self, client):
        resp = client.get("/api/stats")
        by_state = resp.json()["by_state"]
        assert by_state.get("Lagos") == 2
        assert by_state.get("Kano") == 1

    def test_type_counts(self, client):
        resp = client.get("/api/stats")
        by_type = resp.json()["by_facility_type"]
        assert by_type.get("pharmacy") == 3
        assert by_type.get("ppmv") == 1
        assert by_type.get("hospital_pharmacy") == 1


class TestGeoJSON:
    """GET /api/geojson"""

    def test_returns_feature_collection(self, client):
        resp = client.get("/api/geojson")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert "features" in data

    def test_excludes_records_without_coords(self, client):
        resp = client.get("/api/geojson")
        features = resp.json()["features"]
        # Record 5 (Closed Pharmacy Ibadan) has no lat/lon
        assert len(features) == 4

    def test_feature_shape(self, client):
        resp = client.get("/api/geojson")
        feature = resp.json()["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert len(feature["geometry"]["coordinates"]) == 2
        assert "properties" in feature
        assert "pharmacy_id" in feature["properties"]

    def test_filter_by_state(self, client):
        resp = client.get("/api/geojson?state=Lagos")
        features = resp.json()["features"]
        assert len(features) == 2
        for f in features:
            assert f["properties"]["state"].lower() == "lagos"
