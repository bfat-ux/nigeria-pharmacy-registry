# Risk Register — Nigeria Pharmacy Registry

> **Version:** 1.0
> **Last Updated:** 2026-02-16
> **Owner:** Policy & Risk Workstream (Agent 06)
> **Review Cadence:** Quarterly, or after any major incident

## Scoring Criteria

| Score | Likelihood                        | Impact                                  |
|-------|-----------------------------------|-----------------------------------------|
| 1     | Rare — less than 5% chance/year   | Negligible — no operational disruption  |
| 2     | Unlikely — 5–20% chance/year      | Minor — limited to one workstream       |
| 3     | Possible — 20–50% chance/year     | Moderate — degrades registry trust      |
| 4     | Likely — 50–80% chance/year       | Major — regulatory action or data loss  |
| 5     | Almost certain — >80% chance/year | Critical — registry shutdown or lawsuit |

**Risk Score** = Likelihood x Impact. Risks scoring >=12 require an active mitigation plan and executive visibility.

---

## Risk Register

### Privacy Risks

| Risk ID | Category | Description | Likelihood | Impact | Risk Score | Mitigation | Owner |
|---------|----------|-------------|------------|--------|------------|------------|-------|
| PRV-001 | Privacy | Exposure of pharmacy owner personal contact data (phone, email) through API responses or data scraping | 4 | 4 | 16 | Implement tiered access control: public API returns facility-level data only; owner PII requires authenticated partner role. Rate-limit all endpoints. | API Lead + Policy Lead |
| PRV-002 | Privacy | Unauthorized bulk export of registry data enabling commercial exploitation or harassment campaigns | 3 | 4 | 12 | Enforce per-key pagination limits (max 100 records/request). Log and alert on bulk-pattern access. Require data-use agreements for partner API keys. | API Lead |
| PRV-003 | Privacy | Location data combined with external datasets to re-identify pharmacy owners or infer business revenue | 3 | 3 | 9 | Restrict geospatial precision in public tier to ward-level centroids. Full coordinates available only to authenticated partners with signed DUA. | Data Architecture Lead |
| PRV-004 | Privacy | Retention of raw ingested data beyond operational need, violating NDPA storage limitation principle | 3 | 3 | 9 | Define retention schedule: raw ingestion staging tables purged 90 days after successful canonical merge. Automate purge with audit trail. | Data Architecture Lead |
| PRV-005 | Privacy | Verification evidence (photos, call recordings) stored without consent or beyond necessary retention | 2 | 4 | 8 | Collect explicit consent at point of verification. Store evidence in encrypted, access-controlled bucket with 12-month auto-expiry unless re-verified. | Verification Ops Lead |

### Regulatory Risks

| Risk ID | Category | Description | Likelihood | Impact | Risk Score | Mitigation | Owner |
|---------|----------|-------------|------------|--------|------------|------------|-------|
| REG-001 | Regulatory | PCN challenges registry legitimacy or issues cease-and-desist for publishing unlicensed facility data | 3 | 5 | 15 | Proactive engagement with PCN via MoU process. Label all records with validation level; never claim regulatory endorsement without L3+ status. | Regulatory Integration Lead |
| REG-002 | Regulatory | Non-compliance with NDPA 2023 requirements leading to NDPC enforcement action or fine | 3 | 5 | 15 | Complete NDPA compliance checklist (see ndpa_compliance_checklist.md). Appoint Data Protection Officer. File with NDPC if processing threshold is met. | Policy Lead + DPO |
| REG-003 | Regulatory | NAFDAC or state pharmacy boards demand data access without clear legal basis or data sharing agreement | 2 | 4 | 8 | Pre-draft data sharing agreement templates. Only share data under signed agreement with defined purpose, retention, and onward-sharing restrictions. | Regulatory Integration Lead |
| REG-004 | Regulatory | Changes to Nigeria data protection law or pharmacy regulations invalidate current schema or processes | 2 | 3 | 6 | Monitor NDPC gazette and PCN circulars quarterly. Maintain regulatory change log. Design schema to be extensible for new required fields. | Policy Lead |
| REG-005 | Regulatory | Operating across state boundaries triggers conflicting state-level pharmacy regulations | 2 | 3 | 6 | Start with single-state pilot (Lagos). Document state-specific regulatory variance before expansion. Engage state pharmacy boards individually. | Regulatory Integration Lead |

### Fraud Risks

| Risk ID | Category | Description | Likelihood | Impact | Risk Score | Mitigation | Owner |
|---------|----------|-------------|------------|--------|------------|------------|-------|
| FRD-001 | Fraud | Fake pharmacy registration: bad actors submit fictitious facilities to appear legitimate for supply chain fraud | 4 | 5 | 20 | All ingested records start at L0 (Mapped). Require L2+ evidence before any "verified" label. Cross-reference with PCN license database at L3. Flag facilities with no corroborating source. | Verification Ops Lead |
| FRD-002 | Fraud | Impersonation of regulator: attacker uses forged PCN/NAFDAC credentials to elevate facility status to L3 | 2 | 5 | 10 | Regulator-sourced status changes require cryptographically signed feeds or verified institutional API keys. No manual override to L3 without dual-approval workflow. | Regulatory Integration Lead |
| FRD-003 | Fraud | Duplicate facility records created intentionally to game verification metrics or claim multiple identities | 3 | 3 | 9 | Run deduplication pipeline on every ingestion batch. Alert on facilities with suspiciously similar names/addresses but different IDs. Manual review queue for composite scores above merge threshold. | Dedup Lead |
| FRD-004 | Fraud | Verification bribery: field agents accept payment to falsely confirm facility existence at L2 | 3 | 4 | 12 | Randomized verification assignment. Photo evidence must include GPS-embedded metadata. Spot-check 10% of L2 verifications with independent re-verification. | Verification Ops Lead |
| FRD-005 | Fraud | Pharmacy identity theft: real facility's record hijacked by changing contact info to attacker-controlled data | 2 | 4 | 8 | Contact info changes trigger re-verification workflow. Notify previous contact on record. Require evidence for ownership transfer. Append-only status history prevents silent overwrites. | Data Architecture Lead |

### Operational Risks

| Risk ID | Category | Description | Likelihood | Impact | Risk Score | Mitigation | Owner |
|---------|----------|-------------|------------|--------|------------|------------|-------|
| OPS-001 | Operational | Data source feeds (CSV/Excel from regulators) arrive in inconsistent or corrupt formats breaking ingestion | 4 | 3 | 12 | Build format-validation layer at ingestion boundary. Quarantine malformed records for manual review. Maintain per-source schema mapping configuration. | Data Acquisition Lead |
| OPS-002 | Operational | Database failure or data corruption causes loss of canonical registry records | 2 | 5 | 10 | Daily automated backups with point-in-time recovery. Store backups in separate region. Test restore procedure quarterly. | Infrastructure Lead |
| OPS-003 | Operational | Low-bandwidth environments cause API timeouts, degrading trust among field verification agents | 4 | 3 | 12 | Design API for low-bandwidth: compressed responses, offline-capable mobile verification app with sync queue, paginated endpoints with small default page size. | API Lead |
| OPS-004 | Operational | Single point of failure in deduplication pipeline causes duplicate records to propagate to verified status | 2 | 4 | 8 | Dedup runs as mandatory pre-verification gate. No record advances past L0 without dedup clearance. Monitor duplicate-detection rate; alert on sudden drops. | Dedup Lead |
| OPS-005 | Operational | Key personnel departure causes loss of institutional knowledge about source mappings or verification procedures | 3 | 3 | 9 | Document all source mappings in source_registry.json. Maintain runbooks for every operational procedure. Ensure at least two people are trained on each critical workflow. | Project Lead |
| OPS-006 | Operational | Third-party geocoding service unavailable, blocking geospatial enrichment of new records | 3 | 2 | 6 | Support multiple geocoding providers with automatic failover. Cache geocoding results. Degrade gracefully: ingest records without coordinates and backfill when service recovers. | Data Architecture Lead |

### Reputational Risks

| Risk ID | Category | Description | Likelihood | Impact | Risk Score | Mitigation | Owner |
|---------|----------|-------------|------------|--------|------------|------------|-------|
| REP-001 | Reputational | Public perception that registry legitimizes unregulated or dangerous pharmacies | 3 | 5 | 15 | Clear public communication: validation level is prominently displayed. L0/L1 records explicitly labeled "unverified." Public-facing documentation explains the validation ladder. | Policy Lead + Comms |
| REP-002 | Reputational | Verified pharmacy found selling counterfeit drugs, damaging registry credibility | 2 | 5 | 10 | Registry does not vouch for drug quality — only facility existence and licensing status. Disclaimers on all outputs. Rapid status downgrade procedure when adverse report received. | Policy Lead |
| REP-003 | Reputational | Contact data in registry used to harass or extort pharmacy owners (e.g., by criminal networks) | 3 | 4 | 12 | Restrict contact data to authenticated users with legitimate purpose. Implement abuse reporting mechanism. Suspend API keys associated with harassment patterns. | API Lead + Policy Lead |
| REP-004 | Reputational | Media reports inaccurate record counts or misrepresents registry completeness as endorsement | 3 | 3 | 9 | Publish methodology documentation openly. Include data quality metrics and coverage disclaimers in all public outputs. Designate media spokesperson. | Comms + Policy Lead |
| REP-005 | Reputational | Partner organization misuses shared data, causing public backlash attributed to the registry | 2 | 4 | 8 | Data Use Agreements (DUAs) with all partners. DUAs include audit rights and breach notification requirements. Public transparency report on data sharing annually. | Policy Lead |

---

## Risk Heatmap Summary

| Risk Score | Risk IDs |
|------------|----------|
| 20 (Critical) | FRD-001 |
| 15–16 (High) | PRV-001, REG-001, REG-002, REP-001 |
| 12 (Elevated) | PRV-002, FRD-004, OPS-001, OPS-003, REP-003 |
| 8–10 (Moderate) | PRV-003, PRV-004, PRV-005, REG-003, FRD-002, FRD-003, FRD-005, OPS-002, OPS-004, OPS-005, REP-002, REP-004, REP-005 |
| <=6 (Low) | REG-004, REG-005, OPS-006 |

## Next Review Actions

1. **Immediate:** Finalize mitigation plans for all risks scoring >=12.
2. **Within 30 days:** Assign named individuals (not just roles) to each Owner field.
3. **Quarterly:** Re-score all risks based on operational experience and any incidents.
4. **On incident:** Update register within 48 hours of any materialized risk.
