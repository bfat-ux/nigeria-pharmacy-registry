# Data Governance Specification

## Purpose

This document defines the rules governing data lifecycle, status transitions, retention, actor permissions, and conflict resolution within the Nigeria Pharmacy Registry (NPR). All system behavior must conform to these rules. Deviations require documented exceptions approved by the data governance owner.

---

## 1. Record Lifecycle

Every pharmacy record progresses through a defined lifecycle:

```
  Ingestion ──> Canonical ──> Validation ──> Active Use ──> Archival
    (raw)        (L0)        (L0→L3/L4)     (serving)     (retired)
```

### 1.1 Stages

| Stage        | Description                                                                 | Storage                     |
|--------------|-----------------------------------------------------------------------------|---------------------------  |
| **Ingestion**| Raw data lands in `raw_ingested_records` from a source feed                 | `raw_ingested_records`      |
| **Canonical**| After dedup/merge, record exists in `pharmacy_locations` at L0              | `pharmacy_locations`        |
| **Validation**| Record progresses up the validation ladder as evidence is gathered          | `validation_status_history` |
| **Active Use**| Record is served via API; consumers see current_validation_level            | `pharmacy_locations`        |
| **Archival** | Record is soft-archived (operational_status = permanently_closed + flag)    | `pharmacy_locations`        |

### 1.2 Lifecycle Rules

- **No hard deletes.** Records are never physically deleted from `pharmacy_locations`. Removal means setting `operational_status` to `permanently_closed` and logging the change.
- **Raw data is retained.** `raw_ingested_records` entries are never deleted. They are the forensic trail linking canonical records to their sources.
- **Every state change is logged.** Validation level changes go to `validation_status_history`. Operational status changes go to `operational_status_history`. All mutations generate `provenance_records` entries.

---

## 2. Validation Status Transition Rules

The validation ladder is the trust spine of the registry. Transitions must follow these rules:

### 2.1 Valid Transitions

| From | To  | Allowed? | Required Evidence                                           |
|------|-----|----------|-------------------------------------------------------------|
| L0   | L1  | Yes      | Logged outbound contact attempt + confirmed response        |
| L1   | L2  | Yes      | Location evidence (photo, GPS confirmation, field visit log) |
| L2   | L3  | Yes      | Match against official PCN/NAFDAC/partner dataset           |
| L3   | L4  | Yes      | In-person audit or biometric check-in (future)              |
| L0   | L2  | No       | Must pass through L1 first                                  |
| L0   | L3  | **Yes*** | *Only via regulator_sync actor when official dataset match is unambiguous |
| L1   | L3  | Yes      | Regulator cross-reference + existing L1 contact evidence    |
| Any  | L0  | Yes      | Downgrade: evidence of data quality concern                 |
| L3   | L1  | Yes      | Downgrade: regulator data expired or retracted              |

### 2.2 Transition Constraints

- **No skipping L1 to reach L2.** Contact confirmation is a prerequisite for evidence documentation (you need to reach someone to schedule a visit).
- **L0 → L3 exception.** When a regulator sync provides a high-confidence match (exact PCN premises ID match), the system may assign L3 directly. This must be logged with `actor_type = 'regulator_sync'` and the matching criteria in `evidence_detail`.
- **Downgrades are always permitted** but require a reason logged in `source_description`.
- **Re-verification can reset levels.** A failed re-verification attempt can downgrade a record (e.g., L2 → L0 if location no longer found).

### 2.3 Evidence Requirements by Level

| Level | Minimum Evidence Record                                                     |
|-------|-----------------------------------------------------------------------------|
| L1    | `evidence_type: 'contact_verification'`, `capture_method: 'outbound_call' or 'outbound_sms'`, contact response logged |
| L2    | `evidence_type: 'location_verification'`, `capture_method: 'field_visit' or 'gps_confirmation'`, optional photo reference |
| L3    | `evidence_type: 'regulator_crossref'`, `source_system: 'pcn' or 'nafdac' or 'nhia'`, matching record ID |
| L4    | `evidence_type: 'in_person_audit'`, auditor identity, audit checklist (future specification) |

---

## 3. Data Retention Policy

| Data Category              | Retention Period  | Rationale                                              |
|----------------------------|-------------------|--------------------------------------------------------|
| `pharmacy_locations`       | Indefinite        | Core registry data; archived but never deleted         |
| `raw_ingested_records`     | Indefinite        | Source provenance trail; required for audit             |
| `validation_status_history`| Indefinite        | Immutable audit trail                                  |
| `provenance_records`       | Indefinite        | Immutable audit trail                                  |
| `audit_log`                | 3 years minimum   | API access logs; may be rotated after retention period |
| `contacts`                 | Until superseded  | Contact data updated in place (old values in provenance)|
| Evidence artifacts (photos)| 5 years minimum   | Stored in object storage; referenced by evidence_schema |

### Retention Rules

- **No data is deleted to "clean up."** If a record is wrong, it is corrected with a new provenance entry — the old data remains in history.
- **NDPA compliance** may require demonstrating data minimization. Contact data for permanently closed pharmacies may be redacted after 2 years, but the provenance record of its existence remains.
- **Backup and disaster recovery:** Full database backups retained for 90 days. Point-in-time recovery capability for 30 days.

---

## 4. Actor Types

Every mutation in the registry is attributed to an actor. The system recognizes these actor types:

| Actor Type        | Description                                          | Example Actions                                |
|-------------------|------------------------------------------------------|------------------------------------------------|
| `system`          | Automated system processes                           | Ingestion pipelines, dedup batch jobs, scheduled tasks |
| `field_agent`     | Human operators conducting verification              | Phone calls, field visits, evidence upload     |
| `partner_api`     | External partner systems pushing data via API        | NGO facility list uploads, health program data |
| `regulator_sync`  | Official regulatory data feeds                       | PCN premises list sync, NAFDAC data import     |
| `admin`           | System administrators                                | Configuration changes, manual corrections      |
| `api_user`        | External API consumers (read-heavy)                  | Querying pharmacy data, downloading exports    |

### Actor Authentication

- `system`: Identified by service account name (e.g., `svc_ingest_grid3`)
- `field_agent`: Authenticated via application login; user ID logged
- `partner_api`: Authenticated via API key; key reference logged
- `regulator_sync`: Authenticated via dedicated sync credentials
- `admin`: Authenticated via admin portal; MFA required
- `api_user`: Authenticated via API key; rate-limited

---

## 5. Conflict Resolution Rules

When multiple data sources provide conflicting information about the same pharmacy, the registry must resolve conflicts deterministically.

### 5.1 Source Priority Hierarchy

When field values conflict, the following source priority applies (highest to lowest):

1. **Regulator data** (PCN, NAFDAC) — highest authority for licensing and official status
2. **Field verification** — direct observation by a verified field agent
3. **Partner data** (NHIA, NGOs) — institutional data with known provenance
4. **Crowdsourced/community data** — useful but lowest trust without corroboration
5. **Automated extracts** (OSM, GRID3) — useful for initial mapping but may be outdated

### 5.2 Field-Level Rules

| Field               | Conflict Rule                                                                    |
|----------------------|----------------------------------------------------------------------------------|
| `name`              | Prefer regulator name > field-verified name > partner name > source name         |
| `facility_type`     | Regulator classification wins. If absent, most recent verified source wins.      |
| `address fields`    | Field-verified address wins. If no field visit, most recent source wins.         |
| `geolocation`       | GPS from field visit > geocoded address > source-provided coordinates            |
| `operational_status`| Most recent verified observation wins. Regulator "license revoked" = permanently_closed. |
| `contacts`          | Most recently verified contact wins. Multiple contacts can coexist (primary flag). |
| `PCN premises ID`   | PCN is the sole authority. No override permitted.                                |

### 5.3 Conflict Logging

Every conflict resolution must be logged in `provenance_records` with:

- `action`: `'conflict_resolution'`
- `detail`: JSON object containing `{ "field": "...", "winning_value": "...", "losing_value": "...", "winning_source": "...", "losing_source": "...", "rule_applied": "..." }`

### 5.4 Unresolvable Conflicts

If automated rules cannot resolve a conflict (e.g., two regulator sources disagree), the record is flagged for manual review:

- A provenance record is created with `action: 'conflict_escalation'`
- The record's validation level is not upgraded until the conflict is resolved
- A review queue item is generated for admin attention

---

## 6. Data Quality Rules

### 6.1 Required Fields (canonical record)

| Field           | Required | Validation                                 |
|-----------------|----------|--------------------------------------------|
| `name`          | Yes      | Non-empty, max 500 characters              |
| `facility_type` | Yes      | Must be valid enum value                   |
| `state`         | Yes      | Must match Nigeria state list (36 + FCT)   |
| `lga`           | Yes      | Must match LGA list for the given state    |
| `created_by`    | Yes      | Non-empty actor reference                  |

### 6.2 Soft Validations (warnings, not blocks)

- `geolocation` should be within Nigeria's bounding box (lat: 4.0-14.0, lon: 2.5-15.0)
- `address_line_1` should be non-empty for records above L0
- At least one contact should exist for records at L1 or above
- `postal_code`, if present, should match Nigeria postal code format

---

## 7. Access Control Principles

| Data Category      | Public API | Authenticated API | Admin | Notes                                    |
|--------------------|------------|-------------------|-------|------------------------------------------|
| Pharmacy name      | Yes        | Yes               | Yes   |                                          |
| Address            | Yes        | Yes               | Yes   |                                          |
| Geolocation        | Yes        | Yes               | Yes   |                                          |
| Facility type      | Yes        | Yes               | Yes   |                                          |
| Validation level   | Yes        | Yes               | Yes   |                                          |
| Contact phone      | No         | Rate-limited      | Yes   | Sensitive: risk of harassment            |
| Contact email      | No         | Rate-limited      | Yes   | Sensitive                                |
| Contact person     | No         | No                | Yes   | PII: only exposed to admins              |
| Provenance records | No         | Summary only      | Full  | Detailed audit trail is admin-only       |
| Raw ingested data  | No         | No                | Yes   | Internal forensic data                   |

---

## 8. Change Management

Changes to this governance specification require:

1. Written proposal with rationale
2. Review by data governance owner and technical lead
3. Impact assessment on existing data and downstream consumers
4. Version bump and changelog entry in this document

**Document Version:** 1.0
**Last Updated:** 2025-01-01
**Owner:** NPR Data Governance Team
