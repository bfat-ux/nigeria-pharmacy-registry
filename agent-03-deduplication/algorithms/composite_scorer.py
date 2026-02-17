#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Composite Deduplication Scorer

Combines name similarity, geospatial proximity, phone matching, and
external-ID overlap into a single match_confidence score (0.0–1.0).

When a signal is missing (e.g. no coordinates on one record), its weight
is redistributed proportionally among the remaining signals rather than
penalizing the pair.

Dependencies:
    pip install rapidfuzz pyyaml
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .name_similarity import compute_name_similarity
from .geo_proximity import compute_geo_proximity


# ---------------------------------------------------------------------------
# Defaults (overridden by merge_rules.yaml at runtime)
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {
    "name": 0.40,
    "geo": 0.25,
    "phone": 0.20,
    "external_id": 0.15,
}

_DEFAULT_THRESHOLDS = {
    "auto_merge": 0.95,
    "review_queue_upper": 0.95,
    "review_queue_lower": 0.70,
    "no_match": 0.70,
}

_DEFAULT_GEO = {
    "match_radius_km": 0.5,
    "decay_radius_km": 2.0,
}


# ---------------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------------

_PHONE_STRIP = re.compile(r"[^0-9]")

# Nigerian mobile prefixes after stripping country code
_NG_MOBILE_RE = re.compile(r"^(?:\+?234|0)?(\d{10})$")


def normalize_phone(phone: str | None) -> str | None:
    """
    Normalize a Nigerian phone number to a 10-digit local form.

    Examples:
        "+234 803 123 4567" → "8031234567"
        "08031234567"       → "8031234567"
        "234-803-123-4567"  → "8031234567"
    """
    if not phone:
        return None
    digits = _PHONE_STRIP.sub("", phone)
    m = _NG_MOBILE_RE.match(digits)
    if m:
        return m.group(1)
    # If it doesn't match Nigerian pattern, return stripped digits as-is
    return digits if digits else None


def phone_match_score(phone_a: str | None, phone_b: str | None) -> float | None:
    """
    Compare two phone numbers.

    Returns:
        1.0  — normalised numbers match exactly
        0.0  — normalised numbers differ
        None — one or both phones missing (indeterminate)
    """
    norm_a = normalize_phone(phone_a)
    norm_b = normalize_phone(phone_b)

    if norm_a is None or norm_b is None:
        return None

    return 1.0 if norm_a == norm_b else 0.0


# ---------------------------------------------------------------------------
# External ID matching
# ---------------------------------------------------------------------------


def external_id_overlap_score(
    ids_a: dict[str, str] | None,
    ids_b: dict[str, str] | None,
) -> float | None:
    """
    Compare external identifier dictionaries.

    Each dict maps identifier_type → identifier_value (e.g.
    {"pcn_registration": "PCN/12345", "nhia_facility": "NHIA-9999"}).

    Scoring:
        - Any shared (type, value) pair → 1.0 (hard match)
        - Shared type but different value → 0.0 (conflict — likely different entity)
        - No overlapping types → None (indeterminate)

    A single matching regulator ID (PCN, NHIA, NAFDAC) is strong enough
    to drive an auto-merge on its own, handled via override rules.
    """
    if not ids_a or not ids_b:
        return None

    common_types = set(ids_a.keys()) & set(ids_b.keys())
    if not common_types:
        return None

    matches = 0
    conflicts = 0
    for id_type in common_types:
        if ids_a[id_type].strip().upper() == ids_b[id_type].strip().upper():
            matches += 1
        else:
            conflicts += 1

    if conflicts > 0:
        # Any conflicting regulator ID is a strong negative signal
        return 0.0

    # All common types matched
    return 1.0


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------


@dataclass
class ScorerConfig:
    """Loaded scorer configuration from merge_rules.yaml."""

    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    thresholds: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_THRESHOLDS))
    geo: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_GEO))
    same_state_required: bool = True
    same_lga_boost: float = 0.05

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ScorerConfig":
        """Load configuration from a YAML file."""
        with open(path, "r") as f:
            raw = yaml.safe_load(f)

        weights = raw.get("weights", {})
        thresholds_raw = raw.get("thresholds", {})
        geo_raw = raw.get("geo_proximity", {})
        blocking = raw.get("blocking_rules", {})
        boosts = raw.get("boosts", {})

        thresholds = {
            "auto_merge": thresholds_raw.get("auto_merge", _DEFAULT_THRESHOLDS["auto_merge"]),
            "review_queue_upper": thresholds_raw.get("review_queue_upper", _DEFAULT_THRESHOLDS["review_queue_upper"]),
            "review_queue_lower": thresholds_raw.get("review_queue_lower", _DEFAULT_THRESHOLDS["review_queue_lower"]),
            "no_match": thresholds_raw.get("no_match", _DEFAULT_THRESHOLDS["no_match"]),
        }

        return cls(
            weights={
                "name": weights.get("name", _DEFAULT_WEIGHTS["name"]),
                "geo": weights.get("geo", _DEFAULT_WEIGHTS["geo"]),
                "phone": weights.get("phone", _DEFAULT_WEIGHTS["phone"]),
                "external_id": weights.get("external_id", _DEFAULT_WEIGHTS["external_id"]),
            },
            thresholds=thresholds,
            geo={
                "match_radius_km": geo_raw.get("match_radius_km", _DEFAULT_GEO["match_radius_km"]),
                "decay_radius_km": geo_raw.get("decay_radius_km", _DEFAULT_GEO["decay_radius_km"]),
            },
            same_state_required=blocking.get("same_state_required", True),
            same_lga_boost=boosts.get("same_lga", 0.05),
        )


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    """Detailed result of comparing two pharmacy records."""

    record_a_id: str
    record_b_id: str
    name_score: float
    geo_score: float | None
    geo_distance_km: float | None
    phone_score: float | None
    external_id_score: float | None
    lga_boost_applied: bool
    match_confidence: float
    decision: str  # "auto_merge" | "review" | "no_match"
    signals_used: list[str]
    override_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_a_id": self.record_a_id,
            "record_b_id": self.record_b_id,
            "name_score": self.name_score,
            "geo_score": self.geo_score,
            "geo_distance_km": self.geo_distance_km,
            "phone_score": self.phone_score,
            "external_id_score": self.external_id_score,
            "lga_boost_applied": self.lga_boost_applied,
            "match_confidence": self.match_confidence,
            "decision": self.decision,
            "signals_used": self.signals_used,
            "override_reason": self.override_reason,
        }


def _classify(confidence: float, thresholds: dict[str, float]) -> str:
    """Map a confidence score to a merge decision."""
    if confidence >= thresholds["auto_merge"]:
        return "auto_merge"
    if confidence >= thresholds["review_queue_lower"]:
        return "review"
    return "no_match"


def compute_match(
    record_a: dict[str, Any],
    record_b: dict[str, Any],
    config: ScorerConfig | None = None,
) -> MatchResult:
    """
    Compute a composite match confidence between two pharmacy records.

    Parameters
    ----------
    record_a, record_b : dict
        Canonical pharmacy records as produced by the ingestion pipeline.
        Expected keys: pharmacy_id, facility_name, state, lga, latitude,
        longitude, phone, external_identifiers.
    config : ScorerConfig, optional
        Scoring configuration. Uses defaults if not provided.

    Returns
    -------
    MatchResult with match_confidence in [0.0, 1.0] and a decision string.
    """
    if config is None:
        config = ScorerConfig()

    id_a = record_a.get("pharmacy_id", "unknown")
    id_b = record_b.get("pharmacy_id", "unknown")

    # ------------------------------------------------------------------
    # Blocking rule: different states → no match (skip expensive scoring)
    # ------------------------------------------------------------------
    if config.same_state_required:
        state_a = (record_a.get("state") or "").strip().lower()
        state_b = (record_b.get("state") or "").strip().lower()
        if state_a and state_b and state_a != state_b:
            return MatchResult(
                record_a_id=id_a,
                record_b_id=id_b,
                name_score=0.0,
                geo_score=None,
                geo_distance_km=None,
                phone_score=None,
                external_id_score=None,
                lga_boost_applied=False,
                match_confidence=0.0,
                decision="no_match",
                signals_used=[],
                override_reason="different_state_blocked",
            )

    # ------------------------------------------------------------------
    # Signal 1: Name similarity
    # ------------------------------------------------------------------
    name_result = compute_name_similarity(
        record_a.get("facility_name", ""),
        record_b.get("facility_name", ""),
    )
    name_score = name_result["composite"]

    # ------------------------------------------------------------------
    # Signal 2: Geo proximity
    # ------------------------------------------------------------------
    geo_result = compute_geo_proximity(
        record_a.get("latitude"),
        record_a.get("longitude"),
        record_b.get("latitude"),
        record_b.get("longitude"),
        match_radius_km=config.geo["match_radius_km"],
        decay_radius_km=config.geo["decay_radius_km"],
    )
    geo_score = geo_result["score"]       # float or None
    geo_dist = geo_result["distance_km"]  # float or None

    # ------------------------------------------------------------------
    # Signal 3: Phone matching
    # ------------------------------------------------------------------
    phone_score = phone_match_score(
        record_a.get("phone"),
        record_b.get("phone"),
    )

    # ------------------------------------------------------------------
    # Signal 4: External ID overlap
    # ------------------------------------------------------------------
    ext_id_score = external_id_overlap_score(
        record_a.get("external_identifiers"),
        record_b.get("external_identifiers"),
    )

    # ------------------------------------------------------------------
    # Override: exact regulator ID match → auto-merge
    # ------------------------------------------------------------------
    if ext_id_score == 1.0:
        ids_a = record_a.get("external_identifiers", {})
        ids_b = record_b.get("external_identifiers", {})
        regulator_types = {"pcn_registration", "nhia_facility", "nafdac_license"}
        common_reg = regulator_types & set(ids_a.keys()) & set(ids_b.keys())
        matching_reg = [
            t for t in common_reg
            if ids_a[t].strip().upper() == ids_b[t].strip().upper()
        ]
        if matching_reg:
            return MatchResult(
                record_a_id=id_a,
                record_b_id=id_b,
                name_score=name_score,
                geo_score=geo_score,
                geo_distance_km=geo_dist,
                phone_score=phone_score,
                external_id_score=ext_id_score,
                lga_boost_applied=False,
                match_confidence=1.0,
                decision="auto_merge",
                signals_used=["name", "external_id"],
                override_reason=f"regulator_id_match:{','.join(matching_reg)}",
            )

    # ------------------------------------------------------------------
    # Override: exact phone match + high name → auto-merge
    # ------------------------------------------------------------------
    if phone_score == 1.0 and name_score >= 0.80:
        return MatchResult(
            record_a_id=id_a,
            record_b_id=id_b,
            name_score=name_score,
            geo_score=geo_score,
            geo_distance_km=geo_dist,
            phone_score=phone_score,
            external_id_score=ext_id_score,
            lga_boost_applied=False,
            match_confidence=min(1.0, 0.50 + name_score * 0.50),
            decision="auto_merge",
            signals_used=["name", "phone"],
            override_reason="phone_exact_match_with_high_name",
        )

    # ------------------------------------------------------------------
    # Override: conflicting external IDs → no match
    # ------------------------------------------------------------------
    if ext_id_score == 0.0:
        return MatchResult(
            record_a_id=id_a,
            record_b_id=id_b,
            name_score=name_score,
            geo_score=geo_score,
            geo_distance_km=geo_dist,
            phone_score=phone_score,
            external_id_score=ext_id_score,
            lga_boost_applied=False,
            match_confidence=0.0,
            decision="no_match",
            signals_used=["external_id"],
            override_reason="conflicting_external_ids",
        )

    # ------------------------------------------------------------------
    # Weighted composite — redistribute weight of missing signals
    # ------------------------------------------------------------------
    scores: dict[str, float] = {"name": name_score}
    signals_used = ["name"]

    if geo_score is not None:
        scores["geo"] = geo_score
        signals_used.append("geo")
    if phone_score is not None:
        scores["phone"] = phone_score
        signals_used.append("phone")
    if ext_id_score is not None:
        scores["external_id"] = ext_id_score
        signals_used.append("external_id")

    # Compute available weight and redistribute
    available_weight = sum(config.weights[k] for k in scores)
    if available_weight == 0:
        composite = 0.0
    else:
        composite = sum(
            (config.weights[k] / available_weight) * scores[k]
            for k in scores
        )

    # ------------------------------------------------------------------
    # LGA boost: small bonus when records share the same LGA
    # ------------------------------------------------------------------
    lga_boost = False
    lga_a = (record_a.get("lga") or "").strip().lower()
    lga_b = (record_b.get("lga") or "").strip().lower()
    if lga_a and lga_b and lga_a == lga_b:
        composite = min(1.0, composite + config.same_lga_boost)
        lga_boost = True

    composite = round(composite, 4)
    decision = _classify(composite, config.thresholds)

    return MatchResult(
        record_a_id=id_a,
        record_b_id=id_b,
        name_score=round(name_score, 4),
        geo_score=round(geo_score, 4) if geo_score is not None else None,
        geo_distance_km=geo_dist,
        phone_score=phone_score,
        external_id_score=ext_id_score,
        lga_boost_applied=lga_boost,
        match_confidence=composite,
        decision=decision,
        signals_used=signals_used,
    )


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


def score_candidate_pairs(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    config: ScorerConfig | None = None,
) -> list[MatchResult]:
    """
    Score a list of (record_a, record_b) candidate pairs.

    Returns a list of MatchResult objects sorted by match_confidence
    descending.
    """
    results = [compute_match(a, b, config) for a, b in pairs]
    results.sort(key=lambda r: r.match_confidence, reverse=True)
    return results
