"""Tests for regulator sync endpoints — Agent 08 regulatory integration."""

from __future__ import annotations

import io

import pytest


# ===================================================================
# Upload endpoint — POST /api/regulator/upload
# ===================================================================


class TestRegulatorUpload:
    """POST /api/regulator/upload — requires admin + DB."""

    def test_requires_auth(self, client):
        """Unauthenticated requests get 401."""
        resp = client.post("/api/regulator/upload?regulator_source=pcn")
        assert resp.status_code == 401

    def test_requires_admin_not_read(self, read_client):
        """registry_read tier gets 403."""
        resp = read_client.post("/api/regulator/upload?regulator_source=pcn")
        assert resp.status_code == 403

    def test_requires_admin_not_write(self, write_client):
        """registry_write tier gets 403."""
        resp = write_client.post("/api/regulator/upload?regulator_source=pcn")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        """Admin gets 503 in JSON fallback mode (no DB)."""
        csv_content = b"premises_name,registration_number,state,facility_category\nTest Pharmacy,PCN-001,Lagos,community_pharmacy\n"
        resp = admin_client.post(
            "/api/regulator/upload?regulator_source=pcn",
            files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert resp.status_code == 503

    def test_invalid_regulator_source(self, admin_client):
        """Invalid regulator source returns 400 or 503 (no DB)."""
        csv_content = b"name,id\nTest,001\n"
        resp = admin_client.post(
            "/api/regulator/upload?regulator_source=invalid",
            files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")},
        )
        # 400 for bad source or 503 for no DB (checked first)
        assert resp.status_code in (400, 422, 503)


# ===================================================================
# Batches list — GET /api/regulator/batches
# ===================================================================


class TestRegulatorBatches:
    """GET /api/regulator/batches — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/regulator/batches")
        assert resp.status_code == 401

    def test_requires_admin_not_read(self, read_client):
        resp = read_client.get("/api/regulator/batches")
        assert resp.status_code == 403

    def test_requires_admin_not_write(self, write_client):
        resp = write_client.get("/api/regulator/batches")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/regulator/batches")
        assert resp.status_code == 503


# ===================================================================
# Batch detail — GET /api/regulator/batches/{batch_id}
# ===================================================================


class TestRegulatorBatchDetail:
    """GET /api/regulator/batches/{batch_id} — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/regulator/batches/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.get("/api/regulator/batches/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/regulator/batches/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 503


# ===================================================================
# Batch approve — POST /api/regulator/batches/{batch_id}/approve
# ===================================================================


class TestRegulatorBatchApprove:
    """POST /api/regulator/batches/{batch_id}/approve — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/approve",
            json={"dry_run": False},
        )
        assert resp.status_code == 401

    def test_requires_admin(self, write_client):
        resp = write_client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/approve",
            json={"dry_run": False},
        )
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/approve",
            json={"dry_run": False},
        )
        assert resp.status_code == 503


# ===================================================================
# Manual review — POST /api/regulator/batches/{batch_id}/review/{record_id}
# ===================================================================


class TestRegulatorReview:
    """POST /api/regulator/batches/{batch_id}/review/{record_id} — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/review/00000000-0000-0000-0000-000000000002",
            json={"action": "approve"},
        )
        assert resp.status_code == 401

    def test_requires_admin(self, write_client):
        resp = write_client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/review/00000000-0000-0000-0000-000000000002",
            json={"action": "approve"},
        )
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/review/00000000-0000-0000-0000-000000000002",
            json={"action": "approve"},
        )
        assert resp.status_code == 503

    def test_invalid_action_returns_error(self, admin_client):
        """Invalid action returns 400 or 503."""
        resp = admin_client.post(
            "/api/regulator/batches/00000000-0000-0000-0000-000000000001/review/00000000-0000-0000-0000-000000000002",
            json={"action": "maybe"},
        )
        assert resp.status_code in (400, 503)


# ===================================================================
# Unmatched — GET /api/regulator/unmatched
# ===================================================================


class TestRegulatorUnmatched:
    """GET /api/regulator/unmatched — requires admin + DB."""

    def test_requires_auth(self, client):
        resp = client.get("/api/regulator/unmatched")
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.get("/api/regulator/unmatched")
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.get("/api/regulator/unmatched")
        assert resp.status_code == 503


# ===================================================================
# Service constants — unit tests
# ===================================================================


class TestRegulatorSyncConstants:
    """Unit tests for regulator sync service constants."""

    def test_regulator_id_type_mapping(self):
        from agent_05_platform_api.src.regulator_sync import REGULATOR_ID_TYPE_MAP

        assert REGULATOR_ID_TYPE_MAP["pcn"] == "pcn_premises_id"
        assert REGULATOR_ID_TYPE_MAP["nhia"] == "nhia_facility_id"
        assert REGULATOR_ID_TYPE_MAP["nafdac"] == "nafdac_license_number"

    def test_valid_sources(self):
        from agent_05_platform_api.src.regulator_sync import VALID_SOURCES

        assert VALID_SOURCES == {"pcn", "nhia", "nafdac"}

    def test_column_maps_cover_all_sources(self):
        from agent_05_platform_api.src.regulator_sync import REGULATOR_COLUMN_MAP

        assert "pcn" in REGULATOR_COLUMN_MAP
        assert "nhia" in REGULATOR_COLUMN_MAP
        assert "nafdac" in REGULATOR_COLUMN_MAP

    def test_column_maps_have_required_keys(self):
        from agent_05_platform_api.src.regulator_sync import REGULATOR_COLUMN_MAP

        required_keys = {"name", "registration_id", "state"}
        for source, mapping in REGULATOR_COLUMN_MAP.items():
            assert required_keys.issubset(mapping.keys()), (
                f"{source} missing keys: {required_keys - mapping.keys()}"
            )


# ===================================================================
# CSV parsing — unit tests
# ===================================================================


class TestCsvParsing:
    """Unit tests for CSV parsing logic."""

    def test_parse_pcn_csv(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        csv_bytes = (
            b"premises_name,registration_number,state,facility_category\n"
            b"MedPlus Pharmacy,PCN-12345,Lagos,community_pharmacy\n"
        )
        records = parse_csv(csv_bytes, "pcn", max_records=100)
        assert len(records) == 1
        assert records[0]["raw_name"] == "MedPlus Pharmacy"
        assert records[0]["raw_registration_id"] == "PCN-12345"
        assert records[0]["raw_state"] == "Lagos"

    def test_parse_nhia_csv(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        csv_bytes = (
            b"facility_name,facility_code,state,facility_type\n"
            b"Kano General Hospital,NHIA-9001,Kano,hospital_pharmacy\n"
        )
        records = parse_csv(csv_bytes, "nhia", max_records=100)
        assert len(records) == 1
        assert records[0]["raw_name"] == "Kano General Hospital"
        assert records[0]["raw_registration_id"] == "NHIA-9001"

    def test_parse_nafdac_csv(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        csv_bytes = (
            b"outlet_name,license_number,state,license_type\n"
            b"Abuja Drug Store,NAF-5001,FCT,retail\n"
        )
        records = parse_csv(csv_bytes, "nafdac", max_records=100)
        assert len(records) == 1
        assert records[0]["raw_name"] == "Abuja Drug Store"
        assert records[0]["raw_registration_id"] == "NAF-5001"

    def test_parse_csv_skips_empty_name(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        csv_bytes = (
            b"premises_name,registration_number,state,facility_category\n"
            b",PCN-001,Lagos,community_pharmacy\n"
            b"Valid Pharmacy,PCN-002,Lagos,community_pharmacy\n"
        )
        records = parse_csv(csv_bytes, "pcn", max_records=100)
        assert len(records) == 1
        assert records[0]["raw_name"] == "Valid Pharmacy"

    def test_parse_empty_csv_raises(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        with pytest.raises(Exception):
            parse_csv(
                b"premises_name,registration_number,state,facility_category\n",
                "pcn",
                max_records=100,
            )

    def test_parse_exceeds_max_records(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        rows = "premises_name,registration_number,state,facility_category\n"
        for i in range(10):
            rows += f"Pharmacy {i},PCN-{i:05d},Lagos,community_pharmacy\n"
        with pytest.raises(Exception):
            parse_csv(rows.encode(), "pcn", max_records=5)

    def test_parse_invalid_source_raises(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        with pytest.raises(Exception):
            parse_csv(b"a,b\n1,2\n", "invalid", max_records=100)

    def test_parse_csv_preserves_raw_data(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        csv_bytes = (
            b"premises_name,registration_number,state,extra_field\n"
            b"Test Pharm,PCN-999,Lagos,some_value\n"
        )
        records = parse_csv(csv_bytes, "pcn", max_records=100)
        assert "extra_field" in records[0]["raw_data"]
        assert records[0]["raw_data"]["extra_field"] == "some_value"

    def test_parse_csv_handles_bom(self):
        from agent_05_platform_api.src.regulator_sync import parse_csv

        # UTF-8 BOM prefix
        csv_bytes = (
            b"\xef\xbb\xbfpremises_name,registration_number,state,facility_category\n"
            b"BOM Pharmacy,PCN-BOM,Lagos,community_pharmacy\n"
        )
        records = parse_csv(csv_bytes, "pcn", max_records=100)
        assert len(records) == 1
        assert records[0]["raw_name"] == "BOM Pharmacy"


# ===================================================================
# File hash — unit tests
# ===================================================================


class TestFileHash:
    """Unit tests for file hash computation."""

    def test_hash_is_deterministic(self):
        from agent_05_platform_api.src.regulator_sync import compute_file_hash

        content = b"test,data\n1,2\n"
        assert compute_file_hash(content) == compute_file_hash(content)

    def test_hash_differs_for_different_content(self):
        from agent_05_platform_api.src.regulator_sync import compute_file_hash

        assert compute_file_hash(b"a") != compute_file_hash(b"b")

    def test_hash_is_sha256_hex(self):
        from agent_05_platform_api.src.regulator_sync import compute_file_hash

        h = compute_file_hash(b"test")
        assert len(h) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in h)
