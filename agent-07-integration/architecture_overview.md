# Nigeria Pharmacy Registry — Master Architecture Overview

> **Version:** 1.0
> **Last updated:** 2026-02-17
> **Owner:** Agent 07 — Integration

---

## 1. System Purpose

The Nigeria Pharmacy Registry (NPR) is a national dispensing-endpoint infrastructure
layer that tracks pharmacy and PPMV locations across Nigeria. It ingests data from
multiple sources, deduplicates records into a canonical registry, progressively
validates them through a trust ladder (L0–L3), and exposes the registry through a
tiered API with FHIR R4 interoperability.

**Core invariants:**

- No patient data — locations only.
- Provenance on every mutation — source, timestamp, actor.
- No unearned trust — validation ladder is sacred.
- Regulator-sync ready — PCN, NAFDAC, NHIA fields built in.

---

## 2. Component Map

```
+----------------------------------------------------------------------+
|                        NIGERIA PHARMACY REGISTRY                      |
+----------------------------------------------------------------------+
|                                                                      |
|  +-----------------------+    +---------------------------+          |
|  | EXTERNAL DATA SOURCES |    | REGULATORY PARTNERS       |          |
|  |-----------------------|    |---------------------------|          |
|  | GRID3 (open)          |    | PCN  (premises register)  |          |
|  | OSM   (open)          |    | NAFDAC (facility register)|          |
|  | State Gov datasets    |    | NHIA  (accreditation)     |          |
|  | Fintech partners      |    | State Pharmacy Councils   |          |
|  | Crowdsource / API     |    +---------------------------+          |
|  +-----------------------+           |                               |
|           |                          |                               |
|           v                          v                               |
|  +------------------------------------------------------------+     |
|  |              INGESTION LAYER  (Agent 02)                    |     |
|  |------------------------------------------------------------|     |
|  | ingest_template.py — ETL pipeline                           |     |
|  | Source templates: generic, OSM, PCN                         |     |
|  | source_registry.json — 8 registered sources                 |     |
|  | Output: raw_ingested_records + provenance + batch stats      |     |
|  +------------------------------------------------------------+     |
|           |                                                          |
|           v                                                          |
|  +------------------------------------------------------------+     |
|  |              STAGING & RAW STORAGE                          |     |
|  |------------------------------------------------------------|     |
|  | Table: raw_ingested_records (jsonb raw_data)                |     |
|  | Fields: source_name, source_dataset, source_record_id,      |     |
|  |         ingestion_batch_id, processing_status               |     |
|  +------------------------------------------------------------+     |
|           |                                                          |
|           v                                                          |
|  +------------------------------------------------------------+     |
|  |              DEDUPLICATION ENGINE  (Agent 03)               |     |
|  |------------------------------------------------------------|     |
|  | Pipeline: blocking -> candidate gen -> scoring -> decision  |     |
|  | Signals: name (0.40), geo (0.25), phone (0.20), ID (0.15)  |     |
|  | Thresholds: >=0.95 auto-merge, 0.70-0.95 review, <0.70 no  |     |
|  | Config: merge_rules.yaml (tunable without code change)      |     |
|  +------------------------------------------------------------+     |
|           |                                                          |
|           v                                                          |
|  +------------------------------------------------------------+     |
|  |              CANONICAL REGISTRY  (Agent 01)                 |     |
|  |------------------------------------------------------------|     |
|  | Table: pharmacy_locations (uuid PK, PostGIS geolocation)    |     |
|  | Table: external_identifiers (PCN, NAFDAC, NHIA IDs)         |     |
|  | Table: contacts (phone, email, WhatsApp)                    |     |
|  | Table: validation_status_history (append-only)               |     |
|  | Table: operational_status_history (append-only)              |     |
|  | Table: provenance_records (full lineage)                     |     |
|  | Table: audit_log (API request trail)                         |     |
|  | View:  current_validation_status                             |     |
|  +------------------------------------------------------------+     |
|           |                          ^                               |
|           v                          |                               |
|  +---------------------------+  +-----------------------------+      |
|  | VERIFICATION OPS (Agt 04) |  | REGULATOR SYNC (Agt 08)    |      |
|  |---------------------------|  |-----------------------------|      |
|  | L0->L1: contact confirm   |  | Batch CSV/Excel import     |      |
|  | L1->L2: field evidence    |  | Staging -> match -> promote |      |
|  | L2->L3: regulator xref    |  | PCN/NAFDAC/NHIA sync       |      |
|  | Dispute workflow           |  | Source tier governance      |      |
|  | Re-verification schedule   |  | Conflict resolution rules  |      |
|  | Evidence schema (JSON)     |  | L3 promotion authority     |      |
|  +---------------------------+  +-----------------------------+      |
|           |                          |                               |
|           v                          v                               |
|  +------------------------------------------------------------+     |
|  |              PLATFORM & API LAYER  (Agent 05)               |     |
|  |------------------------------------------------------------|     |
|  | OpenAPI 3.1 spec at api.npr.ng/v1                           |     |
|  | Endpoints: /pharmacies/search, /pharmacies/{id},            |     |
|  |   /pharmacies/nearest, /pharmacies/{id}/validation-history, |     |
|  |   /changes, /fhir/Location, /fhir/Location/{id}, /health   |     |
|  | Auth: API Key (X-API-Key) + OAuth 2.0 Client Credentials    |     |
|  | Tiers: public (60/min), registry_read (300/min),            |     |
|  |        registry_write (300/min), admin (600/min)            |     |
|  | Contact redaction at public tier                             |     |
|  +------------------------------------------------------------+     |
|           |                                                          |
|           v                                                          |
|  +------------------------------------------------------------+     |
|  |              MIDDLEWARE STACK  (Agent 05)                    |     |
|  |------------------------------------------------------------|     |
|  | auth.md — API key + OAuth middleware, tier resolution        |     |
|  | rate_limiting.md — Redis token bucket, contact sub-limits    |     |
|  | audit_logging.md — async buffered writes, correlation IDs    |     |
|  +------------------------------------------------------------+     |
|           |                                                          |
|           v                                                          |
|  +------------------------------------------------------------+     |
|  |              POLICY & RISK GOVERNANCE  (Agent 06)           |     |
|  |------------------------------------------------------------|     |
|  | Risk register: 30+ risks, 5 categories, scored              |     |
|  | NDPA compliance checklist (Nigeria Data Protection Act)      |     |
|  | STRIDE threat model                                          |     |
|  | Misuse mitigation playbook (SEV-1 through SEV-4)            |     |
|  +------------------------------------------------------------+     |
|                                                                      |
+----------------------------------------------------------------------+
```

---

## 3. Data Flow Diagram

The following diagram traces a record from ingestion to API consumption.

```
                    EXTERNAL SOURCE
                         |
                         | (CSV / JSON / API)
                         v
              +---------------------+
              |  Source Validation   |
              |  (template schema)  |
              +---------------------+
                         |
                   pass  |  fail --> rejected_records.json
                         v
              +---------------------+
              |  Normalization      |
              |  (to generic form)  |
              +---------------------+
                         |
                         v
              +---------------------+
              |  raw_ingested_      |
              |  records (staging)  |  <-- processing_status: pending
              +---------------------+
                         |
                         v
              +---------------------+
              |  Deduplication      |
              |  Engine             |
              |  (blocking, score,  |
              |   decision)         |
              +---------------------+
                    /          \
          new record            match found
                /                    \
               v                      v
    +------------------+    +------------------+
    | INSERT into      |    | MERGE into       |
    | pharmacy_        |    | existing record  |
    | locations        |    | (field-level     |
    | (L0_mapped)      |    |  precedence)     |
    +------------------+    +------------------+
              \                    /
               \                  /
                v                v
              +---------------------+
              |  provenance_records |  <-- log every change
              +---------------------+
                         |
                         v
              +---------------------+
              |  Verification       |
              |  Operations         |
              |  (L0 -> L1 -> L2)  |
              +---------------------+
                         |
                         |  evidence captured
                         v
              +---------------------+
              |  validation_status_ |
              |  history            |  <-- append-only
              +---------------------+
                         |
                         v
              +---------------------+
              |  Regulator Sync     |
              |  (PCN/NAFDAC/NHIA)  |
              |  (L2 -> L3)         |
              +---------------------+
                         |
                         v
              +---------------------+
              |  API Layer          |
              |  /pharmacies/*      |
              |  /fhir/Location     |
              +---------------------+
                         |
                         v
              +---------------------+
              |  Consumers          |
              |  (public, partners, |
              |   regulators)       |
              +---------------------+
```

---

## 4. Database Schema Map

### 4.1 Tables and Relationships

```
pharmacy_locations (PK: id uuid)
    |
    |-- 1:N --> external_identifiers (FK: pharmacy_id)
    |-- 1:N --> contacts (FK: pharmacy_id)
    |-- 1:N --> validation_status_history (FK: pharmacy_id)
    |-- 1:N --> operational_status_history (FK: pharmacy_id)
    |-- 1:N --> provenance_records (entity_id, entity_type='pharmacy_location')
    |-- 1:N --> raw_ingested_records (FK: canonical_pharmacy_id)

audit_log (standalone, linked by resource_id)

current_validation_status (VIEW on validation_status_history)
```

### 4.2 Enum Types

| Enum | Values |
|------|--------|
| `facility_type` | `pharmacy`, `ppmv`, `hospital_pharmacy` |
| `operational_status` | `operational`, `temporarily_closed`, `permanently_closed`, `unknown` |
| `validation_level` | `L0_mapped`, `L1_contact_confirmed`, `L2_evidence_documented`, `L3_regulator_verified`, `L4_high_assurance` |

### 4.3 Key Database Functions

| Function | Purpose |
|----------|---------|
| `record_validation_change()` | Append to validation_status_history + update convenience column |
| `log_provenance()` | Insert provenance record for any entity mutation |
| `log_audit()` | Insert API audit log entry |
| `find_pharmacies_within_radius()` | PostGIS radius search (ST_DWithin) |
| `find_nearest_pharmacies()` | PostGIS KNN nearest-neighbor (<->) |
| `pharmacy_bbox()` | Bounding box for state/LGA |

---

## 5. Integration Points

### 5.1 Ingestion to Canonical (Agent 02 -> Agent 01)

| Aspect | Detail |
|--------|--------|
| **Interface** | `ingest_template.py` writes to `raw_ingested_records` |
| **Contract** | Records must pass template schema validation (generic, OSM, or PCN) |
| **Output** | Canonical records, provenance entries, validation status entries, rejected records |
| **Provenance** | Every insert logs source_system, source_dataset, source_record_id, actor |
| **Initial state** | All ingested records start at `L0_mapped` |

### 5.2 Canonical to Deduplication (Agent 01 <-> Agent 03)

| Aspect | Detail |
|--------|--------|
| **Interface** | Dedup engine reads `pharmacy_locations` + `external_identifiers` + `contacts` |
| **Blocking** | State-level partition using `state` column |
| **Scoring** | 4-signal composite: name (trigram), geo (PostGIS), phone, external ID |
| **Output** | Merge decisions applied to `pharmacy_locations`; provenance logged |
| **Config** | `merge_rules.yaml` — thresholds, weights, overrides |
| **Review queue** | Scores 0.70–0.95 routed to human dedup_reviewer |

### 5.3 Canonical to Verification (Agent 01 <-> Agent 04)

| Aspect | Detail |
|--------|--------|
| **Interface** | Verification agents query `pharmacy_locations` + `current_validation_status` |
| **L0->L1** | Contact confirmation via phone/email; evidence stored per `evidence_schema.json` |
| **L1->L2** | Field visit with GPS, photos, observation; evidence stored |
| **L2->L3** | Regulator cross-reference (via Agent 08 sync) |
| **Status change** | Calls `record_validation_change()` — appends to history, never updates in place |
| **Disputes** | Handled via `dispute_workflow.md` with 7 dispute types, 3 escalation levels |

### 5.4 Regulator Sync to Canonical (Agent 08 -> Agent 01)

| Aspect | Detail |
|--------|--------|
| **Interface** | Batch CSV/Excel upload through admin portal |
| **Pipeline** | Receive -> staging table -> matching & reconciliation -> apply & promote -> provenance |
| **Matching** | PCN premises_id, NAFDAC facility_id, NHIA facility_id against `external_identifiers` |
| **Conflict resolution** | Source tier hierarchy: T1 Regulator > T2 Field > T3 Partner > T4 Community > T5 System |
| **L3 authority** | Only T1 (regulator_sync) sources can promote to L3_regulator_verified |
| **Tracking** | sync_batch_id, last_sync_timestamp on matched records |

### 5.5 Canonical to API (Agent 01 -> Agent 05)

| Aspect | Detail |
|--------|--------|
| **Interface** | TypeScript API reads PostgreSQL via connection pool |
| **Endpoints** | 8 endpoints defined in `openapi.yaml` |
| **Auth** | API Key (`npr_{env}_{32 chars}`) or OAuth 2.0 Client Credentials (RS256 JWT) |
| **Tiers** | public (60/min, redacted contacts), registry_read (300/min, full contacts, 100 contact/min sub-limit), registry_write (300/min), admin (600/min) |
| **FHIR** | `/fhir/Location` and `/fhir/Location/{id}` return FHIR R4 resources per mapping specs |
| **Audit** | Every request logged via `audit_log` table; critical events written synchronously |

### 5.6 Policy to All Components (Agent 06 -> All)

| Aspect | Detail |
|--------|--------|
| **Risk register** | 30+ risks across privacy, regulatory, fraud, operational, reputational categories |
| **NDPA compliance** | Lawful basis mapped for 8 processing activities; retention schedules defined |
| **Threat model** | STRIDE analysis covering spoofing, tampering, fraud, information disclosure |
| **Misuse playbook** | SEV-1 through SEV-4 response procedures for fake pharmacies, impersonation, data scraping |
| **Cross-cutting** | Informs API rate limits, contact redaction, audit retention, evidence requirements |

---

## 6. Technology Stack Summary

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Database** | PostgreSQL 15+ | Relational data store, canonical registry |
| **Geospatial** | PostGIS | Geography columns (SRID 4326), spatial indexes, radius/KNN queries |
| **Text search** | pg_trgm | Trigram-based fuzzy name matching |
| **Data pipelines** | Python 3.11+ | ETL ingestion, entity resolution, deduplication scoring |
| **API server** | TypeScript / Node.js | REST API, FHIR endpoints |
| **API spec** | OpenAPI 3.1 | Contract-first API design |
| **FHIR** | FHIR R4 | Location and Organization resource mapping |
| **Rate limiting** | Redis | Token bucket / sliding window rate limiter |
| **Auth** | API Key + OAuth 2.0 | Tiered access control (public/read/write/admin) |
| **Config** | YAML | Business rules, thresholds, merge config |
| **IDs** | UUID v4 | All internal primary keys |
| **Coordinates** | WGS84 (SRID 4326) | All geospatial columns |
| **Documentation** | Markdown | All specs, SOPs, playbooks |

---

## 7. Security Architecture

### 7.1 Trust Boundaries

```
+-------------------------------------------------------------+
|  BOUNDARY 1: INGRESS                                         |
|  +--------------------------------------------------------+  |
|  | Public internet -> API Gateway                          |  |
|  | Controls: TLS, API key/OAuth, rate limiting, audit      |  |
|  +--------------------------------------------------------+  |
|                                                               |
|  BOUNDARY 2: DATA LAYER                                      |
|  +--------------------------------------------------------+  |
|  | API server -> PostgreSQL                                |  |
|  | Controls: connection pool, role-based DB permissions,   |  |
|  |   audit_log INSERT/SELECT only, provenance immutability |  |
|  +--------------------------------------------------------+  |
+-------------------------------------------------------------+
```

### 7.2 Key Security Controls

| Control | Implementation |
|---------|---------------|
| Contact data redaction | Public tier sees masked phone (`+234****1234`) and email (`u***@example.com`) |
| Contact harvesting prevention | 100 contact-bearing responses/min sub-limit for registry_read |
| API key storage | bcrypt hash (work factor 12), 16-char prefix for lookup |
| Audit immutability | INSERT/SELECT only on audit_log; DEFAULT now() timestamps |
| Provenance integrity | Every mutation logged with actor, source, timestamp |
| Brute-force protection | Rate limiting + credential stuffing detection |
| Abuse detection | Sequential ID enumeration, geo-sweeping, contact harvesting patterns |

### 7.3 NDPA Compliance Controls

- Lawful basis documented for all 8 processing activities.
- Retention: raw staging 90 days, canonical indefinite, contact PII operational+24 months, photos 12 months, API logs 12 months.
- Data subject rights workflow defined but implementation pending.
- No patient data collected — location registry only.

---

## 8. FHIR Interoperability Layer

### 8.1 Resource Mappings

| Internal Entity | FHIR Resource | Mapping Spec |
|----------------|---------------|--------------|
| `pharmacy_locations` | FHIR Location | `location_mapping.json` |
| Organization data (future) | FHIR Organization | `organization_mapping.json` |

### 8.2 Nigeria-Specific Extensions

| Extension | URL Pattern | Purpose |
|-----------|------------|---------|
| Validation Level | `npr.ng/fhir/ext/validation-level` | L0–L3 trust level |
| Address Ward | `npr.ng/fhir/ext/address-ward` | Sub-LGA administrative unit |
| Primary Source | `npr.ng/fhir/ext/primary-source` | Source attribution |

### 8.3 Custom Code System

| Code | Display | Definition |
|------|---------|-----------|
| `PHARM` | Community Pharmacy | Licensed retail pharmacy |
| `PPMV` | Patent Medicine Vendor | Patent and Proprietary Medicine Vendor |
| `HOSPHARM` | Hospital Pharmacy | Hospital-based dispensing unit |

---

## 9. Operational Architecture

### 9.1 Verification Operations Flow

```
L0 (Mapped)
  |-- Contact confirmation (phone/email script)
  |-- 3 failed attempts -> Data Steward review
  v
L1 (Contact Confirmed)
  |-- Field visit: GPS, photos, observation
  |-- Evidence stored per evidence_schema.json
  v
L2 (Evidence Documented)
  |-- Regulator dataset cross-reference
  |-- Only T1 (regulator_sync) can promote
  v
L3 (Regulator Verified)
  |-- Re-verification: L1 every 12mo, L2 every 18mo, L3 quarterly
```

### 9.2 Dispute Resolution

- 7 dispute types: factual_correction, status_dispute, duplicate_report, closure_report, ownership_dispute, removal_request, verification_challenge.
- 5 intake channels: web portal, phone hotline, email, field visit, partner referral.
- 3-level escalation: initial review -> senior review -> external referral.
- SLAs defined per dispute type.

### 9.3 Re-Verification Schedule

| Level | Cadence | Trigger |
|-------|---------|---------|
| L1 | Every 12 months | Scheduled + complaint/alert triggers |
| L2 | Every 18 months | Scheduled + GPS anomaly/ownership change |
| L3 | Quarterly (on regulator sync) | Regulator dataset refresh |

---

## 10. Deployment Environments

| Environment | Base URL | Purpose |
|-------------|----------|---------|
| Production | `https://api.npr.ng/v1` | Live registry |
| Staging | `https://staging-api.npr.ng/v1` | Pre-production testing |
| Local | `http://localhost:3000/v1` | Developer workstations |

---

## 11. Cross-Cutting Concerns

### 11.1 Audit Trail

Every system action produces entries in one or more of:

- `provenance_records` — data lineage (who changed what, from which source).
- `audit_log` — API request trail (path, method, status, duration, IP).
- `validation_status_history` — validation level changes (append-only).
- `operational_status_history` — operational status changes (append-only).

### 11.2 Correlation

- Every API request receives `X-Request-Id` (UUID v4).
- Request ID propagated to all downstream service calls and audit entries.

### 11.3 Monitoring Metrics

| Metric | Source |
|--------|--------|
| `npr_requests_total` | Rate limiting middleware |
| `npr_rate_limit_rejections_total` | Rate limiting middleware |
| `npr_contact_limit_exceeded_total` | Contact sub-limit enforcement |
| `npr_abuse_events_total` | Abuse detection |
| `npr_audit_writes_total` | Audit logging |
| `npr_audit_write_errors_total` | Audit logging |
| `npr_audit_buffer_size` | Audit buffer health |
| `npr_contact_access_total` | Contact data access tracking |

---

## 12. Known Gaps and Future Work

| Gap | Status | Notes |
|-----|--------|-------|
| L4 (High-Assurance) implementation | Future | Biometric check-in / in-person audit |
| Dedicated organizations table | Future | Currently embedded in pharmacy_locations |
| Real-time regulator API feeds | Phase 3 | Currently batch CSV/Excel (Phase 1) |
| Data subject rights implementation | Pending | NDPA requirements documented, workflow TBD |
| FHIR write endpoints | Future | Currently read-only |
| Multi-state rollout playbook | Pending | Start with one state, expand incrementally |
| Redis HA for rate limiting | TBD | Degraded mode falls back to in-memory |
| Automated re-verification triggers | Pending | Schedule defined, automation not built |
