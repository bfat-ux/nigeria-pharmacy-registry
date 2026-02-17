#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Fuzzy Name Matching

Computes similarity scores between pharmacy/PPMV names using Levenshtein
distance and token-sort ratio, with normalizations tuned for common
Nigerian pharmacy naming patterns.

Dependencies:
    pip install rapidfuzz
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein


# ---------------------------------------------------------------------------
# Nigerian pharmacy naming noise
# ---------------------------------------------------------------------------

# Business suffixes and legal forms that carry no identity signal
_STRIP_SUFFIXES = [
    r"\bltd\.?\b",
    r"\blimited\b",
    r"\bplc\b",
    r"\b(?:nig(?:eria)?\.?)\b",
    r"\b(?:int(?:'?l|ernational)?)\b",
    r"\b&\s*sons?\b",
    r"\b&\s*daughters?\b",
    r"\b(?:ent(?:erprise)?s?\.?)\b",
    r"\bcompany\b",
    r"\bco\.?\b",
    r"\binc\.?\b",
    r"\bcorp(?:oration)?\.?\b",
    r"\bgroup\b",
    r"\bglobal\b",
    r"\bassociates?\b",
    r"\bventures?\b",
    r"\b(?:&|and)\b",
]

# Facility-type words that appear in many names but don't distinguish identity
_STRIP_FACILITY_WORDS = [
    r"\bpharmacy\b",
    r"\bpharmacies\b",
    r"\bchemist\b",
    r"\bchemists?\b",
    r"\bdrug\s*store\b",
    r"\bmedical\s*store\b",
    r"\bpatent\s*(?:medicine)?\s*(?:store|vendor|shop)\b",
    r"\bppmv\b",
    r"\bpharmaceuticals?\b",
    r"\bpharmaceutics?\b",
    r"\bmedicals?\b",
    r"\bstores?\b",
    r"\bshop\b",
    r"\boutlet\b",
    r"\bclinic\b",
    r"\bhospital\b",
]

# Common abbreviation expansions
_ABBREVIATIONS: dict[str, str] = {
    "st": "saint",
    "st.": "saint",
    "mt": "mount",
    "mt.": "mount",
    "dr": "doctor",
    "dr.": "doctor",
    "prof": "professor",
    "prof.": "professor",
    "govt": "government",
    "gen": "general",
    "hosp": "hospital",
    "natl": "national",
    "fed": "federal",
    "univ": "university",
}

_NOISE_RE = re.compile(
    "|".join(_STRIP_SUFFIXES + _STRIP_FACILITY_WORDS),
    re.IGNORECASE,
)
_MULTI_SPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def normalize_name(name: str) -> str:
    """
    Normalize a pharmacy name for comparison.

    Steps:
        1. Unicode NFKD normalisation (strip accents)
        2. Lowercase
        3. Expand common abbreviations
        4. Strip business suffixes and facility-type words
        5. Remove non-alphanumeric characters
        6. Collapse whitespace and trim
    """
    if not name:
        return ""

    # Unicode normalise — strip combining marks (accents)
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))

    text = text.lower().strip()

    # Expand abbreviations (whole-word only)
    tokens = text.split()
    tokens = [_ABBREVIATIONS.get(t, t) for t in tokens]
    text = " ".join(tokens)

    # Strip noise patterns
    text = _NOISE_RE.sub(" ", text)

    # Remove remaining punctuation
    text = _NON_ALNUM.sub(" ", text)

    # Collapse whitespace
    text = _MULTI_SPACE.sub(" ", text).strip()

    return text


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def levenshtein_similarity(a: str, b: str) -> float:
    """
    Normalised Levenshtein similarity between two strings.

    Returns a value in [0.0, 1.0] where 1.0 means identical.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    dist = Levenshtein.distance(a, b)
    return 1.0 - (dist / max_len)


def token_sort_similarity(a: str, b: str) -> float:
    """
    Token-sort ratio from rapidfuzz.

    Splits both strings into tokens, sorts them, and computes the
    Levenshtein ratio on the re-joined result.  This handles word-order
    variations ("Emeka Pharmacy" vs "Pharmacy Emeka").

    Returns a value in [0.0, 1.0].
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a, b) / 100.0


def token_set_similarity(a: str, b: str) -> float:
    """
    Token-set ratio — handles substring containment across token sets.

    Useful when one name is a superset of the other:
        "Goodwill Pharmacy Ikeja" vs "Goodwill Pharmacy"

    Returns a value in [0.0, 1.0].
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def compute_name_similarity(
    name_a: str,
    name_b: str,
    *,
    levenshtein_weight: float = 0.35,
    token_sort_weight: float = 0.40,
    token_set_weight: float = 0.25,
) -> dict[str, float]:
    """
    Compute a blended name-similarity score between two pharmacy names.

    The score is a weighted average of three metrics applied to the
    normalised name forms:
        - Normalised Levenshtein distance  (edit-distance robustness)
        - Token-sort ratio                 (word-order invariance)
        - Token-set ratio                  (substring/superset tolerance)

    Parameters
    ----------
    name_a, name_b : str
        Raw pharmacy names (pre-normalisation is handled internally).
    levenshtein_weight : float
        Weight for the Levenshtein component. Default 0.35.
    token_sort_weight : float
        Weight for the token-sort component. Default 0.40.
    token_set_weight : float
        Weight for the token-set component. Default 0.25.

    Returns
    -------
    dict with keys:
        - name_a_normalized, name_b_normalized: the cleaned names
        - levenshtein: float [0–1]
        - token_sort: float [0–1]
        - token_set: float [0–1]
        - composite: weighted average float [0–1]
    """
    norm_a = normalize_name(name_a)
    norm_b = normalize_name(name_b)

    lev = levenshtein_similarity(norm_a, norm_b)
    tsort = token_sort_similarity(norm_a, norm_b)
    tset = token_set_similarity(norm_a, norm_b)

    composite = (
        levenshtein_weight * lev
        + token_sort_weight * tsort
        + token_set_weight * tset
    )

    return {
        "name_a_normalized": norm_a,
        "name_b_normalized": norm_b,
        "levenshtein": round(lev, 4),
        "token_sort": round(tsort, 4),
        "token_set": round(tset, 4),
        "composite": round(composite, 4),
    }


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def quick_name_score(name_a: str, name_b: str) -> float:
    """Return only the composite name similarity score (0.0–1.0)."""
    return compute_name_similarity(name_a, name_b)["composite"]


def names_are_similar(
    name_a: str,
    name_b: str,
    threshold: float = 0.70,
) -> bool:
    """Return True if the two names exceed the similarity threshold."""
    return quick_name_score(name_a, name_b) >= threshold
