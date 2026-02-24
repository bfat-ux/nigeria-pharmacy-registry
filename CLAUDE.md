# Nigeria Pharmacy Registry — Claude Code Instructions

## Mission

Build a Nigeria Pharmacy Registry that functions as a national dispensing-endpoint
infrastructure layer: continuously ingestible, progressively validated, interoperable,
and regulator-alignment ready.

## Non-Negotiables

- **No patient data.** This registry tracks dispensing LOCATIONS, not patients or transactions.
- **Provenance for every record.** Every insert, update, and status change must have a logged source, timestamp, and actor.
- **No unearned trust.** Never label a record "Verified" without logged evidence. The validation ladder is sacred.
- **Regulator-sync ready.** All schemas must accommodate future synchronization with PCN, NAFDAC, and NHIA official datasets.
- **Dual storage model.** Internal: relational + geospatial (PostGIS). Interoperability: FHIR Location/Organization mapping layer.

## Validation Ladder

| Level | Label                    | Meaning                                              |
|-------|--------------------------|------------------------------------------------------|
| L0    | Mapped                   | Record exists from an ingested data source           |
| L1    | Contact Confirmed        | Phone/email contact verified (outbound call, SMS campaign, or message) |
| L2    | Evidence Documented      | Location confirmation + optional storefront photo    |
| L3    | Regulator/Partner Verified | Cross-referenced with official or partner dataset  |
| L4    | High-Assurance (Future)  | Biometric check-in or in-person audit                |

## Verification Strategy

### L1 — SMS-First Contact Confirmation

Primary verification method is automated bulk SMS campaigns. An SMS is sent to
each pharmacy's registered phone number asking whether the facility is currently
operating. A valid reply (YES/NO/MOVED/CLOSED) from the registered number
constitutes contact confirmation — the phone answered, someone acknowledged.

- **SMS flow:** outbound message → reply parsed → auto-promote to L1
- **Retry policy:** up to 3 attempts, 48h between attempts
- **No reply = stays L0** until the next campaign cycle
- **Gateway-agnostic:** the system provides an outbox + webhook endpoints;
  any SMS provider (Africa's Talking, Twilio, bulk SMS tool) can plug in
- **All valid replies promote:** even "CLOSED" or "MOVED" confirms the contact
  works. The operating status is captured in evidence for downstream use.

### L2 — Field Evidence Collection

GPS coordinates + storefront photo via site visit. Requires L1 first.

### L3 — Regulator Cross-Reference

Batch CSV upload from PCN/NHIA/NAFDAC → composite scoring → auto-match at ≥0.90,
manual review at 0.70–0.90. Administered via admin-only API endpoints.

### L4 — High-Assurance (Future)

Biometric check-in or in-person audit. Schema exists; workflow not yet implemented.

## Project Structure

```
nigeria-pharmacy-registry/
├── CLAUDE.md                          # This file (project instructions)
├── README.md                          # Project overview
│
├── agent-01-data-architecture/        # Schema, governance, FHIR mapping
│   ├── sql/                           # DDL scripts
│   │   ├── 001_core_schema.sql
│   │   ├── 002_status_history.sql
│   │   ├── 003_provenance_audit.sql
│   │   ├── 004_geospatial.sql
│   │   ├── 005_api_keys.sql
│   │   ├── 006_verification_tasks.sql
│   │   ├── 007_regulator_staging.sql
│   │   └── 008_sms_campaigns.sql
│   ├── fhir/                          # FHIR mapping specs
│   │   ├── location_mapping.json
│   │   └── organization_mapping.json
│   └── docs/
│       └── data_governance_spec.md
│
├── agent-02-data-acquisition/         # Ingestion pipelines & source tracking
│   ├── sources/                       # Raw source metadata & attribution
│   │   └── source_registry.json
│   ├── templates/                     # Ingestion templates for partner data
│   ├── scripts/                       # ETL / ingestion scripts
│   └── docs/
│       └── coverage_report.md
│
├── agent-03-deduplication/            # Entity resolution & matching
│   ├── algorithms/                    # Matching logic
│   │   ├── name_similarity.py
│   │   ├── geo_proximity.py
│   │   └── composite_scorer.py
│   ├── config/                        # Thresholds, merge rules
│   │   └── merge_rules.yaml
│   └── docs/
│       └── dedup_methodology.md
│
├── agent-04-verification/             # Verification operations
│   ├── workflows/                     # Verification pipeline definitions
│   ├── schemas/                       # Evidence capture schemas
│   │   └── evidence_schema.json
│   └── docs/
│       ├── verification_sop.md
│       └── reverification_schedule.md
│
├── agent-05-platform-api/             # Backend & API infrastructure
│   ├── api/                           # API implementation
│   │   └── openapi.yaml              # OpenAPI 3.1 spec
│   ├── src/                           # Application source code
│   ├── middleware/                     # Rate limiting, auth, audit
│   └── docs/
│       └── api_guide.md
│
├── agent-06-policy-risk/              # Policy, risk & compliance
│   ├── risk_register.md
│   ├── ndpa_compliance_checklist.md
│   ├── threat_model.md
│   └── misuse_mitigation_playbook.md
│
├── agent-07-integration/              # Cross-workstream coordination
│   ├── architecture_overview.md       # Master architecture doc
│   ├── dependency_tracker.md
│   └── integration_reports/
│
└── agent-08-regulatory-integration/   # Regulator sync & partnerships
    ├── sync_architecture.md
    ├── partnership_playbook.md
    └── status_source_governance.md
```

## Workstream Execution Order

1. **Agent 1** — Data Architecture (foundation — everything depends on this)
2. **Agent 6** — Policy & Risk (inform design before data collection)
3. **Agent 8** — Regulatory Integration (bake PCN/NAFDAC fields into schema)
4. **Agent 2** — Data Acquisition (begin ingestion once schema is stable)
5. **Agent 3** — Deduplication (process ingested data)
6. **Agent 4** — Verification Operations (design the human workflows)
7. **Agent 5** — Platform & API (build on stable schema + verified data)
8. **Agent 7** — Integration (ongoing, grows with project)

## Design Assumptions

- Registry coverage grows incrementally (start with one state, expand).
- Verification improves over time (most records start at L0).
- Official regulatory feeds may initially be periodic CSV/Excel exports.
- Connectivity varies: design APIs for low-bandwidth environments.
- PPMVs (Patent and Proprietary Medicine Vendors) are in-scope alongside pharmacies.

## Technology Preferences

- **Database:** PostgreSQL + PostGIS
- **Language:** Python (data pipelines, entity resolution), TypeScript (API layer)
- **API Standard:** OpenAPI 3.1, optional FHIR R4 endpoints
- **Schema IDs:** UUID v4 for all internal primary keys
- **Config/Rules:** YAML for thresholds and business rules
- **Documentation:** Markdown

## Coding Standards

- All SQL uses lowercase with underscores (snake_case).
- Every table has `created_at`, `updated_at` timestamps (UTC).
- Every table has `created_by`, `updated_by` actor references.
- Status changes are NEVER in-place updates — always append to history table.
- Raw ingested data is stored separately from the canonical registry.
- All geospatial columns use SRID 4326 (WGS84).
