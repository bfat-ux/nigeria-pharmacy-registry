# STRIDE Threat Model — Nigeria Pharmacy Registry

> **Version:** 1.0
> **Last Updated:** 2026-02-16
> **Owner:** Policy & Risk Workstream (Agent 06)
> **Methodology:** STRIDE (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege)
> **Review Cadence:** Semi-annually, or after significant architecture changes

## System Overview

The Nigeria Pharmacy Registry is a national dispensing-endpoint infrastructure layer
that ingests, deduplicates, verifies, and serves data about pharmacy and PPMV facilities
across Nigeria.

### Trust Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│                     EXTERNAL / UNTRUSTED                        │
│                                                                 │
│  [Data Sources]     [Public Users]     [Regulator Feeds]        │
│   CSV/Excel          Browser/App        PCN, NAFDAC, NHIA       │
│                                                                 │
└──────────┬──────────────┬─────────────────┬─────────────────────┘
           │              │                 │
     ══════╪══════════════╪═════════════════╪══════ TRUST BOUNDARY 1
           │              │                 │        (Ingress)
┌──────────▼──────────────▼─────────────────▼─────────────────────┐
│                   APPLICATION TIER                               │
│                                                                 │
│  [Ingestion API]   [Public API]   [Partner API]  [Admin Portal] │
│                                                                 │
└──────────┬──────────────┬─────────────────┬─────────────────────┘
           │              │                 │
     ══════╪══════════════╪═════════════════╪══════ TRUST BOUNDARY 2
           │              │                 │        (Data Layer)
┌──────────▼──────────────▼─────────────────▼─────────────────────┐
│                     DATA TIER                                    │
│                                                                 │
│  [PostgreSQL + PostGIS]    [Evidence Store]    [Audit Log]       │
│   Canonical Registry        Photos/Docs        Provenance        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Assets

| Asset | Sensitivity | Description |
|-------|------------|-------------|
| Canonical facility records | High | Core registry data — facility existence, location, licensing |
| Facility contact PII | High | Owner names, phone numbers, email addresses |
| Verification evidence | High | Storefront photos with GPS metadata, call recordings |
| Validation level status | High | L0–L4 status determines public trust in a facility |
| API keys and credentials | Critical | Access tokens for partners and regulators |
| Audit/provenance logs | High | Immutable record of all data changes |
| Source mapping configuration | Medium | How each data source maps to canonical schema |

---

## Threat Analysis

### S — Spoofing

#### S-1: Fake Pharmacy Registration

| Property | Detail |
|----------|--------|
| **Threat** | Malicious actor submits fictitious pharmacy records through ingestion channels to create fake entries that appear legitimate |
| **Attack Vector** | Submit fabricated CSV/Excel data through partner ingestion templates, or exploit open submission channels |
| **Target Asset** | Canonical facility records, validation level status |
| **Impact** | Fake pharmacies gain apparent legitimacy; supply chain fraud enabled; registry credibility undermined |
| **Likelihood** | High — low barrier to entry if ingestion channels accept unvalidated data |
| **Controls** | 1. All ingested records enter at L0 (Mapped) — no automatic trust. 2. Source attribution required for every record (source_id, ingestion_timestamp, actor). 3. Deduplication pipeline flags records with no corroborating source. 4. L2+ requires independent verification evidence. 5. Cross-reference with PCN license database at L3. |

#### S-2: Impersonation of Regulator

| Property | Detail |
|----------|--------|
| **Threat** | Attacker impersonates PCN, NAFDAC, or NHIA to inject false regulatory data or elevate facility validation status |
| **Attack Vector** | Forge regulator email/credentials to submit data via regulator integration channel; social-engineer admin portal access |
| **Target Asset** | Validation level status, canonical facility records |
| **Impact** | Illegitimate facilities reach L3 (Regulator Verified) status; catastrophic trust damage |
| **Likelihood** | Medium — requires knowledge of integration protocols but high reward for attacker |
| **Controls** | 1. Regulator feeds authenticated via institutional API keys or signed data packages. 2. No manual override to L3 without dual-approval workflow. 3. Regulator-sourced changes logged with institutional identity and verified through separate channel. 4. Admin portal uses MFA and role-based access. 5. Alert on any L3 status change for human review. |

#### S-3: Field Agent Identity Spoofing

| Property | Detail |
|----------|--------|
| **Threat** | Unauthorized individual impersonates a verification field agent to submit fraudulent verification evidence |
| **Attack Vector** | Obtain or fabricate field agent credentials; submit photos/evidence from unauthorized device |
| **Target Asset** | Verification evidence, validation level status |
| **Impact** | Facilities falsely elevated to L2; verification integrity compromised |
| **Likelihood** | Medium — depends on credential management maturity |
| **Controls** | 1. Field agents authenticate via registered device with MFA. 2. Evidence submissions include device fingerprint and GPS metadata. 3. Agent assignment is randomized — agents cannot choose which facilities to verify. 4. Spot-check 10% of L2 verifications independently. |

---

### T — Tampering

#### T-1: Modification of Facility Records

| Property | Detail |
|----------|--------|
| **Threat** | Authorized or compromised user modifies facility records to change status, contact information, or location data |
| **Attack Vector** | Exploit admin portal, direct database access, or compromised API key |
| **Target Asset** | Canonical facility records, facility contact PII, validation level status |
| **Impact** | False data in registry; legitimate facilities removed or downgraded; illegitimate facilities elevated |
| **Likelihood** | Medium |
| **Controls** | 1. Status changes are NEVER in-place updates — append to history table (immutable audit trail). 2. All changes require actor identity and timestamp. 3. Database user permissions restrict direct table modification to application service accounts. 4. Admin actions require MFA + are logged. 5. Anomaly detection on high-frequency status changes for a single facility. |

#### T-2: Tampering with Verification Evidence

| Property | Detail |
|----------|--------|
| **Threat** | Attacker modifies storefront photos (e.g., Photoshop) or GPS metadata to fabricate verification evidence |
| **Attack Vector** | Submit altered images with spoofed EXIF data; intercept evidence upload and replace content |
| **Target Asset** | Verification evidence |
| **Impact** | False evidence supports unwarranted status elevation |
| **Likelihood** | Medium |
| **Controls** | 1. Evidence files hashed (SHA-256) at capture time; hash stored separately in audit log. 2. GPS metadata cross-referenced against expected facility coordinates. 3. Upload channel uses TLS; integrity verified on receipt. 4. Evidence review workflow flags photos with stripped or inconsistent metadata. |

#### T-3: Poisoning Ingestion Data Sources

| Property | Detail |
|----------|--------|
| **Threat** | Attacker compromises a trusted data source (e.g., partner organization's system) to inject malicious data at scale |
| **Attack Vector** | Compromise partner system; supply chain attack on data feed |
| **Target Asset** | Raw ingestion data, canonical facility records |
| **Impact** | Large-scale data corruption; mass false registrations |
| **Likelihood** | Low — requires compromising external system |
| **Controls** | 1. Per-source ingestion quotas alert on abnormal record volumes. 2. Schema validation rejects structurally invalid records. 3. Ingestion is staged: raw data quarantined before canonical merge. 4. Source reputation tracking: sudden quality changes trigger manual review. 5. Rollback capability: batch ingestions can be reverted using provenance timestamps. |

---

### R — Repudiation

#### R-1: Denial of Data Modification

| Property | Detail |
|----------|--------|
| **Threat** | Internal user or partner denies having modified or submitted data to the registry |
| **Attack Vector** | Claim action was unauthorized; dispute audit trail accuracy |
| **Target Asset** | Audit/provenance logs |
| **Impact** | Cannot attribute data quality issues; accountability breakdown |
| **Likelihood** | Medium |
| **Controls** | 1. Every record change includes `created_by`/`updated_by` actor reference, timestamp, and source. 2. Audit log is append-only; no delete or update permissions granted to application accounts. 3. Audit log backed up to separate system with independent access controls. 4. API key usage logged with IP address and request payload hash. |

#### R-2: Verification Agent Denies Conducting Verification

| Property | Detail |
|----------|--------|
| **Threat** | Field agent denies performing (or failing to perform) a facility verification |
| **Attack Vector** | Agent claims they never visited facility; disputes evidence attribution |
| **Target Asset** | Verification evidence, validation level status |
| **Impact** | Cannot resolve verification disputes; process integrity questioned |
| **Likelihood** | Medium |
| **Controls** | 1. Verification assignments logged with agent ID, timestamp, facility ID. 2. Evidence submissions cryptographically linked to agent session. 3. GPS trail from mobile verification app corroborates site visit. 4. Re-verification by independent agent resolves disputes. |

---

### I — Information Disclosure

#### I-1: Exposure of Pharmacy Owner Contact Data

| Property | Detail |
|----------|--------|
| **Threat** | Unauthorized access to owner PII (phone, email, name) through API responses, data scraping, or breach |
| **Attack Vector** | Scrape public API if PII not properly restricted; exploit API vulnerability; insider access abuse; database breach |
| **Target Asset** | Facility contact PII |
| **Impact** | Harassment of pharmacy owners; extortion; spam campaigns; NDPA violation |
| **Likelihood** | High — contact data is valuable and frequently targeted |
| **Controls** | 1. Tiered API access: public tier excludes all PII; partner tier requires authenticated key + DUA. 2. Rate limiting on all endpoints (see OPS controls). 3. Field-level encryption for phone numbers and email addresses at rest. 4. API responses for contact data logged and monitored for anomalous patterns. 5. Database access restricted to service accounts; no direct human query access to PII tables without approval. |

#### I-2: Data Scraping of Facility Directory

| Property | Detail |
|----------|--------|
| **Threat** | Automated scraping of public API to build competing databases or target pharmacies for commercial/criminal purposes |
| **Attack Vector** | Enumerate paginated API endpoints; use multiple API keys to circumvent rate limits |
| **Target Asset** | Canonical facility records (aggregated) |
| **Impact** | Commercial misuse of public health data; loss of data governance control |
| **Likelihood** | High — publicly accessible APIs are routinely scraped |
| **Controls** | 1. API key required even for public tier. 2. Per-key rate limits and pagination caps (max 100 records/page). 3. Request pattern analysis: alert on sequential enumeration patterns. 4. Geospatial queries limited to bounding box size. 5. Terms of use prohibiting bulk extraction; legal enforcement mechanism. |

#### I-3: Leakage Through Logs or Error Messages

| Property | Detail |
|----------|--------|
| **Threat** | PII inadvertently included in application logs, error responses, or monitoring dashboards |
| **Attack Vector** | Log aggregation system accessed by unauthorized personnel; verbose error messages returned to clients |
| **Target Asset** | Facility contact PII, API keys |
| **Impact** | Indirect PII exposure; credential leakage |
| **Likelihood** | Medium — common vulnerability in early-stage systems |
| **Controls** | 1. Log sanitization middleware strips PII patterns (phone, email) before write. 2. API error responses use generic codes; no internal state or PII in error payloads. 3. Log access restricted to ops team with audit trail. 4. API keys never logged in full — mask to last 4 characters. |

---

### D — Denial of Service

#### D-1: API Abuse / Resource Exhaustion

| Property | Detail |
|----------|--------|
| **Threat** | Attacker floods API endpoints to degrade or prevent legitimate access |
| **Attack Vector** | Volumetric attack against public API; complex query abuse against geospatial endpoints |
| **Target Asset** | API availability |
| **Impact** | Field verification agents unable to access registry; partner integrations fail |
| **Likelihood** | Medium |
| **Controls** | 1. Per-key and per-IP rate limiting at middleware layer. 2. Geospatial query complexity limits (bounding box size, result cap). 3. CDN/reverse proxy with DDoS mitigation. 4. Circuit breaker pattern: shed load before database is overwhelmed. 5. Monitoring and alerting on request volume anomalies. |

#### D-2: Ingestion Pipeline Overload

| Property | Detail |
|----------|--------|
| **Threat** | Massive or malformed data source submission overwhelms ingestion pipeline, blocking legitimate data processing |
| **Attack Vector** | Submit extremely large CSV; submit records designed to trigger expensive deduplication computations |
| **Target Asset** | Ingestion pipeline availability, data freshness |
| **Impact** | Data ingestion backlog; stale registry data |
| **Likelihood** | Low–Medium |
| **Controls** | 1. File size limits on ingestion uploads. 2. Per-source ingestion quotas (max records/batch). 3. Async ingestion with queue; pipeline failures don't cascade. 4. Schema validation rejects malformed records early. 5. Monitoring on queue depth and processing latency. |

---

### E — Elevation of Privilege

#### E-1: API Key Privilege Escalation

| Property | Detail |
|----------|--------|
| **Threat** | Attacker escalates from public API key to partner tier, gaining access to PII |
| **Attack Vector** | Exploit API authentication vulnerability; steal partner API key; forge JWT claims |
| **Target Asset** | Facility contact PII, partner-tier API functions |
| **Impact** | Unauthorized PII access; bypass of data use agreements |
| **Likelihood** | Low–Medium |
| **Controls** | 1. API key tiers enforced at middleware with separate key spaces. 2. Partner keys bound to specific IP ranges or mTLS certificates. 3. JWT tokens signed with RS256; short expiry (1 hour). 4. Key rotation policy: partner keys rotated every 90 days. 5. Alert on key usage from unexpected IP ranges. |

#### E-2: Admin Portal Compromise

| Property | Detail |
|----------|--------|
| **Threat** | Attacker gains admin portal access, enabling mass data modification, status changes, or data export |
| **Attack Vector** | Credential stuffing, phishing, session hijacking of admin user |
| **Target Asset** | All registry data, validation level status, user management |
| **Impact** | Complete registry compromise; mass data tampering |
| **Likelihood** | Low–Medium |
| **Controls** | 1. MFA required for all admin accounts. 2. Admin sessions limited to 30-minute idle timeout. 3. Admin actions logged with IP, user agent, action detail. 4. Privileged actions (bulk status change, data export, user management) require secondary approval. 5. Admin accounts subject to quarterly access review. |

#### E-3: Insider Threat — Unauthorized Validation Level Override

| Property | Detail |
|----------|--------|
| **Threat** | Internal team member bypasses validation ladder to directly set a facility to L3 or higher without proper evidence |
| **Attack Vector** | Direct database manipulation; admin portal abuse; social engineering of approval workflow |
| **Target Asset** | Validation level status |
| **Impact** | Illegitimate facilities appear regulator-verified; foundational trust violation |
| **Likelihood** | Medium — insider threats are difficult to prevent entirely |
| **Controls** | 1. No single actor can elevate a record to L3 — dual-approval workflow enforced at application level. 2. Database triggers reject direct status updates outside application flow. 3. Daily automated audit: all L3+ records cross-checked against regulator feed. 4. Separation of duties: verification ops cannot also approve regulator cross-references. 5. Quarterly access review for all privileged roles. |

---

## Threat Summary Matrix

| ID | Category | Threat | Likelihood | Impact | Priority |
|----|----------|--------|------------|--------|----------|
| S-1 | Spoofing | Fake pharmacy registration | High | High | Critical |
| S-2 | Spoofing | Impersonation of regulator | Medium | Critical | Critical |
| S-3 | Spoofing | Field agent identity spoofing | Medium | High | High |
| T-1 | Tampering | Modification of facility records | Medium | High | High |
| T-2 | Tampering | Tampering with verification evidence | Medium | Medium | Medium |
| T-3 | Tampering | Poisoning ingestion data sources | Low | High | Medium |
| R-1 | Repudiation | Denial of data modification | Medium | Medium | Medium |
| R-2 | Repudiation | Verification agent denial | Medium | Medium | Medium |
| I-1 | Info Disclosure | Exposure of owner contact data | High | High | Critical |
| I-2 | Info Disclosure | Data scraping of facility directory | High | Medium | High |
| I-3 | Info Disclosure | Leakage through logs/errors | Medium | Medium | Medium |
| D-1 | Denial of Service | API abuse / resource exhaustion | Medium | Medium | Medium |
| D-2 | Denial of Service | Ingestion pipeline overload | Low–Medium | Medium | Low |
| E-1 | Elevation of Privilege | API key privilege escalation | Low–Medium | High | Medium |
| E-2 | Elevation of Privilege | Admin portal compromise | Low–Medium | Critical | High |
| E-3 | Elevation of Privilege | Insider validation override | Medium | High | High |

---

## Architecture Recommendations

Based on this threat model, the following controls should be embedded in system design:

1. **Append-only status history** — Validation level changes are never in-place updates (addresses T-1, R-1, E-3).
2. **Tiered API access** — Public, partner, and admin tiers with distinct authentication and field-level filtering (addresses I-1, I-2, E-1).
3. **Dual-approval workflows** — L3 status changes require two independent approvers (addresses S-2, E-3).
4. **Evidence integrity chain** — SHA-256 hashing of verification evidence at capture with separate hash storage (addresses T-2).
5. **Source attribution on every record** — `created_by`, `updated_by`, `source_id` on all tables (addresses R-1, S-1).
6. **Field-level PII encryption** — Phone and email encrypted at rest with separate key management (addresses I-1).
7. **Rate limiting and pattern detection** — Per-key, per-IP limits with anomaly alerting (addresses D-1, I-2).
8. **Randomized verification assignment** — Prevents collusion between agents and facility operators (addresses S-3, FRD-004).
