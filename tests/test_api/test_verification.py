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


class TestReverificationConstants:
    """Unit tests for re-verification interval and threshold constants."""

    def test_intervals_match_schedule(self):
        from agent_05_platform_api.src.helpers import REVERIFICATION_INTERVALS

        assert REVERIFICATION_INTERVALS["L1_contact_confirmed"] == 365
        assert REVERIFICATION_INTERVALS["L2_evidence_documented"] == 548
        assert REVERIFICATION_INTERVALS["L3_regulator_verified"] == 90

    def test_grace_period(self):
        from agent_05_platform_api.src.helpers import GRACE_PERIOD_DAYS

        assert GRACE_PERIOD_DAYS == 30

    def test_crossref_thresholds(self):
        from agent_05_platform_api.src.helpers import (
            CROSSREF_AUTO_APPROVE_THRESHOLD,
            CROSSREF_MANUAL_REVIEW_THRESHOLD,
        )

        assert CROSSREF_AUTO_APPROVE_THRESHOLD == 0.90
        assert CROSSREF_MANUAL_REVIEW_THRESHOLD == 0.70
        assert CROSSREF_MANUAL_REVIEW_THRESHOLD < CROSSREF_AUTO_APPROVE_THRESHOLD


class TestExpiryReport:
    """GET /api/validation/expiry-report — requires registry_read + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/validation/expiry-report")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/validation/expiry-report")
        assert resp.status_code == 503


class TestDowngradeEndpoint:
    """POST /api/pharmacies/{id}/downgrade — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/pharmacies/aaaaaaaa-0001-0001-0001-000000000001/downgrade",
            json={"reason": "Re-verification expired"},
        )
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.post(
            "/api/pharmacies/aaaaaaaa-0001-0001-0001-000000000001/downgrade",
            json={"reason": "Re-verification expired"},
        )
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post(
            "/api/pharmacies/aaaaaaaa-0001-0001-0001-000000000001/downgrade",
            json={"reason": "Re-verification expired"},
        )
        assert resp.status_code == 503


class TestDowngradeMap:
    """Unit tests for the downgrade map constants."""

    def test_covers_l1_through_l3(self):
        from agent_05_platform_api.src.helpers import DOWNGRADE_MAP

        assert DOWNGRADE_MAP["L1_contact_confirmed"] == "L0_mapped"
        assert DOWNGRADE_MAP["L2_evidence_documented"] == "L1_contact_confirmed"
        assert DOWNGRADE_MAP["L3_regulator_verified"] == "L2_evidence_documented"

    def test_l0_not_downgradable(self):
        from agent_05_platform_api.src.helpers import DOWNGRADE_MAP

        assert "L0_mapped" not in DOWNGRADE_MAP

    def test_l4_not_downgradable(self):
        from agent_05_platform_api.src.helpers import DOWNGRADE_MAP

        # L4 is not in the map — no automatic downgrade for high-assurance
        assert "L4_high_assurance" not in DOWNGRADE_MAP


class TestValidationProgress:
    """GET /api/validation/progress — public, works in JSON fallback mode."""

    def test_returns_200(self, client):
        resp = client.get("/api/validation/progress")
        assert resp.status_code == 200

    def test_json_fallback_shape(self, client):
        data = client.get("/api/validation/progress").json()
        assert data["mode"] == "json_fallback"
        assert data["total_pharmacies"] == 5
        assert "by_level" in data
        assert "verified_above_L0" in data
        assert "verified_percentage" in data

    def test_counts_match_sample_data(self, client):
        data = client.get("/api/validation/progress").json()
        # 5 sample pharmacies: 4 at L0, 1 at L1
        assert data["by_level"].get("L0_mapped") == 4
        assert data["by_level"].get("L1_contact_confirmed") == 1
        assert data["verified_above_L0"] == 1
        assert data["verified_percentage"] == 20.0

    def test_recent_activity_null_in_json_mode(self, client):
        data = client.get("/api/validation/progress").json()
        # JSON fallback doesn't have history data
        assert data["recent_activity"] is None
        assert data["pending_tasks"] is None
