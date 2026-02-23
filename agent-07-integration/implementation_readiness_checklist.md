# Nigeria Pharmacy Registry — Implementation Readiness Checklist

> **Version:** 1.0
> **Last updated:** 2026-02-23
> **Owner:** Agent 07 — Integration

Use this checklist as the "spec-to-build" gate before implementation work is marked ready.

---

## 1) Data Model Readiness (Agent 01 + Agent 02)

- [ ] Canonical enum contract exists and is versioned (`agent-07-integration/contracts/canonical_vocabulary.json`).
- [ ] SQL enum types in `001_core_schema.sql` match canonical contract.
- [ ] Ingestion allowed enums and mapping outputs match canonical contract.
- [ ] Ingestion rejects non-canonical `facility_type` and invalid `operational_status`.
- [ ] Ingestion emits machine-readable rejection reasons and source quality counters.
- [ ] `source_registry.json` includes structured compliance metadata per source:
  - lawful basis
  - retention class
  - sharing constraints
  - PII handling requirements

## 2) API Contract Readiness (Agent 05)

- [ ] `openapi.yaml` enum schemas match canonical contract values.
- [ ] API examples use canonical values (no legacy aliases such as `L2` or `community_pharmacy`).
- [ ] Query filters (`facility_type`, `operational_status`, `min_validation_level`) align with canonical values.
- [ ] FHIR extension values for validation level align with canonical values.

## 3) Security and Compliance Readiness (Agent 05 + Agent 06)

- [ ] Middleware specs map to threat model critical/high controls.
- [ ] Public tier contact redaction behavior is specified and tested.
- [ ] Audit/provenance expectations are defined for all write-like operations.
- [ ] NDPA retention and lawful-basis obligations are operationalized in ingestion metadata.

## 4) Verification Ops Readiness (Agent 04)

- [ ] Validation ladder transitions are append-only and aligned with SQL enum values.
- [ ] Evidence schema supports L1/L2/L3 transitions and references.
- [ ] Dispute and re-verification workflows are linked to status history operations.

## 5) Integration Truthfulness Gate (Agent 07)

- [ ] Dependency tracker status reflects in-repo artifacts only (no assumed completions).
- [ ] Missing upstream artifacts are marked blocked/not-delivered.
- [ ] Open risks include integration inventory gaps and owners.

## 6) Automated Gate Checks

Run these checks before marking a milestone "ready":

```bash
python3 agent-07-integration/enum_contract_check.py
python3 -m py_compile agent-02-data-acquisition/scripts/ingest_template.py
```

## 7) Sign-off Record

| Area | Owner | Date | Status | Notes |
|------|-------|------|--------|-------|
| Data model |  |  |  |  |
| API contract |  |  |  |  |
| Security/compliance |  |  |  |  |
| Verification ops |  |  |  |  |
| Integration truthfulness |  |  |  |  |

