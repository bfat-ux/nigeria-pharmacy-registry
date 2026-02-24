"""Tests for evidence detail validation logic."""

from __future__ import annotations

from agent_05_platform_api.src.evidence_validator import validate_evidence_detail


# ---------------------------------------------------------------------------
# Helpers — sample valid evidence dicts
# ---------------------------------------------------------------------------

def _valid_contact_evidence():
    return {
        "contact_details": {
            "respondent_name": "Adamu Yusuf",
            "respondent_role": "pharmacist",
            "facility_name_confirmed": "MedPlus Pharmacy",
            "operating_status_confirmed": "operating",
        },
    }


def _valid_location_evidence():
    return {
        "location_details": {
            "gps_latitude": 6.6018,
            "gps_longitude": 3.3515,
            "gps_accuracy_meters": 5.0,
            "facility_operational": True,
            "signage_visible": True,
        },
    }


def _valid_regulator_evidence(score=0.95):
    return {
        "regulator_details": {
            "regulator_source": "pcn_2025_q4",
            "regulator_record_id": "PCN-12345",
            "match_score": score,
            "match_type": "exact_match",
        },
    }


def _valid_audit_evidence():
    return {
        "audit_date": "2026-02-20",
        "auditor_id": "auditor-001",
        "facility_open": True,
        "license_displayed": True,
    }


# ---------------------------------------------------------------------------
# contact_confirmation
# ---------------------------------------------------------------------------

class TestContactConfirmationValidation:
    def test_valid_passes(self):
        errors = validate_evidence_detail("contact_confirmation", _valid_contact_evidence())
        assert errors == []

    def test_missing_contact_details(self):
        errors = validate_evidence_detail("contact_confirmation", {})
        assert len(errors) == 1
        assert "contact_details is required" in errors[0]

    def test_missing_required_fields(self):
        detail = {"contact_details": {"respondent_name": "Ade"}}
        errors = validate_evidence_detail("contact_confirmation", detail)
        assert len(errors) == 1
        assert "missing required fields" in errors[0]
        assert "respondent_role" in errors[0]

    def test_invalid_operating_status(self):
        detail = _valid_contact_evidence()
        detail["contact_details"]["operating_status_confirmed"] = "maybe"
        errors = validate_evidence_detail("contact_confirmation", detail)
        assert any("operating_status_confirmed" in e for e in errors)

    def test_extra_fields_ignored(self):
        detail = _valid_contact_evidence()
        detail["contact_details"]["extra_field"] = "no problem"
        errors = validate_evidence_detail("contact_confirmation", detail)
        assert errors == []


# ---------------------------------------------------------------------------
# location_confirmation
# ---------------------------------------------------------------------------

class TestLocationConfirmationValidation:
    def test_valid_passes(self):
        errors = validate_evidence_detail("location_confirmation", _valid_location_evidence())
        assert errors == []

    def test_missing_location_details(self):
        errors = validate_evidence_detail("location_confirmation", {})
        assert len(errors) == 1
        assert "location_details is required" in errors[0]

    def test_missing_gps_fields(self):
        detail = {"location_details": {"facility_operational": True, "signage_visible": True}}
        errors = validate_evidence_detail("location_confirmation", detail)
        assert any("missing required fields" in e for e in errors)

    def test_latitude_out_of_nigeria_bounds(self):
        detail = _valid_location_evidence()
        detail["location_details"]["gps_latitude"] = 50.0  # way outside Nigeria
        errors = validate_evidence_detail("location_confirmation", detail)
        assert any("outside Nigeria bounds" in e for e in errors)

    def test_longitude_out_of_nigeria_bounds(self):
        detail = _valid_location_evidence()
        detail["location_details"]["gps_longitude"] = 1.0  # west of Nigeria
        errors = validate_evidence_detail("location_confirmation", detail)
        assert any("outside Nigeria bounds" in e for e in errors)

    def test_boundary_values_accepted(self):
        detail = _valid_location_evidence()
        detail["location_details"]["gps_latitude"] = 4.0  # min bound
        detail["location_details"]["gps_longitude"] = 15.0  # max bound
        errors = validate_evidence_detail("location_confirmation", detail)
        assert errors == []


# ---------------------------------------------------------------------------
# regulator_crossref
# ---------------------------------------------------------------------------

class TestRegulatorCrossrefValidation:
    def test_valid_passes(self):
        errors = validate_evidence_detail("regulator_crossref", _valid_regulator_evidence())
        assert errors == []

    def test_missing_regulator_details(self):
        errors = validate_evidence_detail("regulator_crossref", {})
        assert len(errors) == 1
        assert "regulator_details is required" in errors[0]

    def test_missing_required_fields(self):
        detail = {"regulator_details": {"regulator_source": "pcn_2025_q4"}}
        errors = validate_evidence_detail("regulator_crossref", detail)
        assert any("missing required fields" in e for e in errors)

    def test_match_score_out_of_range(self):
        detail = _valid_regulator_evidence()
        detail["regulator_details"]["match_score"] = 1.5
        errors = validate_evidence_detail("regulator_crossref", detail)
        assert any("between 0.0 and 1.0" in e for e in errors)

    def test_invalid_match_type(self):
        detail = _valid_regulator_evidence()
        detail["regulator_details"]["match_type"] = "fuzzy_match"
        errors = validate_evidence_detail("regulator_crossref", detail)
        assert any("match_type" in e for e in errors)


# ---------------------------------------------------------------------------
# in_person_audit
# ---------------------------------------------------------------------------

class TestInPersonAuditValidation:
    def test_valid_passes(self):
        errors = validate_evidence_detail("in_person_audit", _valid_audit_evidence())
        assert errors == []

    def test_missing_required_fields(self):
        errors = validate_evidence_detail("in_person_audit", {"audit_date": "2026-02-20"})
        assert any("missing required fields" in e for e in errors)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestNoneEvidenceDetail:
    """None evidence_detail is accepted for backwards compatibility."""

    def test_none_passes_for_all_types(self):
        for etype in ("contact_confirmation", "location_confirmation", "regulator_crossref", "in_person_audit"):
            assert validate_evidence_detail(etype, None) == []

    def test_unknown_evidence_type_passes(self):
        assert validate_evidence_detail("unknown_type", {"foo": "bar"}) == []
