"""Tests for agent-03-deduplication — geospatial proximity module."""

import math

import pytest

from agent_03_deduplication.algorithms.geo_proximity import (
    Coordinate,
    bounding_box_filter,
    compute_geo_proximity,
    find_nearby_candidates,
    geo_proximity_score,
    haversine_km,
)


# ---- Coordinate -------------------------------------------------------------


class TestCoordinate:
    def test_valid_nigerian_coordinate(self):
        """Lagos: 6.45, 3.40 — well within Nigeria."""
        coord = Coordinate(latitude=6.45, longitude=3.40)
        assert coord.is_valid()

    def test_valid_boundary(self):
        """Northern Nigeria edge."""
        coord = Coordinate(latitude=13.5, longitude=10.0)
        assert coord.is_valid()

    def test_invalid_outside_nigeria(self):
        """London is not in Nigeria."""
        coord = Coordinate(latitude=51.5, longitude=-0.12)
        assert not coord.is_valid()

    def test_frozen(self):
        coord = Coordinate(latitude=6.0, longitude=3.0)
        with pytest.raises(AttributeError):
            coord.latitude = 7.0  # type: ignore[misc]


# ---- haversine_km -----------------------------------------------------------


class TestHaversineKm:
    def test_same_point_is_zero(self):
        coord = Coordinate(6.45, 3.40)
        assert haversine_km(coord, coord) == 0.0

    def test_known_distance_lagos_to_abuja(self):
        """Lagos (6.45, 3.40) to Abuja (9.06, 7.49) is roughly 530 km."""
        lagos = Coordinate(6.45, 3.40)
        abuja = Coordinate(9.06, 7.49)
        dist = haversine_km(lagos, abuja)
        assert 500.0 < dist < 560.0

    def test_short_distance(self):
        """Two points ~100m apart on Victoria Island, Lagos."""
        a = Coordinate(6.4281, 3.4219)
        b = Coordinate(6.4290, 3.4219)
        dist = haversine_km(a, b)
        assert dist < 0.15  # should be ~100m

    def test_symmetry(self):
        a = Coordinate(6.45, 3.40)
        b = Coordinate(9.06, 7.49)
        assert haversine_km(a, b) == pytest.approx(haversine_km(b, a))


# ---- geo_proximity_score ----------------------------------------------------


class TestGeoProximityScore:
    def test_same_point_scores_one(self):
        coord = Coordinate(6.45, 3.40)
        assert geo_proximity_score(coord, coord) == 1.0

    def test_within_match_radius(self):
        """A point 200m away should score between 0.5 and 1.0."""
        a = Coordinate(6.4500, 3.4200)
        b = Coordinate(6.4518, 3.4200)  # ~200m north
        score = geo_proximity_score(a, b, match_radius_km=0.5)
        assert 0.5 < score < 1.0

    def test_at_match_radius_boundary(self):
        """At exactly the match radius, score should be ~0.5."""
        a = Coordinate(6.45, 3.42)
        # Create a point exactly 0.5 km away
        lat_offset = 0.5 / 6371.0 * (180.0 / math.pi)
        b = Coordinate(6.45 + lat_offset, 3.42)
        score = geo_proximity_score(a, b, match_radius_km=0.5, decay_radius_km=2.0)
        assert score == pytest.approx(0.5, abs=0.05)

    def test_beyond_decay_radius_scores_zero(self):
        """Far away points should score 0."""
        lagos = Coordinate(6.45, 3.40)
        abuja = Coordinate(9.06, 7.49)
        assert geo_proximity_score(lagos, abuja) == 0.0

    def test_between_match_and_decay(self):
        """A point 1 km away (between 0.5 and 2.0 defaults) should score 0 < s < 0.5."""
        a = Coordinate(6.45, 3.42)
        lat_offset = 1.0 / 6371.0 * (180.0 / math.pi)
        b = Coordinate(6.45 + lat_offset, 3.42)
        score = geo_proximity_score(a, b)
        assert 0.0 < score < 0.5


# ---- compute_geo_proximity --------------------------------------------------


class TestComputeGeoProximity:
    def test_returns_expected_keys(self):
        result = compute_geo_proximity(6.45, 3.40, 6.46, 3.41)
        assert set(result.keys()) == {"distance_km", "score", "status"}

    def test_computed_status(self):
        result = compute_geo_proximity(6.45, 3.40, 6.46, 3.41)
        assert result["status"] == "computed"
        assert result["distance_km"] is not None
        assert result["score"] is not None

    def test_missing_coords_a(self):
        result = compute_geo_proximity(None, None, 6.46, 3.41)
        assert result["status"] == "missing_coords_a"
        assert result["score"] is None

    def test_missing_coords_b(self):
        result = compute_geo_proximity(6.45, 3.40, None, None)
        assert result["status"] == "missing_coords_b"
        assert result["score"] is None

    def test_missing_coords_both(self):
        result = compute_geo_proximity(None, None, None, None)
        assert result["status"] == "missing_coords_both"

    def test_partial_missing_lat(self):
        result = compute_geo_proximity(6.45, None, 6.46, 3.41)
        assert result["status"] == "missing_coords_a"


# ---- bounding_box_filter ----------------------------------------------------


class TestBoundingBoxFilter:
    def test_box_encloses_radius(self):
        target = Coordinate(6.45, 3.40)
        min_lat, max_lat, min_lon, max_lon = bounding_box_filter(target, 1.0)
        assert min_lat < target.latitude < max_lat
        assert min_lon < target.longitude < max_lon

    def test_box_symmetric(self):
        target = Coordinate(6.45, 3.40)
        min_lat, max_lat, min_lon, max_lon = bounding_box_filter(target, 1.0)
        assert target.latitude - min_lat == pytest.approx(max_lat - target.latitude)


# ---- find_nearby_candidates -------------------------------------------------


class TestFindNearbyCandidates:
    @pytest.fixture()
    def candidates(self):
        """Three pharmacies around Lagos Island."""
        return [
            {"pharmacy_id": "A", "latitude": 6.4500, "longitude": 3.4200},  # target
            {"pharmacy_id": "B", "latitude": 6.4510, "longitude": 3.4205},  # ~120m
            {"pharmacy_id": "C", "latitude": 6.5000, "longitude": 3.4200},  # ~5.5km
            {"pharmacy_id": "D", "latitude": 9.0600, "longitude": 7.4900},  # Abuja
        ]

    def test_finds_nearby(self, candidates):
        target = Coordinate(6.4500, 3.4200)
        nearby = find_nearby_candidates(target, candidates, radius_km=2.0)
        ids = [r["pharmacy_id"] for r in nearby]
        assert "A" in ids
        assert "B" in ids
        assert "C" not in ids
        assert "D" not in ids

    def test_sorted_by_distance(self, candidates):
        target = Coordinate(6.4500, 3.4200)
        nearby = find_nearby_candidates(target, candidates, radius_km=2.0)
        distances = [r["_distance_km"] for r in nearby]
        assert distances == sorted(distances)

    def test_augmented_fields(self, candidates):
        target = Coordinate(6.4500, 3.4200)
        nearby = find_nearby_candidates(target, candidates, radius_km=2.0)
        for r in nearby:
            assert "_distance_km" in r
            assert "_geo_score" in r

    def test_skips_missing_coords(self):
        candidates = [
            {"pharmacy_id": "X", "latitude": None, "longitude": 3.42},
        ]
        target = Coordinate(6.45, 3.42)
        assert find_nearby_candidates(target, candidates) == []

    def test_empty_candidates(self):
        target = Coordinate(6.45, 3.42)
        assert find_nearby_candidates(target, []) == []
