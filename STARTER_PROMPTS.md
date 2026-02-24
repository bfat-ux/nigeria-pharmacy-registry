# Claude Code — St

---

## 🏗️ Agent 1 — Data Architecture (START HERE)

### Prompt 1.1: Core Schema
```
Read CLAUDE.md. Then build Agent 1 deliverables:

1. Create agent-01-data-architecture/sql/001_core_schema.sql:
   - pharmacy_locations table (UUID PK, name, facility_type enum [pharmacy, ppmv, hospital_pharmacy], address fields, lga, state, geolocation as PostGIS POINT)
   - external_identifiers table (linking to PCN premises IDs, NHIA facility IDs, etc.)
   - contacts table (phone, email, contact_person)
   - All tables: created_at, updated_at, created_by, updated_by

2. Create agent-01-data-architecture/sql/002_status_history.sql:
   - validation_status_history table (append-only, pharmacy_id FK, old_level, new_level, changed_at, changed_by, evidence_reference, source_description)
   - Current validation level derived from latest entry

3. Create agent-01-data-architecture/sql/003_provenance_audit.sql:
   - provenance_records table (entity_type, entity_id, action, actor, source_system, source_dataset, timestamp, detail JSONB)
   - audit_log table for all API/system operations

4. Create agent-01-data-architecture/sql/004_geospatial.sql:
   - PostGIS extension setup
   - Spatial indexes
   - Helper function: find_pharmacies_within_radius(lat, lon, radius_km)

Follow the coding standards in CLAUDE.md exactly.
```

### Prompt 1.2: FHIR Mapping
```
Now build the FHIR mapping layer in agent-01-data-architecture/fhir/:

1. location_mapping.json — Map pharmacy_locations fields to FHIR R4 Location resource fields. Include the validation ladder level as a Location.status + custom extension.

2. organization_mapping.json — Map ownership/operator data to FHIR R4 Organization resource.

3. Create agent-01-data-architecture/docs/fhir_mapping_spec.md documenting the mapping decisions and any Nigeria-specific extensions needed.
```

### Prompt 1.3: Data Governance
```
Create agent-01-data-architecture/docs/data_governance_spec.md covering:
- Record lifecycle (creation → validation → archival)
- Status transition rules (which L→L transitions are valid, what evidence is required)
- Data retention policy
- Actor types (system, field_agent, partner_api, regulator_sync)
- Conflict resolution rules when multiple sources disagree
```

---

## 🛡️ Agent 6 — Policy, Risk & Compliance

### Prompt 6.1: Risk Register + NDPA
```
Read CLAUDE.md. Build Agent 6 deliverables:

1. agent-06-policy-risk/risk_register.md — Structured risk register with columns: Risk ID, Category, Description, Likelihood, Impact, Mitigation, Owner. Categories: privacy, regulatory, fraud, operational, reputational.

2. agent-06-policy-risk/ndpa_compliance_checklist.md — Map project operations against Nigeria Data Protection Act 2023 requirements. Focus on: lawful basis for processing, data minimization, storage limitation, data subject rights.

3. agent-06-policy-risk/threat_model.md — STRIDE-based threat model for the registry. Key threats: fake pharmacy registration, impersonation of regulators, harassment of pharmacy owners via exposed contact data, data scraping.

4. agent-06-policy-risk/misuse_mitigation_playbook.md — For each threat, define detection signals and response procedures.
```

---

## 🏛️ Agent 8 — Regulatory Integration

### Prompt 8.1: Regulatory Sync Architecture
```
Read CLAUDE.md. Build Agent 8 deliverables:

1. agent-08-regulatory-integration/sync_architecture.md — Design the sync pipeline model for PCN/NAFDAC datasets. Assume initial feeds are periodic CSV exports. Include: schema fields (pcn_premises_id, license_status, status_effective_date, status_source, last_sync_timestamp), batch-to-incremental migration path, conflict resolution when registry data disagrees with regulator data.

2. agent-08-regulatory-integration/status_source_governance.md — Rules for representing "community-verified" vs "regulator-verified" status. Define which source wins in conflicts.

3. agent-08-regulatory-integration/partnership_playbook.md — Template for data partnership agreements with PCN, NAFDAC, state pharmacy councils. Include: data fields requested, update frequency, attribution requirements, permitted uses.
```

---

## 📊 Agent 2 — Data Acquisition

### Prompt 2.1: Source Identification & Templates
```
Read CLAUDE.md. Build Agent 2 deliverables:

1. agent-02-data-acquisition/sources/source_registry.json — Catalog of potential data sources with fields: source_name, source_type (open_data, partner, regulator, crowdsource), url, license, coverage_scope, estimated_record_count, ingestion_status, notes. Include known sources: GRID3 Nigeria health facilities, OpenStreetMap pharmacy POIs, NHIA facility lists, any available PCN data.

2. agent-02-data-acquisition/templates/ — Create ingestion templates (JSON schemas) for: generic_pharmacy_import.json, pcn_format.json, osm_extract.json

3. agent-02-data-acquisition/scripts/ingest_template.py — Skeleton Python ingestion script that reads a source file, validates against a template, assigns L0 status, writes provenance record, and outputs canonical format.

4. agent-02-data-acquisition/docs/coverage_report.md — Template for tracking coverage by state and LGA.
```

---

## 🔗 Agent 3 — Deduplication

### Prompt 3.1: Entity Resolution
```
Read CLAUDE.md. Build Agent 3 deliverables:

1. agent-03-deduplication/algorithms/name_similarity.py — Fuzzy name matching using Levenshtein + token-sort ratio. Handle common Nigerian pharmacy naming patterns.

2. agent-03-deduplication/algorithms/geo_proximity.py — Geospatial proximity matching with configurable radius threshold.

3. agent-03-deduplication/algorithms/composite_scorer.py — Weighted composite confidence score combining name, geo, phone matching. Output: match_confidence 0.0–1.0.

4. agent-03-deduplication/config/merge_rules.yaml — Thresholds: auto_merge (>0.95), review_queue (0.70–0.95), no_match (<0.70). Override rules for edge cases.

5. agent-03-deduplication/docs/dedup_methodology.md — Document the approach, rationale for thresholds, and manual review workflow design.
```

---

## ✅ Agent 4 — Verification Operations

### Prompt 4.1: Verification SOPs
```
Read CLAUDE.md. Build Agent 4 deliverables:

1. agent-04-verification/docs/verification_sop.md — Standard operating procedures for L0→L1 (contact confirmation), L1→L2 (evidence documentation), L2→L3 (regulator cross-reference). Include scripts for phone verification calls.

2. agent-04-verification/schemas/evidence_schema.json — JSON schema for evidence records: evidence_type, capture_method, captured_by, captured_at, storage_reference, verification_notes.

3. agent-04-verification/docs/reverification_schedule.md — Re-verification cadence by level (L1: annual, L2: 18 months, L3: on regulator sync). Define triggers for out-of-cycle re-verification.

4. agent-04-verification/workflows/dispute_workflow.md — Process for pharmacies to dispute or correct their records.
```

---

## 🖥️ Agent 5 — Platform & API

### Prompt 5.1: API Specification
```
Read CLAUDE.md. Build Agent 5 deliverables:

1. agent-05-platform-api/api/openapi.yaml — Full OpenAPI 3.1 spec with endpoints:
   - GET /pharmacies/search (query by name, state, lga, type)
   - GET /pharmacies/{id} (lookup with validation history)
   - GET /pharmacies/nearest (lat/lon + radius)
   - GET /pharmacies/{id}/validation-history
   - GET /changes (change-feed endpoint with since parameter)
   - Optional: FHIR /Location endpoint

2. agent-05-platform-api/docs/api_guide.md — Developer guide with examples, rate limits, auth model.

3. agent-05-platform-api/middleware/ — Design specs for: rate_limiting.md (protect contact data), audit_logging.md (immutable logs), auth.md (API key + optional OAuth).
```

---

## 🔄 Agent 7 — Integration (Ongoing)

### Prompt 7.1: Architecture Overview
```
Read CLAUDE.md and review all existing deliverables across all agent directories. Then:

1. Create agent-07-integration/architecture_overview.md — Master architecture document showing how all components connect. Include: data flow diagram (text-based), component dependencies, integration points, technology stack summary.

2. Create agent-07-integration/dependency_tracker.md — Cross-agent dependency matrix showing which deliverables block which.
```
