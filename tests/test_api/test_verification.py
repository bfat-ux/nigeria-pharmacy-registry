"""Tests for verification endpoints and validation ladder logic."""

from __future__ import annotations


class TestVerifyEndpoint:
    """POST /api/pharmacies/{pharmacy_id}/verify — requires DB + registry_write."""

    def test_requires_db(self, write_client):
        resp = write_client.post(
            "/api/pharmacies/aaaaaaaa-0001-0001-0001-000000000001/verify",
            json={
                "target_level": "L1_contact_confirmed",
                "evidence_type": "contact_confirmation",
                "actor_id": "test-verifier",
                "actor_type": "human_verifier",
            },
        )
        assert resp.status_code == 503
        assert "Database unavailable" in resp.json()["detail"]


class TestValidationHistory:
    """GET /api/pharmacies/{id}/validation-history — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get(
            "/api/pharmacies/aaaaaaaa-0001-0001-0001-000000000001/validation-history"
        )
        assert resp.status_code == 401

    def test_requires_db(self, read_client):
        resp = read_client.get(
            "/api/pharmacies/aaaaaaaa-0001-0001-0001-000000000001/validation-history"
        )
        assert resp.status_code == 503


class TestValidationSummary:
    """GET /api/validation/summary — public, works in both modes."""

    def test_returns_json_fallback(self, client):
        resp = client.get("/api/validation/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert data["mode"] == "json_fallback"
        assert "levels" in data

    def test_level_counts(self, client):
        resp = client.get("/api/validation/summary")
        levels = {lv["level"]: lv["count"] for lv in resp.json()["levels"]}
        assert levels.get("L0_mapped") == 4
        assert levels.get("L1_contact_confirmed") == 1

    def test_percentages(self, client):
        resp = client.get("/api/validation/summary")
        for lv in resp.json()["levels"]:
            assert "percentage" in lv
            assert 0 <= lv["percentage"] <= 100


class TestValidationLadderRules:
    """Unit tests for validation business rules (tested via helpers/constants)."""

    def test_level_ordering(self):
        from agent_05_platform_api.src.helpers import VALIDATION_LEVELS, LEVEL_INDEX

        assert LEVEL_INDEX["L0_mapped"] == 0
        assert LEVEL_INDEX["L1_contact_confirmed"] == 1
        assert LEVEL_INDEX["L2_evidence_documented"] == 2
        assert LEVEL_INDEX["L3_regulator_verified"] == 3
        assert LEVEL_INDEX["L4_high_assurance"] == 4
        assert len(VALIDATION_LEVELS) == 5

    def test_required_evidence_mapping(self):
        from agent_05_platform_api.src.helpers import REQUIRED_EVIDENCE

        assert REQUIRED_EVIDENCE["L1_contact_confirmed"] == "contact_confirmation"
        assert REQUIRED_EVIDENCE["L2_evidence_documented"] == "location_confirmation"
        assert REQUIRED_EVIDENCE["L3_regulator_verified"] == "regulator_crossref"
        assert REQUIRED_EVIDENCE["L4_high_assurance"] == "in_person_audit"
        # L0 has no required evidence (it's the starting level)
        assert "L0_mapped" not in REQUIRED_EVIDENCE

    def test_level_label_mapping(self):
        from agent_05_platform_api.src.helpers import level_label

        assert level_label("L0_mapped") == "Mapped"
        assert level_label("L1_contact_confirmed") == "Contact Confirmed"
        assert level_label("L2_evidence_documented") == "Evidence Documented"
        assert level_label("L3_regulator_verified") == "Regulator Verified"
        assert level_label("L4_high_assurance") == "High Assurance"
        assert level_label(None) == "Unknown"
        assert level_label("bogus") == "bogus"
