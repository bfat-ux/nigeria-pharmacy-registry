"""
Regulator sync service — CSV parsing, staging, matching, and L3 promotion.

Handles batch imports from PCN, NHIA, and NAFDAC regulatory sources.
Matches regulator records against existing pharmacies using the composite
scorer from Agent 03, and promotes confirmed matches to L3 via
execute_verification().
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import db
from .db import extras
from .helpers import (
    CROSSREF_AUTO_APPROVE_THRESHOLD,
    CROSSREF_MANUAL_REVIEW_THRESHOLD,
    ROOT,
    iso,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent-03 composite scorer import
# ---------------------------------------------------------------------------

_AGENT_03_ROOT = ROOT / "agent-03-deduplication"
if str(_AGENT_03_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_03_ROOT))

from algorithms.composite_scorer import (  # noqa: E402
    MatchResult,
    ScorerConfig,
    compute_match,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_SOURCES = {"pcn", "nhia", "nafdac"}

REGULATOR_ID_TYPE_MAP = {
    "pcn": "pcn_premises_id",
    "nhia": "nhia_facility_id",
    "nafdac": "nafdac_license_number",
}

# Flexible column name mappings — first match wins
REGULATOR_COLUMN_MAP: dict[str, dict[str, list[str]]] = {
    "pcn": {
        "name": ["premises_name", "facility_name", "name"],
        "registration_id": ["registration_number", "premises_id", "pcn_id"],
        "state": ["state"],
        "lga": ["lga"],
        "address": ["address", "address_line"],
        "phone": ["phone", "phone_number"],
        "facility_category": ["facility_category", "category"],
    },
    "nhia": {
        "name": ["facility_name", "name"],
        "registration_id": ["facility_code", "nhia_id", "accreditation_code"],
        "state": ["state"],
        "lga": ["lga"],
        "address": ["address"],
        "phone": ["phone"],
        "facility_category": ["facility_type"],
    },
    "nafdac": {
        "name": ["outlet_name", "facility_name", "name"],
        "registration_id": ["license_number", "nafdac_id"],
        "state": ["state"],
        "lga": ["lga"],
        "address": ["address"],
        "phone": ["phone"],
        "facility_category": ["license_type"],
    },
}


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def _resolve_column(row: dict, candidates: list[str]) -> str | None:
    """Return the value from the first matching column name, or None."""
    for col in candidates:
        val = row.get(col) or row.get(col.lower()) or row.get(col.upper())
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def parse_csv(file_content: bytes, regulator_source: str, max_records: int) -> list[dict]:
    """
    Parse a CSV file and normalize column names for the given regulator source.

    Returns list of dicts with normalized keys:
        {raw_name, raw_registration_id, raw_state, raw_lga, raw_address,
         raw_phone, raw_facility_category, raw_data}

    Raises HTTPException on parse errors or if records exceed max_records.
    """
    if regulator_source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid regulator_source '{regulator_source}'. Valid: {sorted(VALID_SOURCES)}",
        )

    col_map = REGULATOR_COLUMN_MAP[regulator_source]

    try:
        text = file_content.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        try:
            text = file_content.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Unable to decode CSV file")

    reader = csv.DictReader(io.StringIO(text))
    records: list[dict] = []

    for i, row in enumerate(reader):
        if i >= max_records:
            raise HTTPException(
                status_code=400,
                detail=f"CSV exceeds max_records limit ({max_records}). "
                f"Split the file or increase the limit.",
            )

        raw_name = _resolve_column(row, col_map["name"])
        if not raw_name:
            continue  # skip rows without a name

        records.append(
            {
                "raw_name": raw_name,
                "raw_registration_id": _resolve_column(row, col_map["registration_id"]),
                "raw_state": _resolve_column(row, col_map["state"]),
                "raw_lga": _resolve_column(row, col_map.get("lga", [])),
                "raw_address": _resolve_column(row, col_map.get("address", [])),
                "raw_phone": _resolve_column(row, col_map.get("phone", [])),
                "raw_facility_category": _resolve_column(row, col_map.get("facility_category", [])),
                "raw_data": {k: v for k, v in row.items()},
            }
        )

    if not records:
        raise HTTPException(status_code=400, detail="CSV contains no valid records")

    return records


def compute_file_hash(file_content: bytes) -> str:
    """SHA-256 hash of file content for idempotency checking."""
    return hashlib.sha256(file_content).hexdigest()


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


def create_batch(
    regulator_source: str,
    file_name: str,
    file_hash: str,
    extract_date: str | None,
    record_count: int,
    actor_id: str,
) -> str:
    """
    Insert a regulator_sync_batches row. Returns batch_id.
    Raises 409 if the same file_hash already exists.
    """
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            # Check for duplicate upload
            cur.execute(
                "SELECT id FROM regulator_sync_batches WHERE file_hash = %s",
                (file_hash,),
            )
            existing = cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"Duplicate upload — this file was already processed in batch {existing['id']}",
                )

            cur.execute(
                """
                INSERT INTO regulator_sync_batches
                    (regulator_source, file_name, file_hash, extract_date,
                     record_count, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    regulator_source,
                    file_name,
                    file_hash,
                    extract_date,
                    record_count,
                    actor_id,
                    actor_id,
                ),
            )
            return str(cur.fetchone()["id"])


def stage_records(batch_id: str, records: list[dict], regulator_source: str, actor_id: str) -> int:
    """
    Insert parsed records into regulator_staging_records.
    Returns count of records staged.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            values = []
            for rec in records:
                values.append((
                    batch_id,
                    regulator_source,
                    rec["raw_name"],
                    rec.get("raw_registration_id"),
                    rec.get("raw_state"),
                    rec.get("raw_lga"),
                    rec.get("raw_address"),
                    rec.get("raw_phone"),
                    rec.get("raw_facility_category"),
                    json.dumps(rec["raw_data"]),
                    actor_id,
                    actor_id,
                ))
            extras.execute_batch(
                cur,
                """
                INSERT INTO regulator_staging_records
                    (batch_id, regulator_source, raw_name, raw_registration_id,
                     raw_state, raw_lga, raw_address, raw_phone,
                     raw_facility_category, raw_data, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                values,
                page_size=500,
            )
    return len(records)


# ---------------------------------------------------------------------------
# Matching pipeline
# ---------------------------------------------------------------------------


def _match_by_external_id(regulator_source: str, registration_id: str, cur) -> str | None:
    """
    Check if the registration ID already exists in external_identifiers.
    Returns pharmacy_id if found, None otherwise.
    """
    id_type = REGULATOR_ID_TYPE_MAP[regulator_source]
    cur.execute(
        """
        SELECT pharmacy_id FROM external_identifiers
        WHERE identifier_type = %s AND identifier_value = %s AND is_current = true
        LIMIT 1
        """,
        (id_type, registration_id),
    )
    row = cur.fetchone()
    return str(row["pharmacy_id"]) if row else None


def _get_state_pharmacies(state: str, cur) -> list[dict]:
    """
    Query pharmacies in a given state for matching candidates.
    Returns list of dicts compatible with compute_match().
    """
    cur.execute(
        """
        SELECT
            pl.id::text AS pharmacy_id,
            pl.facility_name,
            pl.state,
            pl.lga,
            ST_Y(pl.geom::geometry) AS latitude,
            ST_X(pl.geom::geometry) AS longitude,
            c.phone,
            (
                SELECT jsonb_object_agg(ei.identifier_type, ei.identifier_value)
                FROM external_identifiers ei
                WHERE ei.pharmacy_id = pl.id AND ei.is_current = true
            ) AS external_identifiers
        FROM pharmacy_locations pl
        LEFT JOIN LATERAL (
            SELECT phone FROM contacts WHERE pharmacy_id = pl.id AND is_primary = true LIMIT 1
        ) c ON true
        WHERE lower(pl.state) = lower(%s)
        LIMIT 500
        """,
        (state,),
    )
    rows = cur.fetchall()
    result = []
    for r in rows:
        ext_ids = r.get("external_identifiers") or {}
        if isinstance(ext_ids, str):
            ext_ids = json.loads(ext_ids)
        result.append({
            "pharmacy_id": r["pharmacy_id"],
            "facility_name": r.get("facility_name", ""),
            "state": r.get("state", ""),
            "lga": r.get("lga", ""),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "phone": r.get("phone"),
            "external_identifiers": ext_ids if ext_ids else None,
        })
    return result


def match_staged_records(batch_id: str, regulator_source: str) -> dict:
    """
    Run the matching pipeline on all 'pending' records in a batch.

    Returns: {auto_matched: int, probable_match: int, no_match: int}
    """
    id_type = REGULATOR_ID_TYPE_MAP[regulator_source]
    config = ScorerConfig()

    auto_matched = 0
    probable = 0
    no_match = 0

    # Cache state → pharmacy list to avoid repeated queries
    state_cache: dict[str, list[dict]] = {}

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            # Fetch all pending staged records for this batch
            cur.execute(
                """
                SELECT id, raw_name, raw_registration_id, raw_state, raw_lga,
                       raw_phone, raw_facility_category
                FROM regulator_staging_records
                WHERE batch_id = %s AND match_status = 'pending'
                ORDER BY id
                """,
                (batch_id,),
            )
            staged = cur.fetchall()

            for rec in staged:
                rec_id = str(rec["id"])
                pharmacy_id = None
                score = 0.0
                match_details: dict = {}
                status = "no_match"

                # Step 1: Try exact external ID match
                reg_id = rec.get("raw_registration_id")
                if reg_id:
                    pharmacy_id = _match_by_external_id(regulator_source, reg_id, cur)
                    if pharmacy_id:
                        score = 1.0
                        match_details = {
                            "match_method": "external_id",
                            "id_type": id_type,
                            "id_value": reg_id,
                        }
                        status = "auto_matched"

                # Step 2: Composite scoring if no ID match
                if not pharmacy_id and rec.get("raw_state"):
                    state = rec["raw_state"]
                    if state not in state_cache:
                        state_cache[state] = _get_state_pharmacies(state, cur)

                    candidates = state_cache[state]
                    if candidates:
                        pseudo = {
                            "pharmacy_id": f"reg_{rec_id}",
                            "facility_name": rec.get("raw_name", ""),
                            "state": state,
                            "lga": rec.get("raw_lga", ""),
                            "latitude": None,
                            "longitude": None,
                            "phone": rec.get("raw_phone"),
                            "external_identifiers": {id_type: reg_id} if reg_id else None,
                        }

                        best: MatchResult | None = None
                        for cand in candidates:
                            result = compute_match(pseudo, cand, config)
                            if best is None or result.match_confidence > best.match_confidence:
                                best = result

                        if best and best.match_confidence > 0:
                            score = best.match_confidence
                            pharmacy_id = best.record_b_id
                            match_details = best.to_dict()

                            if score >= CROSSREF_AUTO_APPROVE_THRESHOLD:
                                status = "auto_matched"
                            elif score >= CROSSREF_MANUAL_REVIEW_THRESHOLD:
                                status = "probable_match"
                            else:
                                status = "no_match"
                                pharmacy_id = None  # below threshold

                # Step 3: Update staging record
                cur.execute(
                    """
                    UPDATE regulator_staging_records
                    SET match_status = %s::regulator_match_status,
                        matched_pharmacy_id = %s::uuid,
                        match_score = %s,
                        match_details = %s::jsonb,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        status,
                        pharmacy_id,
                        score if score > 0 else None,
                        json.dumps(match_details) if match_details else None,
                        rec_id,
                    ),
                )

                if status == "auto_matched":
                    auto_matched += 1
                elif status == "probable_match":
                    probable += 1
                else:
                    no_match += 1

            # Update batch aggregate counts
            cur.execute(
                """
                UPDATE regulator_sync_batches
                SET auto_matched_count = %s,
                    probable_count = %s,
                    no_match_count = %s,
                    status = 'completed',
                    updated_at = now()
                WHERE id = %s
                """,
                (auto_matched, probable, no_match, batch_id),
            )

    return {"auto_matched": auto_matched, "probable_match": probable, "no_match": no_match}


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def approve_auto_matches(batch_id: str, actor_id: str, dry_run: bool = False) -> dict:
    """
    Promote all auto_matched records in a batch to L3.
    If dry_run=True, return counts without executing.
    """
    # Import here to avoid circular dependency
    from .models import VerifyRequest
    from .routes.verification import execute_verification
    from .auth import AuthContext

    promoted = 0
    skipped = 0
    errors = 0
    error_details: list[dict] = []

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, matched_pharmacy_id, match_score, raw_registration_id,
                       raw_name, regulator_source, match_details
                FROM regulator_staging_records
                WHERE batch_id = %s AND match_status = 'auto_matched'
                ORDER BY id
                """,
                (batch_id,),
            )
            records = cur.fetchall()

    if dry_run:
        return {
            "promoted": 0,
            "eligible": len(records),
            "skipped": 0,
            "errors": 0,
            "dry_run": True,
        }

    for rec in records:
        pharmacy_id = str(rec["matched_pharmacy_id"])
        reg_source = rec["regulator_source"]
        reg_id = rec.get("raw_registration_id")
        score = rec.get("match_score", 0.0)
        id_type = REGULATOR_ID_TYPE_MAP.get(reg_source, "unknown")

        verify_req = VerifyRequest(
            target_level="L3_regulator_verified",
            evidence_type="regulator_crossref",
            capture_method="batch_crossref",
            actor_id=actor_id,
            actor_type="regulator_sync",
            source_description=f"Batch regulator sync from {reg_source} (batch {batch_id})",
            evidence_detail={
                "regulator_details": {
                    "regulator_source": f"{reg_source}_{batch_id[:8]}",
                    "regulator_record_id": reg_id or "unknown",
                    "match_score": float(score),
                    "match_type": "exact_match" if score >= 0.95 else "probable_match",
                },
            },
        )

        auth = AuthContext(
            key_id="regulator_sync",
            tier="admin",
            actor_id=actor_id,
            actor_type="regulator_sync",
            scopes=["*"],
        )

        try:
            result = execute_verification(pharmacy_id, verify_req, auth)
            history_id = result.get("history_id")

            # Insert/update external identifier
            if reg_id:
                _upsert_external_identifier(pharmacy_id, id_type, reg_id, reg_source)

            # Mark staging record as promoted
            with db.get_conn() as conn:
                with conn.cursor() as cur2:
                    cur2.execute(
                        """
                        UPDATE regulator_staging_records
                        SET match_status = 'promoted',
                            promoted = true,
                            promoted_at = now(),
                            history_id = %s::uuid,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (history_id, str(rec["id"])),
                    )
            promoted += 1

        except HTTPException as e:
            errors += 1
            error_details.append({
                "record_id": str(rec["id"]),
                "pharmacy_id": pharmacy_id,
                "error": e.detail if isinstance(e.detail, str) else str(e.detail),
            })
        except Exception as e:
            errors += 1
            error_details.append({
                "record_id": str(rec["id"]),
                "pharmacy_id": pharmacy_id,
                "error": str(e),
            })

    # Update batch promoted count
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE regulator_sync_batches
                SET promoted_count = promoted_count + %s, updated_at = now()
                WHERE id = %s
                """,
                (promoted, batch_id),
            )

    return {
        "promoted": promoted,
        "skipped": skipped,
        "errors": errors,
        "error_details": error_details[:50],
        "dry_run": False,
    }


def review_single_record(
    record_id: str,
    action: str,
    actor_id: str,
    matched_pharmacy_id: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Manual review of a probable_match or no_match record.
    """
    from .models import VerifyRequest
    from .routes.verification import execute_verification
    from .auth import AuthContext

    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, batch_id, matched_pharmacy_id, match_score,
                       raw_registration_id, raw_name, regulator_source, match_status
                FROM regulator_staging_records
                WHERE id = %s::uuid
                """,
                (record_id,),
            )
            rec = cur.fetchone()

    if not rec:
        raise HTTPException(status_code=404, detail="Staging record not found")

    if rec["match_status"] in ("promoted", "approved", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"Record already processed (status: {rec['match_status']})",
        )

    if action == "reject":
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE regulator_staging_records
                    SET match_status = 'rejected',
                        reviewed_by = %s,
                        reviewed_at = now(),
                        review_notes = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (actor_id, notes, record_id),
                )
        return {"success": True, "record_id": record_id, "action": "rejected"}

    if action == "approve":
        pharmacy_id = matched_pharmacy_id or str(rec["matched_pharmacy_id"])
        if not pharmacy_id or pharmacy_id == "None":
            raise HTTPException(
                status_code=400,
                detail="No pharmacy_id to promote — provide matched_pharmacy_id",
            )

        reg_source = rec["regulator_source"]
        reg_id = rec.get("raw_registration_id")
        score = rec.get("match_score", 0.0)
        id_type = REGULATOR_ID_TYPE_MAP.get(reg_source, "unknown")

        verify_req = VerifyRequest(
            target_level="L3_regulator_verified",
            evidence_type="regulator_crossref",
            capture_method="manual_crossref",
            actor_id=actor_id,
            actor_type="regulator_sync",
            source_description=f"Manual review from {reg_source} batch",
            evidence_detail={
                "regulator_details": {
                    "regulator_source": f"{reg_source}_manual_review",
                    "regulator_record_id": reg_id or "unknown",
                    "match_score": float(score) if score else 0.70,
                    "match_type": "probable_match",
                },
            },
        )

        auth = AuthContext(
            key_id="regulator_sync",
            tier="admin",
            actor_id=actor_id,
            actor_type="regulator_sync",
            scopes=["*"],
        )

        result = execute_verification(pharmacy_id, verify_req, auth)

        if reg_id:
            _upsert_external_identifier(pharmacy_id, id_type, reg_id, reg_source)

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE regulator_staging_records
                    SET match_status = 'promoted',
                        promoted = true,
                        promoted_at = now(),
                        matched_pharmacy_id = %s::uuid,
                        history_id = %s::uuid,
                        reviewed_by = %s,
                        reviewed_at = now(),
                        review_notes = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (pharmacy_id, result.get("history_id"), actor_id, notes, record_id),
                )

        return {
            "success": True,
            "record_id": record_id,
            "action": "approved",
            "pharmacy_id": pharmacy_id,
            "new_level": result.get("new_level"),
        }

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}'. Must be 'approve' or 'reject'.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _upsert_external_identifier(pharmacy_id: str, id_type: str, id_value: str, source: str):
    """Insert or update an external identifier for a pharmacy."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO external_identifiers
                    (pharmacy_id, identifier_type, identifier_value,
                     issuing_authority, is_current, created_at, updated_at)
                VALUES (%s::uuid, %s, %s, %s, true, now(), now())
                ON CONFLICT (pharmacy_id, identifier_type)
                DO UPDATE SET
                    identifier_value = EXCLUDED.identifier_value,
                    is_current = true,
                    updated_at = now()
                """,
                (pharmacy_id, id_type, id_value, source),
            )
