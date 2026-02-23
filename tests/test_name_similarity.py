"""Tests for agent-03-deduplication — name similarity module."""

import pytest

from agent_03_deduplication.algorithms.name_similarity import (
    compute_name_similarity,
    levenshtein_similarity,
    names_are_similar,
    normalize_name,
    quick_name_score,
    token_set_similarity,
    token_sort_similarity,
)


# ---- normalize_name ---------------------------------------------------------


class TestNormalizeName:
    def test_empty_and_none(self):
        assert normalize_name("") == ""
        assert normalize_name("   ") == ""

    def test_lowercase(self):
        assert normalize_name("EMEKA") == "emeka"

    def test_strips_business_suffixes(self):
        result = normalize_name("Emeka Ventures Ltd.")
        assert "ltd" not in result
        assert "ventures" not in result
        assert "emeka" in result

    def test_strips_facility_words(self):
        result = normalize_name("Goodwill Pharmacy Ikeja")
        assert "pharmacy" not in result
        assert "goodwill" in result
        assert "ikeja" in result

    def test_expands_abbreviations(self):
        assert "saint" in normalize_name("St. Mary")
        assert "doctor" in normalize_name("Dr. Bello")
        assert "mount" in normalize_name("Mt. Zion")

    def test_strips_accents(self):
        result = normalize_name("Café Médical")
        assert "cafe" in result
        # accent-stripped form of "médical" minus the "medical" facility word
        # "medical" is stripped as a facility word, leaving "cafe"
        assert result == "cafe"

    def test_strips_punctuation(self):
        # Apostrophe is stripped; "& sons" is stripped as a unit but
        # standalone "sons" survives since the regex matches "\b&\s*sons?\b"
        result = normalize_name("Emeka's & Sons")
        assert "emeka" in result
        assert "&" not in result

    def test_collapses_whitespace(self):
        result = normalize_name("  Emeka    Drug   Store  ")
        assert "  " not in result
        assert result == "emeka"

    def test_nigerian_naming_patterns(self):
        """Business suffixes common in Nigeria should be stripped."""
        assert normalize_name("Greenlife Nig. Ltd.") == "greenlife"
        assert normalize_name("Pharma Int'l Enterprises") == "pharma"
        assert normalize_name("Healthway Nigeria Limited") == "healthway"


# ---- levenshtein_similarity -------------------------------------------------


class TestLevenshteinSimilarity:
    def test_identical(self):
        assert levenshtein_similarity("emeka", "emeka") == 1.0

    def test_both_empty(self):
        assert levenshtein_similarity("", "") == 1.0

    def test_one_empty(self):
        assert levenshtein_similarity("emeka", "") == 0.0
        assert levenshtein_similarity("", "emeka") == 0.0

    def test_completely_different(self):
        score = levenshtein_similarity("abc", "xyz")
        assert score < 0.5

    def test_minor_edit(self):
        score = levenshtein_similarity("emeka", "emaka")
        assert score > 0.7


# ---- token_sort_similarity --------------------------------------------------


class TestTokenSortSimilarity:
    def test_word_order_invariance(self):
        """Swapped word order should score very high."""
        score = token_sort_similarity("goodwill ikeja", "ikeja goodwill")
        assert score > 0.95

    def test_identical(self):
        assert token_sort_similarity("hello", "hello") == 1.0

    def test_both_empty(self):
        assert token_sort_similarity("", "") == 1.0

    def test_one_empty(self):
        assert token_sort_similarity("hello", "") == 0.0


# ---- token_set_similarity ---------------------------------------------------


class TestTokenSetSimilarity:
    def test_superset_tolerance(self):
        """One name being a superset should still score high."""
        score = token_set_similarity("goodwill ikeja", "goodwill")
        assert score > 0.8

    def test_identical(self):
        assert token_set_similarity("hello", "hello") == 1.0

    def test_both_empty(self):
        assert token_set_similarity("", "") == 1.0


# ---- compute_name_similarity ------------------------------------------------


class TestComputeNameSimilarity:
    def test_returns_expected_keys(self):
        result = compute_name_similarity("Emeka Pharmacy", "Emeka Chemist")
        expected_keys = {
            "name_a_normalized",
            "name_b_normalized",
            "levenshtein",
            "token_sort",
            "token_set",
            "composite",
        }
        assert set(result.keys()) == expected_keys

    def test_identical_names_high_score(self):
        result = compute_name_similarity("Goodwill Pharmacy", "Goodwill Pharmacy")
        assert result["composite"] == 1.0

    def test_same_entity_different_suffixes(self):
        """Same pharmacy with different business suffixes should match well."""
        result = compute_name_similarity(
            "Greenlife Pharmacy Ltd.",
            "Greenlife Chemist Nig. Limited",
        )
        assert result["composite"] > 0.8

    def test_completely_different_names(self):
        result = compute_name_similarity("Adekunle Pharmacy", "Zainab Medical Store")
        assert result["composite"] < 0.5

    def test_custom_weights(self):
        """Custom weights should be respected."""
        result = compute_name_similarity(
            "Emeka Pharmacy",
            "Emeka Chemist",
            levenshtein_weight=1.0,
            token_sort_weight=0.0,
            token_set_weight=0.0,
        )
        assert result["composite"] == result["levenshtein"]

    def test_scores_are_bounded(self):
        result = compute_name_similarity("any name", "another name")
        for key in ("levenshtein", "token_sort", "token_set", "composite"):
            assert 0.0 <= result[key] <= 1.0


# ---- convenience helpers ----------------------------------------------------


class TestConvenienceHelpers:
    def test_quick_name_score_returns_float(self):
        score = quick_name_score("Emeka Pharmacy", "Emeka Chemist")
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_names_are_similar_default_threshold(self):
        assert names_are_similar("Greenlife Pharmacy", "Greenlife Pharmacy Ltd")
        assert not names_are_similar("Alpha Pharmacy", "Omega Medical Store")

    def test_names_are_similar_custom_threshold(self):
        # "Emeka Pharmacy" and "Emeka Chemist" both normalize to "emeka"
        # (facility words stripped), so they score 1.0 — use different names.
        # Very strict threshold
        assert not names_are_similar("Alpha", "Beta", threshold=0.99)
        # Very loose threshold
        assert names_are_similar("Goodwill", "Goodwill Ikeja", threshold=0.5)
