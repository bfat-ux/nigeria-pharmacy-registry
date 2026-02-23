# Nigeria Pharmacy Registry — Cross-Agent Dependency Tracker

> **Version:** 1.1
> **Last updated:** 2026-02-23
> **Owner:** Agent 07 — Integration

---

## 1. Agent Inventory

| Agent | Name | Deliverables | Status |
|-------|------|-------------|--------|
| 01 | Data Architecture | 6 files (4 SQL, 2 FHIR JSON, 1 governance doc) | Complete |
| 02 | Data Acquisition | 7 files (1 ETL script, 3 templates, 1 source registry, 1 coverage report) | Complete |
| 03 | Deduplication | 5 files (3 algorithms, 1 config, 1 methodology doc) | Complete |
| 04 | Verification | 4 files (1 evidence schema, 1 dispute workflow, 2 SOP docs) | Complete |
| 05 | Platform & API | 5 files (1 OpenAPI spec, 1 API guide, 3 middleware specs) | Complete |
| 06 | Policy & Risk | 4 files (risk register, NDPA checklist, threat model, misuse playbook) | Complete |
| 07 | Integration | 2 files (architecture overview, dependency tracker) | In Progress |
| 08 | Regulatory Integration | 0 files in repository (expected: sync architecture, partnership playbook, status governance) | Not delivered (blocked) |

---

## 2. Dependency Matrix

Each cell shows whether the **row agent** depends on the **column agent**.

| Depends on -> | Agt 01 | Agt 02 | Agt 03 | Agt 04 | Agt 05 | Agt 06 | Agt 07 | Agt 08 |
|---------------|--------|--------|--------|--------|--------|--------|--------|--------|
| **Agt 01** Data Architecture | — | | | | | D6.1 | | D8.1 |
| **Agt 02** Data Acquisition | **D2.1** | — | | | | D6.2 | | |
| **Agt 03** Deduplication | **D3.1** | **D3.2** | — | | | | | |
| **Agt 04** Verification | **D4.1** | | | — | | D6.3 | | D4.2 |
| **Agt 05** Platform & API | **D5.1** | | | D5.2 | — | **D5.3** | | D5.4 |
| **Agt 06** Policy & Risk | D6.4 | | | | | — | | |
| **Agt 07** Integration | D7.1 | D7.2 | D7.3 | D7.4 | D7.5 | D7.6 | — | D7.7 |
| **Agt 08** Regulatory Integration | **D8.2** | | | D8.3 | | D6.5 | | — |

**Bold** = blocking dependency (work cannot proceed without it).
Regular = informing dependency (enriches but does not block).

---

## 3. Dependency Details

### D2.1 — Agent 02 depends on Agent 01 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 01: `001_core_schema.sql`, `003_provenance_audit.sql` |
| **Downstream** | Agent 02: `ingest_template.py`, all ingestion templates |
| **Nature** | Ingestion pipeline writes to `raw_ingested_records` table and `provenance_records`. Schema must be stable before ingestion begins. |
| **Specific tables** | `raw_ingested_records`, `provenance_records` |
| **Risk** | Schema changes after ingestion starts require migration of staging data. |
| **Status** | Satisfied — Agent 01 schema is complete and stable. |

---

### D3.1 — Agent 03 depends on Agent 01 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 01: `001_core_schema.sql`, `004_geospatial.sql` |
| **Downstream** | Agent 03: `name_similarity.py`, `geo_proximity.py`, `composite_scorer.py` |
| **Nature** | Dedup reads `pharmacy_locations`, `contacts`, `external_identifiers`. Uses PostGIS functions and trigram indexes for blocking and scoring. |
| **Specific tables** | `pharmacy_locations` (name, state, geolocation), `contacts` (phone), `external_identifiers` (identifier_type, identifier_value) |
| **Risk** | Column renames or type changes break dedup queries. |
| **Status** | Satisfied — schema stable, dedup algorithms reference correct columns. |

---

### D3.2 — Agent 03 depends on Agent 02 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 02: `ingest_template.py` output (canonical records in staging) |
| **Downstream** | Agent 03: deduplication pipeline |
| **Nature** | Dedup engine requires ingested records in `raw_ingested_records` or newly inserted `pharmacy_locations` rows to operate on. No records = nothing to deduplicate. |
| **Risk** | Low-quality ingestion (bad normalization) degrades dedup accuracy. |
| **Status** | Satisfied — ingestion pipeline is complete and produces normalized output. |

---

### D4.1 — Agent 04 depends on Agent 01 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 01: `002_status_history.sql` (`record_validation_change()`), `001_core_schema.sql` |
| **Downstream** | Agent 04: verification workflows, evidence schema |
| **Nature** | Verification operations call `record_validation_change()` to promote records through L0->L1->L2. Evidence references stored in `validation_status_history.evidence_reference`. |
| **Specific functions** | `record_validation_change(p_pharmacy_id, p_new_level, p_changed_by, p_actor_type, p_evidence_reference, p_source_description, p_evidence_detail)` |
| **Risk** | Function signature changes break verification workflow integration. |
| **Status** | Satisfied — function signature and evidence schema are aligned. |

---

### D4.2 — Agent 04 depends on Agent 08 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 08: regulator sync pipeline, `status_source_governance.md` |
| **Downstream** | Agent 04: L2->L3 promotion workflow |
| **Nature** | L3 (Regulator Verified) can only be granted when regulator data is available via sync. Verification SOPs reference regulator cross-reference as the L2->L3 mechanism. |
| **Risk** | Without regulator data, no records can reach L3. This is by design — verification proceeds to L2 independently. |
| **Status** | Blocked/not delivered — Agent 08 artifacts are missing, so L3 promotion dependencies are documented but not yet implemented. |

---

### D5.1 — Agent 05 depends on Agent 01 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 01: all 4 SQL scripts, FHIR mapping specs |
| **Downstream** | Agent 05: `openapi.yaml`, API implementation |
| **Nature** | API endpoints query `pharmacy_locations`, `contacts`, `external_identifiers`, `validation_status_history`, `provenance_records`. FHIR endpoints use mapping specs. Geospatial endpoints invoke `find_nearest_pharmacies()`, `find_pharmacies_within_radius()`. |
| **Specific tables/functions** | All core tables + all geospatial functions + `current_validation_status` view |
| **Risk** | Schema changes require API spec and implementation updates simultaneously. |
| **Status** | Satisfied — OpenAPI spec references are aligned with schema. |

---

### D5.2 — Agent 05 depends on Agent 04 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 04: `evidence_schema.json`, verification workflow definitions |
| **Downstream** | Agent 05: `/pharmacies/{id}/validation-history` endpoint |
| **Nature** | API exposes validation history; evidence_detail JSONB structure must match evidence schema for consistent API responses. |
| **Risk** | Low — validation history is append-only and the API returns it as-is. |
| **Status** | Satisfied — evidence schema and API response structure are compatible. |

---

### D5.3 — Agent 05 depends on Agent 06 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 06: threat model, NDPA compliance checklist, risk register |
| **Downstream** | Agent 05: `auth.md`, `rate_limiting.md`, `audit_logging.md` |
| **Nature** | Middleware design directly implements controls identified in threat model (brute-force, scraping, impersonation) and NDPA requirements (retention, data minimization, contact redaction). |
| **Risk** | New risks identified post-implementation require middleware updates. |
| **Status** | Satisfied — middleware specs address all critical/high risks in the register. |

---

### D5.4 — Agent 05 depends on Agent 08 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 08: `status_source_governance.md` (source tier rules) |
| **Downstream** | Agent 05: API write endpoints (future registry_write tier) |
| **Nature** | Write operations must enforce source tier governance — e.g., API users (T4) cannot assert L3 status. Auth middleware must map API tiers to source tiers. |
| **Risk** | Write endpoint implementation must integrate governance rules. |
| **Status** | Blocked/not delivered — write endpoint governance cannot be finalized until Agent 08 `status_source_governance` artifact exists. |

---

### D6.1 — Agent 01 depends on Agent 06 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 06: NDPA compliance checklist (retention schedules, data minimization) |
| **Downstream** | Agent 01: schema design decisions |
| **Nature** | NDPA retention policy (raw staging 90 days, contact PII operational+24 months) should influence schema partitioning and archive strategy. |
| **Risk** | Low — schema accommodates retention via timestamp columns; partitioning is an operational concern. |
| **Status** | Satisfied — schema includes all necessary timestamp columns for retention enforcement. |

---

### D6.2 — Agent 02 depends on Agent 06 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 06: NDPA compliance (lawful basis for each processing activity) |
| **Downstream** | Agent 02: source registry, ingestion pipeline |
| **Nature** | Each data source must have documented lawful basis for processing. Source registry should reference NDPA compliance mapping. |
| **Risk** | Ingesting a source without lawful basis creates compliance exposure. |
| **Status** | Partially satisfied — NDPA checklist maps processing activities but source_registry.json does not yet reference specific lawful bases per source. |

---

### D6.3 — Agent 04 depends on Agent 06 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 06: risk register (FRD-001 fake pharmacy), misuse playbook |
| **Downstream** | Agent 04: verification SOPs, dispute workflow |
| **Nature** | Verification procedures are a primary mitigation for fake pharmacy risk (FRD-001, score 20). Dispute workflow handles removal requests. |
| **Risk** | Low — procedures are aligned. |
| **Status** | Satisfied — verification SOPs and dispute workflow address all fraud risks. |

---

### D6.4 — Agent 06 depends on Agent 01 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 01: schema design, data types collected |
| **Downstream** | Agent 06: NDPA compliance mapping, threat model asset inventory |
| **Nature** | Risk assessment and compliance mapping require understanding what data is stored and how. |
| **Status** | Satisfied — all 4 schema scripts were reviewed for the threat model and NDPA checklist. |

---

### D6.5 — Agent 08 depends on Agent 06 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 06: risk register (REG-001 PCN challenge, REG-002 NDPA non-compliance) |
| **Downstream** | Agent 08: partnership playbook, data sharing agreement terms |
| **Nature** | Partnership agreements must address regulatory risks and NDPA requirements for data sharing with PCN/NAFDAC/NHIA. |
| **Status** | Blocked/not delivered — Agent 08 partnership artifacts are missing in-repo; DUA template alignment cannot be verified. |

---

### D7.1–D7.7 — Agent 07 depends on all agents (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Nature** | Agent 07 (Integration) reads all deliverables to produce the architecture overview and dependency tracker. These are observation dependencies, not blocking. |
| **Status** | Satisfied — this document and `architecture_overview.md` are the result. |

---

### D8.1 — Agent 01 depends on Agent 08 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 08: PCN/NAFDAC/NHIA field requirements |
| **Downstream** | Agent 01: `external_identifiers` table, schema field accommodations |
| **Nature** | Schema must accommodate regulator-specific identifiers (pcn_premises_id, nafdac_facility_id, nhia_facility_id). The `external_identifiers` table with `identifier_type` + `identifier_value` handles this generically. |
| **Status** | Partially satisfied — schema supports arbitrary external identifier types, but Agent 08 field-level requirements are not yet delivered in-repo. |

---

### D8.2 — Agent 08 depends on Agent 01 (BLOCKING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 01: `001_core_schema.sql`, `002_status_history.sql`, `003_provenance_audit.sql` |
| **Downstream** | Agent 08: sync architecture, matching & reconciliation pipeline |
| **Nature** | Regulator sync pipeline writes to `external_identifiers`, calls `record_validation_change()` for L3 promotion, logs to `provenance_records` with sync_batch_id. |
| **Risk** | Schema changes break sync pipeline. |
| **Status** | Blocked/not delivered — Agent 08 sync architecture is not present in-repo, so this dependency cannot be validated. |

---

### D8.3 — Agent 08 depends on Agent 04 (INFORMING)

| Attribute | Detail |
|-----------|--------|
| **Upstream** | Agent 04: evidence schema, verification levels |
| **Downstream** | Agent 08: L3 promotion via regulator cross-reference |
| **Nature** | Regulator sync produces evidence of type `regulator_crossref` per `evidence_schema.json`. Evidence includes source, record_id, match_score, match_type, license_number, license_expiry. |
| **Status** | Blocked/not delivered — Agent 08 implementation artifact for L3 cross-reference flow is missing in-repo. |

---

## 4. Critical Path

The critical path determines the minimum execution order for the project to function.

```
Agent 01 (Data Architecture)
    |
    +---> Agent 06 (Policy & Risk) ----+
    |                                   |
    +---> Agent 08 (Regulatory) --------+
    |                                   |
    v                                   v
Agent 02 (Data Acquisition)       Agent 05 (Platform & API)
    |                                   ^
    v                                   |
Agent 03 (Deduplication)               |
    |                                   |
    v                                   |
Agent 04 (Verification) ---------------+
    |
    v
Agent 07 (Integration) -- ongoing, observes all
```

### Execution Order (per CLAUDE.md)

| Order | Agent | Rationale |
|-------|-------|-----------|
| 1 | Agent 01 — Data Architecture | Foundation schema — everything depends on this |
| 2 | Agent 06 — Policy & Risk | Informs design constraints before data collection |
| 3 | Agent 08 — Regulatory Integration | Bakes PCN/NAFDAC/NHIA fields into schema |
| 4 | Agent 02 — Data Acquisition | Ingestion begins once schema is stable |
| 5 | Agent 03 — Deduplication | Processes ingested records |
| 6 | Agent 04 — Verification Operations | Human workflows on deduplicated records |
| 7 | Agent 05 — Platform & API | Built on stable schema + verified data |
| 8 | Agent 07 — Integration | Ongoing, grows with project |

---

## 5. Deliverable-Level Dependency Map

The following table maps each deliverable to the specific upstream deliverables it depends on.

### Agent 01 — Data Architecture

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `001_core_schema.sql` | Agent 08: PCN/NAFDAC field requirements | Informing |
| `002_status_history.sql` | `001_core_schema.sql` (FK to pharmacy_locations) | Blocking |
| `003_provenance_audit.sql` | `001_core_schema.sql` (entity_id references) | Blocking |
| `004_geospatial.sql` | `001_core_schema.sql` (geolocation column) | Blocking |
| `location_mapping.json` | `001_core_schema.sql` (maps pharmacy_locations fields) | Blocking |
| `organization_mapping.json` | `001_core_schema.sql` (maps organization-related fields) | Blocking |
| `data_governance_spec.md` | Agent 06: NDPA compliance checklist | Informing |

### Agent 02 — Data Acquisition

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `source_registry.json` | None (standalone catalog) | — |
| `generic_pharmacy_import.json` | `001_core_schema.sql` (field names) | Informing |
| `osm_extract.json` | `generic_pharmacy_import.json` (extends pattern) | Informing |
| `pcn_format.json` | Agent 08: PCN field definitions | Informing |
| `ingest_template.py` | `001_core_schema.sql`, `003_provenance_audit.sql`, all templates | Blocking |
| `coverage_report.md` | `ingest_template.py` output (batch stats) | Informing |

### Agent 03 — Deduplication

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `name_similarity.py` | `001_core_schema.sql` (pharmacy_locations.name) | Blocking |
| `geo_proximity.py` | `004_geospatial.sql` (geolocation column, SRID 4326) | Blocking |
| `composite_scorer.py` | `name_similarity.py`, `geo_proximity.py`, `001_core_schema.sql` (contacts, external_identifiers) | Blocking |
| `merge_rules.yaml` | None (standalone config) | — |
| `dedup_methodology.md` | All Agent 03 algorithms + Agent 01 schema | Informing |

### Agent 04 — Verification Operations

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `evidence_schema.json` | `002_status_history.sql` (evidence_reference, evidence_detail) | Blocking |
| `verification_sop.md` | `evidence_schema.json`, Agent 06: risk register | Informing |
| `reverification_schedule.md` | `verification_sop.md`, `002_status_history.sql` | Informing |
| `dispute_workflow.md` | `evidence_schema.json`, Agent 06: misuse playbook | Informing |

### Agent 05 — Platform & API

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `openapi.yaml` | `001_core_schema.sql`, `004_geospatial.sql`, `location_mapping.json` | Blocking |
| `api_guide.md` | `openapi.yaml`, `auth.md`, `rate_limiting.md` | Informing |
| `auth.md` | Agent 06: threat model (S-2 impersonation, credential stuffing) | Blocking |
| `rate_limiting.md` | Agent 06: threat model (scraping, harvesting), NDPA compliance | Blocking |
| `audit_logging.md` | `003_provenance_audit.sql` (audit_log table schema) | Blocking |

### Agent 06 — Policy & Risk

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `risk_register.md` | `001_core_schema.sql` (understanding of data assets) | Informing |
| `ndpa_compliance_checklist.md` | `001_core_schema.sql` (processing activities), `data_governance_spec.md` | Informing |
| `threat_model.md` | All Agent 01 schema (asset inventory), Agent 05 spec (attack surface) | Informing |
| `misuse_mitigation_playbook.md` | `risk_register.md`, `threat_model.md` | Blocking |

### Agent 08 — Regulatory Integration

| Deliverable | Depends On | Dependency Type |
|------------|-----------|-----------------|
| `sync_architecture.md` | `001_core_schema.sql`, `002_status_history.sql`, `003_provenance_audit.sql` | Blocking |
| `partnership_playbook.md` | Agent 06: NDPA checklist, risk register (REG-001, REG-002) | Informing |
| `status_source_governance.md` | `002_status_history.sql` (validation_level enum), `evidence_schema.json` | Blocking |

---

## 6. Open Risks and Action Items

| ID | Risk | Severity | Affected Agents | Mitigation |
|----|------|----------|-----------------|------------|
| R1 | Schema migration after ingestion starts | High | 01, 02, 03 | Schema is marked stable; any changes require migration script + Agent 02 pipeline update |
| R2 | source_registry.json missing NDPA lawful basis per source | Medium | 02, 06 | Add `lawful_basis` field to each source entry in source_registry.json |
| R3 | Write endpoints not yet implementing source tier governance | Medium | 05, 08 | Agent 05 implementation must integrate Agent 08 governance rules when building registry_write tier |
| R4 | No automated re-verification trigger system | Medium | 04, 05 | Build scheduled job referencing reverification_schedule.md cadences |
| R5 | FHIR endpoints are read-only | Low | 05, 01 | FHIR write operations deferred to future phase; documented as known gap |
| R6 | Redis HA not specified for rate limiting | Low | 05 | Degraded mode (in-memory fallback) documented; production deployment should define Redis HA |
| R7 | Data subject rights workflow not implemented | Medium | 06, 05 | NDPA checklist identifies requirements; API endpoints for data subject requests are not yet designed |
| R8 | Agent 08 deliverables missing from repository | High | 01, 04, 05, 07, 08 | Mark Agent 08 dependencies as blocked/not-delivered until `sync_architecture.md`, `partnership_playbook.md`, and `status_source_governance.md` are added |

---

## 7. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-02-17 | Initial dependency tracker created from full deliverable review | Agent 07 |
| 2026-02-23 | Corrected Agent 08 inventory/status claims to reflect missing in-repo artifacts and blocked dependencies | Agent 07 |
