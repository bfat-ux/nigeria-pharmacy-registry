"""Tests for agent-03-deduplication — composite scorer module."""

import os
import tempfile

import pytest
import yaml

from agent_03_deduplication.algorithms.composite_scorer import (
    MatchResult,
    ScorerConfig,
    compute_match,
    external_id_overlap_score,
    normalize_phone,
    phone_match_score,
    score_candidate_pairs,
)


# ---- normalize_phone --------------------------------------------------------


class TestNormalizePhone:
    def test_international_format(self):
        assert normalize_phone("+234 803 123 4567") == "8031234567"

    def test_local_format(self):
        assert normalize_phone("08031234567") == "8031234567"

    def test_dashed_format(self):
        assert normalize_phone("234-803-123-4567") == "8031234567"

    def test_none(self):
        assert normalize_phone(None) is None

    def test_empty_string(self):
        assert normalize_phone("") is None

    def test_non_nigerian_number(self):
        """Non-matching pattern returns stripped digits."""
        result = normalize_phone("12345")
        assert result == "12345"


# ---- phone_match_score ------------------------------------------------------


class TestPhoneMatchScore:
    def test_exact_match(self):
        assert phone_match_score("08031234567", "+234-803-123-4567") == 1.0

    def test_different_numbers(self):
        assert phone_match_score("08031234567", "08039876543") == 0.0

    def test_one_missing(self):
        assert phone_match_score("08031234567", None) is None
        assert phone_match_score(None, "08031234567") is None

    def test_both_missing(self):
        assert phone_match_score(None, None) is None


# ---- external_id_overlap_score ----------------------------------------------


class TestExternalIdOverlapScore:
    def test_matching_ids(self):
        ids_a = {"pcn_registration": "PCN/12345"}
        ids_b = {"pcn_registration": "pcn/12345"}  # case-insensitive
        assert external_id_overlap_score(ids_a, ids_b) == 1.0

    def test_conflicting_ids(self):
        ids_a = {"pcn_registration": "PCN/12345"}
        ids_b = {"pcn_registration": "PCN/99999"}
        assert external_id_overlap_score(ids_a, ids_b) == 0.0

    def test_no_overlapping_types(self):
        ids_a = {"pcn_registration": "PCN/12345"}
        ids_b = {"nhia_facility": "NHIA-9999"}
        assert external_id_overlap_score(ids_a, ids_b) is None

    def test_none_ids(self):
        assert external_id_overlap_score(None, {"pcn_registration": "X"}) is None
        assert external_id_overlap_score({"pcn_registration": "X"}, None) is None
        assert external_id_overlap_score(None, None) is None

    def test_empty_dicts(self):
        assert external_id_overlap_score({}, {}) is None

    def test_multiple_types_all_match(self):
        ids_a = {"pcn_registration": "PCN/123", "nhia_facility": "NHIA-999"}
        ids_b = {"pcn_registration": "PCN/123", "nhia_facility": "NHIA-999"}
        assert external_id_overlap_score(ids_a, ids_b) == 1.0

    def test_multiple_types_one_conflicts(self):
        ids_a = {"pcn_registration": "PCN/123", "nhia_facility": "NHIA-999"}
        ids_b = {"pcn_registration": "PCN/123", "nhia_facility": "NHIA-000"}
        assert external_id_overlap_score(ids_a, ids_b) == 0.0


# ---- ScorerConfig -----------------------------------------------------------


class TestScorerConfig:
    def test_defaults(self):
        config = ScorerConfig()
        assert config.weights["name"] == 0.40
        assert config.thresholds["auto_merge"] == 0.95
        assert config.same_state_required is True

    def test_from_yaml(self):
        data = {
            "weights": {"name": 0.50, "geo": 0.20, "phone": 0.15, "external_id": 0.15},
            "thresholds": {"auto_merge": 0.90, "review_queue_lower": 0.60},
            "geo_proximity": {"match_radius_km": 0.3},
            "blocking_rules": {"same_state_required": False},
            "boosts": {"same_lga": 0.10},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        try:
            config = ScorerConfig.from_yaml(path)
            assert config.weights["name"] == 0.50
            assert config.thresholds["auto_merge"] == 0.90
            assert config.thresholds["review_queue_lower"] == 0.60
            assert config.geo["match_radius_km"] == 0.3
            assert config.same_state_required is False
            assert config.same_lga_boost == 0.10
        finally:
            os.unlink(path)

    def test_from_project_yaml(self):
        """The actual merge_rules.yaml in the repo should load correctly."""
        config_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "agent-03-deduplication",
            "config",
            "merge_rules.yaml",
        )
        if os.path.exists(config_path):
            config = ScorerConfig.from_yaml(config_path)
            assert sum(config.weights.values()) == pytest.approx(1.0)


# ---- compute_match ----------------------------------------------------------

# Helper to build minimal pharmacy records
def _rec(
    pid="P1",
    name="Test Pharmacy",
    state="Lagos",
    lga="Ikeja",
    lat=6.45,
    lon=3.40,
    phone=None,
    ext_ids=None,
):
    return {
        "pharmacy_id": pid,
        "facility_name": name,
        "state": state,
        "lga": lga,
        "latitude": lat,
        "longitude": lon,
        "phone": phone,
        "external_identifiers": ext_ids,
    }


class TestComputeMatch:
    def test_identical_records_high_confidence(self):
        a = _rec(pid="A", name="Greenlife Pharmacy")
        b = _rec(pid="B", name="Greenlife Pharmacy")
        result = compute_match(a, b)
        assert result.match_confidence >= 0.90
        assert result.decision in ("auto_merge", "review")

    def test_different_states_blocked(self):
        a = _rec(pid="A", state="Lagos")
        b = _rec(pid="B", state="Kano")
        result = compute_match(a, b)
        assert result.match_confidence == 0.0
        assert result.decision == "no_match"
        assert result.override_reason == "different_state_blocked"

    def test_different_states_not_blocked_when_disabled(self):
        config = ScorerConfig(same_state_required=False)
        a = _rec(pid="A", name="Greenlife Pharmacy", state="Lagos")
        b = _rec(pid="B", name="Greenlife Pharmacy", state="Kano")
        result = compute_match(a, b, config=config)
        assert result.match_confidence > 0.0

    def test_regulator_id_override(self):
        a = _rec(pid="A", name="Alpha Pharmacy", ext_ids={"pcn_registration": "PCN/123"})
        b = _rec(pid="B", name="Beta Pharmacy", ext_ids={"pcn_registration": "PCN/123"})
        result = compute_match(a, b)
        assert result.match_confidence == 1.0
        assert result.decision == "auto_merge"
        assert "regulator_id_match" in (result.override_reason or "")

    def test_conflicting_external_ids_override(self):
        a = _rec(pid="A", ext_ids={"pcn_registration": "PCN/111"})
        b = _rec(pid="B", ext_ids={"pcn_registration": "PCN/999"})
        result = compute_match(a, b)
        assert result.match_confidence == 0.0
        assert result.decision == "no_match"
        assert result.override_reason == "conflicting_external_ids"

    def test_phone_plus_name_override(self):
        # Names must normalize with composite >= 0.80 for the override.
        # "Greenlife Pharmacy" and "Greenlife Chemist" both normalize to
        # "greenlife" (facility words stripped), giving name_score = 1.0.
        a = _rec(pid="A", name="Greenlife Pharmacy", phone="08031234567")
        b = _rec(pid="B", name="Greenlife Chemist", phone="+234-803-123-4567")
        result = compute_match(a, b)
        assert result.decision == "auto_merge"
        assert result.override_reason == "phone_exact_match_with_high_name"

    def test_completely_different_records(self):
        a = _rec(pid="A", name="Alpha Pharmacy", lat=6.45, lon=3.40)
        b = _rec(pid="B", name="Zeta Medical Store", lat=9.06, lon=7.49)
        result = compute_match(a, b)
        assert result.decision == "no_match"
        assert result.match_confidence < 0.5

    def test_missing_geo_redistributes_weights(self):
        """When one record has no coords, geo weight is redistributed."""
        a = _rec(pid="A", name="Greenlife Pharmacy", lat=None, lon=None)
        b = _rec(pid="B", name="Greenlife Pharmacy")
        result = compute_match(a, b)
        assert result.geo_score is None
        assert "geo" not in result.signals_used
        assert "name" in result.signals_used

    def test_lga_boost(self):
        """Same LGA should give a small confidence boost."""
        config = ScorerConfig(same_lga_boost=0.05)
        a = _rec(pid="A", name="Good Pharmacy", lga="Ikeja")
        b = _rec(pid="B", name="Good Pharmacy", lga="Ikeja")
        result_same = compute_match(a, b, config=config)

        config_no_boost = ScorerConfig(same_lga_boost=0.0)
        result_no_boost = compute_match(a, b, config=config_no_boost)

        assert result_same.match_confidence >= result_no_boost.match_confidence
        assert result_same.lga_boost_applied is True

    def test_different_lga_no_boost(self):
        a = _rec(pid="A", lga="Ikeja")
        b = _rec(pid="B", lga="Surulere")
        result = compute_match(a, b)
        assert result.lga_boost_applied is False

    def test_result_to_dict(self):
        a = _rec(pid="A")
        b = _rec(pid="B")
        result = compute_match(a, b)
        d = result.to_dict()
        assert d["record_a_id"] == "A"
        assert d["record_b_id"] == "B"
        assert "match_confidence" in d


# ---- score_candidate_pairs --------------------------------------------------


class TestScoreCandidatePairs:
    def test_sorted_by_confidence_descending(self):
        pairs = [
            (_rec(pid="A", name="Alpha"), _rec(pid="B", name="Zeta")),
            (_rec(pid="C", name="Greenlife"), _rec(pid="D", name="Greenlife")),
        ]
        results = score_candidate_pairs(pairs)
        confidences = [r.match_confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_empty_pairs(self):
        assert score_candidate_pairs([]) == []
