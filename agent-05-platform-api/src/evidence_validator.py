"""Evidence detail validation for verification ladder transitions.

Validates that evidence_detail dicts contain the required fields for each
evidence type, matching the schema in agent-04-verification/schemas/evidence_schema.json.
Uses simple dict checks — no jsonschema library dependency.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Per-type required fields and range constraints
# ---------------------------------------------------------------------------

_CONTACT_REQUIRED = {"respondent_name", "respondent_role", "facility_name_confirmed", "operating_status_confirmed"}
_CONTACT_STATUS_VALUES = {"operating", "closed", "relocated", "unknown"}

_LOCATION_REQUIRED = {"gps_latitude", "gps_longitude", "gps_accuracy_meters", "facility_operational", "signage_visible"}
_LOCATION_LAT_RANGE = (4.0, 14.0)   # Nigeria latitude bounds
_LOCATION_LON_RANGE = (2.5, 15.0)   # Nigeria longitude bounds

_REGULATOR_REQUIRED = {"regulator_source", "regulator_record_id", "match_score", "match_type"}
_REGULATOR_MATCH_TYPES = {"exact_match", "probable_match"}

_AUDIT_REQUIRED = {"audit_date", "auditor_id", "facility_open", "license_displayed"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_evidence_detail(evidence_type: str, evidence_detail: dict | None) -> list[str]:
    """Validate evidence_detail against the expected structure for *evidence_type*.

    Returns a list of human-readable error strings.  Empty list = valid.
    If *evidence_detail* is None, validation is skipped (backwards compat).
    """
    if evidence_detail is None:
        return []

    validator = _VALIDATORS.get(evidence_type)
    if validator is None:
        # Unknown evidence types are not validated (forward compat)
        return []

    return validator(evidence_detail)


# ---------------------------------------------------------------------------
# Per-type validators
# ---------------------------------------------------------------------------


def _validate_contact(detail: dict) -> list[str]:
    errors: list[str] = []
    cd = detail.get("contact_details")
    if cd is None:
        return ["contact_details is required for contact_confirmation evidence"]
    if not isinstance(cd, dict):
        return ["contact_details must be an object"]

    missing = _CONTACT_REQUIRED - cd.keys()
    if missing:
        errors.append(f"contact_details missing required fields: {', '.join(sorted(missing))}")

    status = cd.get("operating_status_confirmed")
    if status is not None and status not in _CONTACT_STATUS_VALUES:
        errors.append(
            f"operating_status_confirmed must be one of {sorted(_CONTACT_STATUS_VALUES)}, got '{status}'"
        )

    return errors


def _validate_location(detail: dict) -> list[str]:
    errors: list[str] = []
    ld = detail.get("location_details")
    if ld is None:
        return ["location_details is required for location_confirmation evidence"]
    if not isinstance(ld, dict):
        return ["location_details must be an object"]

    missing = _LOCATION_REQUIRED - ld.keys()
    if missing:
        errors.append(f"location_details missing required fields: {', '.join(sorted(missing))}")

    lat = ld.get("gps_latitude")
    if lat is not None:
        try:
            lat = float(lat)
            if not (_LOCATION_LAT_RANGE[0] <= lat <= _LOCATION_LAT_RANGE[1]):
                errors.append(
                    f"gps_latitude {lat} outside Nigeria bounds ({_LOCATION_LAT_RANGE[0]}-{_LOCATION_LAT_RANGE[1]})"
                )
        except (TypeError, ValueError):
            errors.append("gps_latitude must be a number")

    lon = ld.get("gps_longitude")
    if lon is not None:
        try:
            lon = float(lon)
            if not (_LOCATION_LON_RANGE[0] <= lon <= _LOCATION_LON_RANGE[1]):
                errors.append(
                    f"gps_longitude {lon} outside Nigeria bounds ({_LOCATION_LON_RANGE[0]}-{_LOCATION_LON_RANGE[1]})"
                )
        except (TypeError, ValueError):
            errors.append("gps_longitude must be a number")

    return errors


def _validate_regulator(detail: dict) -> list[str]:
    errors: list[str] = []
    rd = detail.get("regulator_details")
    if rd is None:
        return ["regulator_details is required for regulator_crossref evidence"]
    if not isinstance(rd, dict):
        return ["regulator_details must be an object"]

    missing = _REGULATOR_REQUIRED - rd.keys()
    if missing:
        errors.append(f"regulator_details missing required fields: {', '.join(sorted(missing))}")

    score = rd.get("match_score")
    if score is not None:
        try:
            score = float(score)
            if not (0.0 <= score <= 1.0):
                errors.append(f"match_score must be between 0.0 and 1.0, got {score}")
        except (TypeError, ValueError):
            errors.append("match_score must be a number")

    mt = rd.get("match_type")
    if mt is not None and mt not in _REGULATOR_MATCH_TYPES:
        errors.append(f"match_type must be one of {sorted(_REGULATOR_MATCH_TYPES)}, got '{mt}'")

    return errors


def _validate_audit(detail: dict) -> list[str]:
    errors: list[str] = []
    missing = _AUDIT_REQUIRED - detail.keys()
    if missing:
        errors.append(f"in_person_audit evidence missing required fields: {', '.join(sorted(missing))}")
    return errors


_VALIDATORS = {
    "contact_confirmation": _validate_contact,
    "location_confirmation": _validate_location,
    "regulator_crossref": _validate_regulator,
    "in_person_audit": _validate_audit,
}
