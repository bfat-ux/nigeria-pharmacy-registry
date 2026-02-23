# Nigeria Pharmacy Registry — Synthetic End-to-End Dry Run

> **Version:** 1.0
> **Last updated:** 2026-02-23
> **Owner:** Agent 07 — Integration

This dry run validates the full integration chain:

`ingest -> dedup -> validation status update -> API retrieval -> provenance trace`

---

## 1) Preconditions

- Python 3.11+ installed.
- Ingestion template and script available under `agent-02-data-acquisition/`.
- Enum contract check passes:

```bash
python3 agent-07-integration/enum_contract_check.py
```

## 2) Synthetic Input Dataset

Create a small JSON dataset (3-5 records) with:

- 1 canonical pharmacy record
- 1 likely duplicate (same name + nearby coordinates)
- 1 invalid record (bad `facility_type`) to test rejection counters

Recommended fields:

- `facility_name`
- `facility_type`
- `state`
- `lga`
- `latitude`
- `longitude`
- `phone`
- `operational_status`
- `source_record_id`

## 3) Ingestion Run

Execute:

```bash
python3 agent-02-data-acquisition/scripts/ingest_template.py \
  --source-file <synthetic_input.json> \
  --template agent-02-data-acquisition/templates/generic_pharmacy_import.json \
  --source-id src-crowdsource-field \
  --actor "pipeline:e2e_dry_run" \
  --output-dir /tmp/npr_e2e_output
```

Check expected outputs:

- `canonical_<batch_id>.json`
- `provenance_<batch_id>.json`
- `status_history_<batch_id>.json`
- `rejected_<batch_id>.json`
- `stats_<batch_id>.json`

Assertions:

- At least 1 accepted record has `validation_level = L0_mapped`.
- At least 1 rejected record has `error_codes` populated.
- `stats.rejection_reason_counts` is non-empty if there are rejected records.

## 4) Dedup Dry Run (Logic-Level)

Use Agent 03 scoring logic on accepted records:

- confirm duplicate candidate receives score in expected band
- confirm merge/no-merge decision matches `merge_rules.yaml`

Assertion:

- duplicate pair produces deterministic decision for repeated runs.

## 5) Validation Transition Dry Run

Simulate one progression using status-history function semantics:

- initial `L0_mapped`
- transition to `L1_contact_confirmed`

Assertions:

- status history remains append-only
- current level updates consistently with latest history entry

## 6) API Retrieval Dry Run

For one promoted record, verify API response contract (or mock response):

- `facility_type` is canonical (`pharmacy|hospital_pharmacy|ppmv`)
- `validation_level` is canonical ladder value
- contact redaction behavior is correct for public tier

## 7) Provenance Trace Completeness

Trace one record across artifacts and verify:

- ingestion provenance event exists
- status change event exists
- identifiers are linkable by pharmacy ID / entity ID

## 8) Pass/Fail Criteria

Pass only if all are true:

- enum compatibility check passes
- ingestion emits expected files and machine-readable rejections
- dedup decision is deterministic for synthetic duplicate
- status ladder transitions are append-only and canonical
- API contract shape and enum values remain canonical
- provenance chain is complete for at least one record

