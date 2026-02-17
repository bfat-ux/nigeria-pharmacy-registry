# NDPA 2023 Compliance Checklist — Nigeria Pharmacy Registry

> **Version:** 1.0
> **Last Updated:** 2026-02-16
> **Owner:** Policy & Risk Workstream (Agent 06)
> **Applicable Law:** Nigeria Data Protection Act 2023 (NDPA), NDPC Implementation Framework
> **Review Cadence:** Semi-annually, or upon any regulatory guidance update

## Scope

This checklist maps the Nigeria Pharmacy Registry's data processing operations against
the requirements of the Nigeria Data Protection Act 2023. The registry processes data
about **pharmacy facilities and their operators** — not patients or transactions.

Personal data in scope includes: pharmacy owner names, phone numbers, email addresses,
and storefront photographs linked to identifiable individuals.

---

## 1. Lawful Basis for Processing

| # | Requirement | Status | Implementation Notes |
|---|-------------|--------|---------------------|
| 1.1 | Identify and document the lawful basis for each processing activity (NDPA Part III, s.25) | Pending | See section 1.A below for processing activity mapping |
| 1.2 | If relying on consent, ensure consent is freely given, specific, informed, and unambiguous (s.26) | Pending | Required for: verification evidence collection (photos, call recordings). Consent form to be developed by Verification Ops. |
| 1.3 | If relying on legitimate interest, conduct and document a Legitimate Interest Assessment (LIA) (s.25(1)(f)) | Pending | Primary basis for core registry operations. LIA must balance registry public health purpose against data subject privacy impact. |
| 1.4 | If relying on public interest, document the specific public interest ground (s.25(1)(e)) | Pending | Applicable to: publishing aggregated facility counts for public health planning. Document link to National Drug Distribution Guidelines. |
| 1.5 | Maintain a record of lawful basis determination for each processing activity | Not Started | Implement as a controlled document linked to the data processing register. |
| 1.6 | Ensure lawful basis is established before processing begins | Not Started | Gate in ingestion pipeline: no new data source activated without approved lawful basis entry. |

### 1.A Processing Activity — Lawful Basis Mapping

| Processing Activity | Data Elements | Proposed Lawful Basis | Justification |
|---------------------|---------------|----------------------|---------------|
| Ingest facility records from public/partner sources | Facility name, address, license number, owner name, phone, email | Legitimate Interest | Public health infrastructure mapping; facility operators have reduced privacy expectation for business contact details |
| Geocode facility addresses | Address, coordinates | Legitimate Interest | Necessary for spatial analysis and deduplication; no additional personal data collected |
| Deduplication and entity resolution | All facility fields including owner name, phone | Legitimate Interest | Data quality is essential to registry purpose; processing is proportionate |
| Contact verification (outbound calls/messages) | Phone, email | Consent | Direct outreach requires opt-in. Consent captured at point of contact with clear purpose statement. |
| Photo evidence collection (storefront) | Photograph, GPS coordinates, timestamp | Consent | Photographs may capture identifiable individuals. Explicit consent from facility operator required. |
| Regulator data cross-referencing | License number, facility name, address | Legitimate Interest / Public Interest | Regulatory alignment is core registry purpose and serves public safety |
| API responses to authenticated partners | Facility-level data; owner contact data for authorized tiers | Legitimate Interest + Data Use Agreement | Partner access governed by DUA specifying purpose limitation |
| Public API responses | Facility name, address, ward-level location, validation level | Public Interest | Aggregate/non-sensitive facility data supports public health transparency |

---

## 2. Data Minimization

| # | Requirement | Status | Implementation Notes |
|---|-------------|--------|---------------------|
| 2.1 | Process only personal data that is adequate, relevant, and limited to what is necessary (s.28) | Pending | Schema review: confirm every personal data field has a documented necessity justification. Remove any field that cannot be justified. |
| 2.2 | Do not collect personal data "just in case" it might be useful | In Progress | Schema design (Agent 01) limits fields to those in CLAUDE.md entity model. No open-ended "notes" fields containing PII. |
| 2.3 | Separate facility data from operator personal data at the schema level | Pending | Design pattern: `facilities` table holds facility-level data; `facility_contacts` table holds owner PII with access controls. |
| 2.4 | Implement field-level access controls so PII is only returned when necessary | Pending | API design: default responses exclude contact PII. Authenticated partner tier includes PII per DUA scope. |
| 2.5 | Minimize data in logs and audit trails — no PII in application logs | Pending | Audit trail references record UUIDs, not names or phone numbers. Application logs sanitized before write. |
| 2.6 | Pseudonymize personal data where full identification is not required | Pending | Public-facing analytics and coverage reports use aggregated/anonymized counts only. |

---

## 3. Storage Limitation

| # | Requirement | Status | Implementation Notes |
|---|-------------|--------|---------------------|
| 3.1 | Define and document retention periods for each category of personal data (s.29) | Pending | See section 3.A below for proposed retention schedule |
| 3.2 | Do not retain personal data longer than necessary for the stated purpose | Not Started | Implement automated retention enforcement with purge jobs and audit logging |
| 3.3 | Raw ingestion staging data must be purged after successful canonical merge | Pending | Proposed: 90-day retention for raw staging tables post-merge. Automated purge with audit trail. |
| 3.4 | Verification evidence (photos, recordings) must have defined retention periods | Pending | Proposed: 12-month retention unless re-verification extends. Consent withdrawal triggers immediate deletion. |
| 3.5 | Implement automated deletion/anonymization at end of retention period | Not Started | Scheduled job: weekly retention sweep. Records past retention are anonymized (PII nulled, UUID retained for audit continuity). |
| 3.6 | Maintain documentation of all deletions/anonymizations performed | Not Started | Deletion log table: record UUID, data category, deletion timestamp, retention policy applied, actor. |
| 3.7 | Ensure backup retention does not exceed primary retention periods | Not Started | Backup rotation policy: daily backups retained 30 days, monthly backups retained 12 months. Backup purge aligns with primary data retention. |

### 3.A Proposed Retention Schedule

| Data Category | Retention Period | Trigger for Deletion | Justification |
|---------------|-----------------|---------------------|---------------|
| Raw ingestion staging data | 90 days post-merge | Successful merge to canonical table | Only needed for merge reconciliation and error correction |
| Canonical facility record (non-PII) | Indefinite (with periodic review) | Facility confirmed permanently closed + 24 months | Registry integrity requires historical record of facilities |
| Facility contact PII (owner name, phone, email) | Active while facility is operational + 24 months after closure | Facility closure confirmation + 24 months | Contact data needed for verification cycle; retention post-closure for audit |
| Verification evidence (photos) | 12 months from capture, renewable on re-verification | 12 months post-capture or consent withdrawal | Evidence supports validation level; limited retention reduces exposure |
| Verification call/message logs | 6 months from contact | 6 months post-contact | Short-term retention for dispute resolution |
| API access logs | 12 months | Rolling 12-month window | Security monitoring and abuse detection |
| Audit trail (provenance records) | 7 years | 7 years from record creation | Regulatory audit requirement; contains UUIDs not PII |

---

## 4. Data Subject Rights

| # | Requirement | Status | Implementation Notes |
|---|-------------|--------|---------------------|
| **Right of Access (s.34)** | | | |
| 4.1 | Provide mechanism for data subjects to request a copy of their personal data | Not Started | Build self-service portal or designate email channel (dpo@registry-domain). Respond within 30 days per NDPA. |
| 4.2 | Verify identity of requester before disclosing personal data | Not Started | Require identity verification (e.g., confirm registered phone number via OTP) before releasing PII. |
| 4.3 | Provide data in a commonly used electronic format | Not Started | Export as JSON or CSV. Include all personal data fields with processing purpose annotations. |
| **Right to Rectification (s.35)** | | | |
| 4.4 | Allow data subjects to correct inaccurate personal data | Pending | Status history model supports corrections: new record appended, old record flagged as superseded with rectification reason. |
| 4.5 | Process rectification requests within 30 days | Not Started | Define SLA and tracking workflow. Assign to Verification Ops queue. |
| **Right to Erasure (s.36)** | | | |
| 4.6 | Evaluate erasure requests against lawful basis and retention requirements | Not Started | Erasure is not absolute: if legitimate interest or legal obligation applies, document basis for refusal. If consent was the basis, erasure must be honored. |
| 4.7 | Where erasure is granted, remove personal data from all systems including backups within defined timeline | Not Started | Anonymize PII in canonical records (null PII fields, retain facility UUID). Flag backup records for exclusion on next restore. |
| 4.8 | Notify third parties (API partners) who received the data about erasure | Not Started | Partner notification via webhook or email. DUAs must include partner obligation to delete on notification. |
| **Right to Restriction of Processing (s.37)** | | | |
| 4.9 | Support temporary restriction of processing while accuracy is disputed | Not Started | Implement `processing_restricted` flag on facility_contacts. When flagged, PII excluded from all API responses and processing jobs. |
| **Right to Data Portability (s.38)** | | | |
| 4.10 | Provide personal data in a structured, machine-readable format on request | Not Started | Leverage FHIR Location/Organization export format. Additionally support JSON/CSV export of personal data subset. |
| **Right to Object (s.39)** | | | |
| 4.11 | Allow data subjects to object to processing based on legitimate interest | Not Started | On objection: re-assess legitimate interest balance for that specific subject. If objection upheld, restrict processing of their PII. |
| 4.12 | Process objections within 30 days with documented reasoning | Not Started | Objection review workflow: DPO reviews, documents decision, communicates to subject. |

---

## 5. Accountability and Governance

| # | Requirement | Status | Implementation Notes |
|---|-------------|--------|---------------------|
| 5.1 | Appoint a Data Protection Officer (DPO) if processing meets NDPC thresholds (s.31) | Not Started | Assess whether registry meets "large scale processing" threshold. If so, appoint DPO and register with NDPC. |
| 5.2 | Maintain a Record of Processing Activities (ROPA) (s.30) | Not Started | Create and maintain ROPA based on processing activity mapping in section 1.A. Update on any new processing activity. |
| 5.3 | Conduct Data Protection Impact Assessment (DPIA) for high-risk processing (s.32) | Not Started | Geospatial tracking of business locations + contact data processing likely triggers DPIA requirement. Conduct before first ingestion. |
| 5.4 | Implement appropriate technical and organizational security measures (s.39) | Pending | Encryption at rest and in transit. Role-based access control. Audit logging. See threat_model.md for detailed controls. |
| 5.5 | Establish data breach notification procedure (notify NDPC within 72 hours) (s.40) | Not Started | Draft breach response plan. Define severity classification. Test notification workflow. Designate breach response team. |
| 5.6 | Ensure data processing agreements are in place with all processors (s.33) | Not Started | Identify all third-party processors (cloud hosting, geocoding service, SMS gateway). Execute DPAs before engaging. |
| 5.7 | Implement privacy-by-design and privacy-by-default principles (s.27) | In Progress | Schema separates PII from facility data. API defaults to non-PII responses. Validation ladder prevents unearned trust. |
| 5.8 | Document and maintain evidence of compliance | Not Started | This checklist serves as primary compliance tracking document. Supplement with DPIA reports, DPO appointment records, ROPA. |

---

## 6. Cross-Border Data Transfer

| # | Requirement | Status | Implementation Notes |
|---|-------------|--------|---------------------|
| 6.1 | Identify any cross-border transfers of personal data (s.41) | Pending | Assess: cloud hosting region, geocoding API provider jurisdiction, any international partner data sharing. |
| 6.2 | Ensure adequate protection for any cross-border transfer | Not Started | Prefer Nigeria-hosted or Africa-hosted infrastructure. If cross-border transfer required, use NDPC-approved transfer mechanisms (adequacy decision, standard contractual clauses, or binding corporate rules). |
| 6.3 | Document the legal mechanism for each cross-border transfer | Not Started | Maintain transfer impact assessment for each cross-border data flow. |

---

## Compliance Readiness Summary

| Section | Items | Not Started | Pending | In Progress | Complete |
|---------|-------|-------------|---------|-------------|----------|
| 1. Lawful Basis | 6 | 2 | 4 | 0 | 0 |
| 2. Data Minimization | 6 | 0 | 5 | 1 | 0 |
| 3. Storage Limitation | 7 | 4 | 3 | 0 | 0 |
| 4. Data Subject Rights | 12 | 10 | 2 | 0 | 0 |
| 5. Accountability | 8 | 5 | 1 | 2 | 0 |
| 6. Cross-Border Transfer | 3 | 2 | 1 | 0 | 0 |
| **Total** | **42** | **23** | **16** | **3** | **0** |

## Priority Actions

1. **Before first data ingestion:** Complete DPIA (5.3), establish lawful basis for each processing activity (1.1–1.6), appoint DPO if required (5.1).
2. **Before verification outreach:** Develop consent mechanism for contact verification (1.2) and photo evidence collection (1.2, 2.5).
3. **Before API launch:** Implement field-level access controls (2.4), data subject rights request channel (4.1), breach notification procedure (5.5).
4. **Before partner data sharing:** Execute DUAs with all partners (REP-005 in risk register), establish cross-border transfer mechanisms if applicable (6.1–6.3).
