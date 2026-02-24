"""Shared fixtures for the API test suite.

All tests run in JSON fallback mode (no database required).
We seed helpers._RECORDS / _INDEX directly, and patch db.is_available() → False.

Auth injection: we patch auth._cache_get so that magic test API keys
instantly resolve to the desired AuthContext without DB lookups.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Sample pharmacy records (mirrors the JSON fallback shape)
# ---------------------------------------------------------------------------

SAMPLE_PHARMACIES: list[dict] = [
    {
        "pharmacy_id": "aaaaaaaa-0001-0001-0001-000000000001",
        "facility_name": "MedPlus Pharmacy Ikeja",
        "facility_type": "pharmacy",
        "address_line": "10 Allen Avenue, Ikeja",
        "ward": "Ikeja Ward A",
        "lga": "Ikeja",
        "state": "Lagos",
        "latitude": 6.6018,
        "longitude": 3.3515,
        "phone": "+2348012345678",
        "email": "info@medplus.com",
        "operational_status": "operational",
        "validation_level": "L0_mapped",
        "validation_label": "Mapped",
        "source_id": "src-google-places",
        "source_record_id": "google:ChIJ_test_001",
        "created_at": "2026-02-20T10:00:00+00:00",
        "updated_at": "2026-02-20T10:00:00+00:00",
    },
    {
        "pharmacy_id": "aaaaaaaa-0001-0001-0001-000000000002",
        "facility_name": "HealthRite Pharmacy Victoria Island",
        "facility_type": "pharmacy",
        "address_line": "25 Adeola Odeku, VI",
        "ward": None,
        "lga": "Eti-Osa",
        "state": "Lagos",
        "latitude": 6.4281,
        "longitude": 3.4219,
        "phone": "+2348099887766",
        "email": None,
        "operational_status": "operational",
        "validation_level": "L1_contact_confirmed",
        "validation_label": "Contact Confirmed",
        "source_id": "src-google-places",
        "source_record_id": "google:ChIJ_test_002",
        "created_at": "2026-02-20T11:00:00+00:00",
        "updated_at": "2026-02-21T09:00:00+00:00",
    },
    {
        "pharmacy_id": "aaaaaaaa-0001-0001-0001-000000000003",
        "facility_name": "Kano Central PPMV",
        "facility_type": "ppmv",
        "address_line": "5 Kofar Mata Road",
        "ward": "Kofar Mata",
        "lga": "Kano Municipal",
        "state": "Kano",
        "latitude": 12.0022,
        "longitude": 8.5167,
        "phone": None,
        "email": None,
        "operational_status": "operational",
        "validation_level": "L0_mapped",
        "validation_label": "Mapped",
        "source_id": "src-osm",
        "source_record_id": "osm:node/12345",
        "created_at": "2026-02-19T08:00:00+00:00",
        "updated_at": "2026-02-19T08:00:00+00:00",
    },
    {
        "pharmacy_id": "aaaaaaaa-0001-0001-0001-000000000004",
        "facility_name": "Abuja Hospital Pharmacy",
        "facility_type": "hospital_pharmacy",
        "address_line": "1 National Hospital Road",
        "ward": None,
        "lga": "Municipal Area Council",
        "state": "FCT",
        "latitude": 9.0579,
        "longitude": 7.4951,
        "phone": "+2349087654321",
        "email": "pharm@nationalhospital.ng",
        "operational_status": "operational",
        "validation_level": "L0_mapped",
        "validation_label": "Mapped",
        "source_id": "src-grid3",
        "source_record_id": "grid3:FCT_001",
        "created_at": "2026-02-18T14:00:00+00:00",
        "updated_at": "2026-02-18T14:00:00+00:00",
    },
    {
        "pharmacy_id": "aaaaaaaa-0001-0001-0001-000000000005",
        "facility_name": "Closed Pharmacy Ibadan",
        "facility_type": "pharmacy",
        "address_line": "Molete Road, Ibadan",
        "ward": None,
        "lga": "Ibadan South-West",
        "state": "Oyo",
        "latitude": None,
        "longitude": None,
        "phone": None,
        "email": None,
        "operational_status": "permanently_closed",
        "validation_level": "L0_mapped",
        "validation_label": "Mapped",
        "source_id": "src-google-places",
        "source_record_id": "google:ChIJ_test_005",
        "created_at": "2026-02-17T12:00:00+00:00",
        "updated_at": "2026-02-22T10:00:00+00:00",
    },
]


def _build_index(records):
    return {r["pharmacy_id"]: r for r in records}


# ---------------------------------------------------------------------------
# Magic test API keys → AuthContext mapping
# ---------------------------------------------------------------------------

_TEST_KEYS: dict[str, dict] = {}  # populated lazily


def _get_test_auth_contexts():
    """Build AuthContext instances for each tier, keyed by magic API key."""
    from agent_05_platform_api.src.auth import AuthContext, DEFAULT_SCOPES

    if _TEST_KEYS:
        return _TEST_KEYS

    for tier in ("public", "registry_read", "registry_write", "admin"):
        key = f"npr_test_{tier}_0000000000000000"
        _TEST_KEYS[key] = AuthContext(
            tier=tier,
            scopes=DEFAULT_SCOPES.get(tier, [])[:],
            actor_id=f"test:{tier}_key",
            actor_type="api_user",
            key_id=f"test-{tier}-key-id",
        )

    return _TEST_KEYS


def _patched_cache_get(api_key: str):
    """Drop-in replacement for auth._cache_get that recognises test keys."""
    contexts = _get_test_auth_contexts()
    return contexts.get(api_key)


# ---------------------------------------------------------------------------
# App fixture — seeds JSON fallback, patches DB away
# ---------------------------------------------------------------------------


def _noop_rate_limit(client_key, limit):
    """Always allow — disables rate limiting in tests."""
    return True, limit, limit - 1, 60


@pytest.fixture()
def app():
    """FastAPI app running in JSON fallback mode (no DB)."""
    with (
        patch("agent_05_platform_api.src.db.is_available", return_value=False),
        patch("agent_05_platform_api.src.db.init_pool", return_value=False),
        patch("agent_05_platform_api.src.db.close_pool"),
        patch("agent_05_platform_api.src.auth._cache_get", side_effect=_patched_cache_get),
        patch("agent_05_platform_api.src.auth._validate_key", return_value=None),
        patch("agent_05_platform_api.src.rate_limiter.check_rate_limit", side_effect=_noop_rate_limit),
    ):
        from agent_05_platform_api.src.app import app as _app
        from agent_05_platform_api.src import helpers

        # Seed JSON fallback data
        helpers._RECORDS = list(SAMPLE_PHARMACIES)
        helpers._INDEX = _build_index(helpers._RECORDS)

        # Set server_started_at on app.state (normally done in startup event)
        _app.state.server_started_at = datetime(2026, 2, 24, 0, 0, 0, tzinfo=timezone.utc)

        yield _app

        # Cleanup
        helpers._RECORDS = []
        helpers._INDEX = {}


# ---------------------------------------------------------------------------
# Client fixtures — various auth tiers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(app):
    """Unauthenticated (public tier) TestClient — no API key header."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def read_client(app):
    """registry_read tier TestClient."""
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-API-Key": "npr_test_registry_read_0000000000000000"},
    )


@pytest.fixture()
def write_client(app):
    """registry_write tier TestClient."""
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-API-Key": "npr_test_registry_write_0000000000000000"},
    )


@pytest.fixture()
def admin_client(app):
    """admin tier TestClient."""
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-API-Key": "npr_test_admin_0000000000000000"},
    )
