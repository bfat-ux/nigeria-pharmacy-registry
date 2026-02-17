"""Nigeria Pharmacy Registry — Deduplication Algorithms."""

from .name_similarity import (
    compute_name_similarity,
    normalize_name,
    quick_name_score,
    names_are_similar,
)
from .geo_proximity import (
    Coordinate,
    compute_geo_proximity,
    haversine_km,
    find_nearby_candidates,
)
from .composite_scorer import (
    MatchResult,
    ScorerConfig,
    compute_match,
    score_candidate_pairs,
    normalize_phone,
    phone_match_score,
    external_id_overlap_score,
)

__all__ = [
    "compute_name_similarity",
    "normalize_name",
    "quick_name_score",
    "names_are_similar",
    "Coordinate",
    "compute_geo_proximity",
    "haversine_km",
    "find_nearby_candidates",
    "MatchResult",
    "ScorerConfig",
    "compute_match",
    "score_candidate_pairs",
    "normalize_phone",
    "phone_match_score",
    "external_id_overlap_score",
]
