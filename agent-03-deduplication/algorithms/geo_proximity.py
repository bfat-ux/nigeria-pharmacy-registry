#!/usr/bin/env python3
"""
Nigeria Pharmacy Registry — Geospatial Proximity Matching

Computes distance-based similarity between pharmacy locations using the
Haversine formula.  Provides configurable radius thresholds for matching
candidates and a smooth decay function for scoring.

No external geo-libraries required — pure math with stdlib.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0

# Default thresholds tuned for Nigerian pharmacy density
DEFAULT_MATCH_RADIUS_KM = 0.5       # 500 m — urban pharmacies can be close
DEFAULT_PROBABLE_RADIUS_KM = 0.1    # 100 m — high-confidence proximity
DEFAULT_DECAY_RADIUS_KM = 2.0       # beyond this, score drops toward 0


@dataclass(frozen=True)
class Coordinate:
    """A WGS84 coordinate pair."""
    latitude: float
    longitude: float

    def is_valid(self) -> bool:
        """Check whether coordinates fall within Nigeria's bounding box."""
        return (
            3.0 <= self.latitude <= 14.0
            and 2.0 <= self.longitude <= 15.0
        )


# ---------------------------------------------------------------------------
# Core distance calculation
# ---------------------------------------------------------------------------


def haversine_km(coord_a: Coordinate, coord_b: Coordinate) -> float:
    """
    Compute the great-circle distance in kilometres between two WGS84 points
    using the Haversine formula.
    """
    lat1 = math.radians(coord_a.latitude)
    lat2 = math.radians(coord_b.latitude)
    dlat = math.radians(coord_b.latitude - coord_a.latitude)
    dlon = math.radians(coord_b.longitude - coord_a.longitude)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def geo_proximity_score(
    coord_a: Coordinate,
    coord_b: Coordinate,
    *,
    match_radius_km: float = DEFAULT_MATCH_RADIUS_KM,
    decay_radius_km: float = DEFAULT_DECAY_RADIUS_KM,
) -> float:
    """
    Compute a proximity similarity score in [0.0, 1.0].

    Scoring curve:
        - distance == 0       → 1.0
        - distance <= match_radius → linear decay from 1.0 to 0.5
        - distance <= decay_radius → linear decay from 0.5 to 0.0
        - distance >  decay_radius → 0.0

    This two-segment linear decay gives high scores to very close points
    while still providing a signal for moderately close locations.

    Parameters
    ----------
    coord_a, coord_b : Coordinate
        The two points to compare.
    match_radius_km : float
        Distance within which locations are considered strong matches.
    decay_radius_km : float
        Distance beyond which the score drops to zero.
    """
    dist = haversine_km(coord_a, coord_b)

    if dist <= match_radius_km:
        # Inner zone: 1.0 → 0.5
        if match_radius_km == 0:
            return 1.0
        return 1.0 - 0.5 * (dist / match_radius_km)

    if dist <= decay_radius_km:
        # Outer zone: 0.5 → 0.0
        span = decay_radius_km - match_radius_km
        if span == 0:
            return 0.0
        return 0.5 * (1.0 - (dist - match_radius_km) / span)

    return 0.0


def compute_geo_proximity(
    lat_a: float | None,
    lon_a: float | None,
    lat_b: float | None,
    lon_b: float | None,
    *,
    match_radius_km: float = DEFAULT_MATCH_RADIUS_KM,
    decay_radius_km: float = DEFAULT_DECAY_RADIUS_KM,
) -> dict[str, float | str | None]:
    """
    High-level geo proximity computation between two pharmacy records.

    Handles missing coordinates gracefully — when either side lacks geo data,
    the result score is None (indeterminate) rather than 0.0, so the
    composite scorer can exclude the geo component instead of penalizing.

    Returns
    -------
    dict with keys:
        - distance_km : float or None
        - score       : float [0–1] or None if coordinates missing
        - status      : 'computed' | 'missing_coords_a' | 'missing_coords_b' | 'missing_coords_both'
    """
    missing_a = lat_a is None or lon_a is None
    missing_b = lat_b is None or lon_b is None

    if missing_a and missing_b:
        return {"distance_km": None, "score": None, "status": "missing_coords_both"}
    if missing_a:
        return {"distance_km": None, "score": None, "status": "missing_coords_a"}
    if missing_b:
        return {"distance_km": None, "score": None, "status": "missing_coords_b"}

    coord_a = Coordinate(latitude=float(lat_a), longitude=float(lon_a))
    coord_b = Coordinate(latitude=float(lat_b), longitude=float(lon_b))

    dist = haversine_km(coord_a, coord_b)
    score = geo_proximity_score(
        coord_a,
        coord_b,
        match_radius_km=match_radius_km,
        decay_radius_km=decay_radius_km,
    )

    return {
        "distance_km": round(dist, 4),
        "score": round(score, 4),
        "status": "computed",
    }


# ---------------------------------------------------------------------------
# Candidate filtering
# ---------------------------------------------------------------------------


def bounding_box_filter(
    target: Coordinate,
    radius_km: float,
) -> tuple[float, float, float, float]:
    """
    Return a lat/lon bounding box that encloses a circle of the given radius
    around the target coordinate.

    Returns (min_lat, max_lat, min_lon, max_lon) in degrees.

    Useful for pre-filtering candidate records with a simple SQL WHERE clause
    before running the more expensive Haversine computation.
    """
    lat_delta = radius_km / EARTH_RADIUS_KM * (180.0 / math.pi)
    lon_delta = lat_delta / math.cos(math.radians(target.latitude))

    return (
        target.latitude - lat_delta,
        target.latitude + lat_delta,
        target.longitude - lon_delta,
        target.longitude + lon_delta,
    )


def find_nearby_candidates(
    target: Coordinate,
    candidates: list[dict],
    radius_km: float = DEFAULT_DECAY_RADIUS_KM,
    *,
    lat_key: str = "latitude",
    lon_key: str = "longitude",
    id_key: str = "pharmacy_id",
) -> list[dict]:
    """
    Filter a list of candidate records to those within radius_km of the target.

    Uses a bounding-box pre-filter then exact Haversine check.  Returns
    candidates sorted by distance (ascending), each augmented with
    '_distance_km' and '_geo_score'.

    Parameters
    ----------
    target : Coordinate
        The reference point.
    candidates : list of dict
        Candidate pharmacy records with lat/lon fields.
    radius_km : float
        Maximum distance to consider.
    lat_key, lon_key, id_key : str
        Field names in the candidate dicts.
    """
    min_lat, max_lat, min_lon, max_lon = bounding_box_filter(target, radius_km)

    nearby = []
    for rec in candidates:
        lat = rec.get(lat_key)
        lon = rec.get(lon_key)
        if lat is None or lon is None:
            continue
        lat, lon = float(lat), float(lon)
        # Bounding-box pre-filter
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        # Exact distance
        cand_coord = Coordinate(latitude=lat, longitude=lon)
        dist = haversine_km(target, cand_coord)
        if dist <= radius_km:
            score = geo_proximity_score(target, cand_coord)
            nearby.append({
                **rec,
                "_distance_km": round(dist, 4),
                "_geo_score": round(score, 4),
            })

    nearby.sort(key=lambda r: r["_distance_km"])
    return nearby
