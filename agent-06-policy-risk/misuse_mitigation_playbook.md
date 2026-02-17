# Misuse Mitigation Playbook — Nigeria Pharmacy Registry

> **Version:** 1.0
> **Last Updated:** 2026-02-16
> **Owner:** Policy & Risk Workstream (Agent 06)
> **Review Cadence:** Quarterly, or after any playbook activation
> **Related Documents:** threat_model.md, risk_register.md

## Purpose

This playbook defines detection signals and response procedures for each identified
misuse threat against the Nigeria Pharmacy Registry. Every threat has a structured
response procedure with clear escalation paths.

## Severity Levels

| Level | Label | Definition | Response SLA |
|-------|-------|------------|-------------|
| SEV-1 | Critical | Active exploitation causing data corruption, PII exposure, or registry integrity compromise | Respond within 1 hour; resolve or contain within 4 hours |
| SEV-2 | High | Confirmed misuse attempt with potential for significant harm if unchecked | Respond within 4 hours; resolve within 24 hours |
| SEV-3 | Medium | Suspicious activity requiring investigation; no confirmed harm yet | Respond within 24 hours; resolve within 72 hours |
| SEV-4 | Low | Minor anomaly or policy violation; no immediate harm | Respond within 72 hours; resolve within 1 week |

## Escalation Contacts

| Role | Responsibility | Escalation Trigger |
|------|---------------|--------------------|
| On-Call Engineer | Initial triage, containment | All automated alerts |
| Security Lead | Investigation, forensics | SEV-1 and SEV-2 incidents |
| Policy Lead / DPO | Regulatory notification, data subject communication | PII exposure, NDPA violations |
| Project Lead | Executive communication, resource allocation | SEV-1 incidents, media involvement |
| Legal Counsel | Regulatory engagement, enforcement action | Regulator inquiry, legal threats |

---

## Threat 1: Fake Pharmacy Registration

**Threat Reference:** S-1, FRD-001
**Severity if Realized:** SEV-1 (if verified status gained) / SEV-3 (if caught at L0)

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| FPR-D1 | Burst of new facility records from a single source with no prior history | Ingestion monitoring dashboard | >50 new records from new source in 24h |
| FPR-D2 | Facility addresses that do not geocode to valid locations or resolve to empty lots | Post-ingestion geocoding validation | Geocode confidence score <0.3 or no match |
| FPR-D3 | Cluster of new registrations at the same or nearly identical coordinates | Geospatial proximity analysis in dedup pipeline | >3 facilities within 50m radius from same batch |
| FPR-D4 | Facility name patterns matching known fraudulent naming conventions | NLP pattern matching on facility names | Match against curated watchlist |
| FPR-D5 | Phone numbers registered to multiple different facilities in the same batch | Contact deduplication check | Same phone on >3 distinct facilities |
| FPR-D6 | No corroborating record found in any other data source after 90 days at L0 | Periodic L0 review job | L0 for >90 days with zero corroboration |

### Response Procedure

```
1. TRIAGE (On-Call Engineer)
   ├── Confirm signal is not a false positive (e.g., legitimate bulk partner upload)
   ├── If legitimate bulk upload → verify source attribution, close alert
   └── If suspicious → proceed to INVESTIGATE

2. INVESTIGATE (Security Lead + Data Acquisition Lead)
   ├── Quarantine affected records: set processing_restricted flag
   ├── Trace source: identify API key, IP, user agent, ingestion channel
   ├── Check source_registry.json for known source match
   ├── Cross-reference flagged facilities against PCN license database
   └── Document findings in incident log

3. CONTAIN
   ├── If confirmed fake: mark records as "flagged_fraudulent" in status history
   ├── Suspend or revoke the API key / ingestion credential used
   ├── If records advanced past L0: initiate emergency status downgrade
   └── Notify any partners who may have received the affected data

4. REMEDIATE
   ├── Purge fraudulent records from canonical registry (retain in audit log)
   ├── Update source reputation tracking
   ├── Review and tighten ingestion validation rules if pattern is novel
   └── If large-scale: publish transparency notice on data quality

5. POST-INCIDENT
   ├── Incident report within 72 hours
   ├── Update FPR detection thresholds based on attack pattern
   └── Add new patterns to watchlist (FPR-D4)
```

---

## Threat 2: Impersonation of Regulators

**Threat Reference:** S-2, FRD-002
**Severity if Realized:** SEV-1

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| REG-D1 | L3 status change request from unregistered institutional key | API authentication middleware | Any L3 request from non-whitelisted key |
| REG-D2 | Regulator feed data arriving outside expected schedule or volume | Ingestion monitoring | Data received outside scheduled window or >2x expected volume |
| REG-D3 | Regulator-attributed changes to facilities not in the expected geographic scope | Cross-reference with regulator jurisdiction | PCN data modifying facility outside PCN's purview |
| REG-D4 | Email or communication claiming regulator authority requesting manual status override | Admin portal request logs + email monitoring | Any manual L3 override request |
| REG-D5 | Sudden spike in L3 promotions without corresponding regulator feed update | Status change monitoring | >10 L3 promotions in 24h without feed ingestion event |

### Response Procedure

```
1. TRIAGE (On-Call Engineer)
   ├── Verify the claimed institutional identity through out-of-band channel
   │   (phone call to known regulator contact, not contact info provided by requester)
   └── If identity cannot be verified → escalate immediately to SEV-1

2. INVESTIGATE (Security Lead + Regulatory Integration Lead)
   ├── Freeze all pending L3 status changes
   ├── Audit last 48 hours of L3 changes for unauthorized promotions
   ├── Identify all records modified by the suspected actor
   ├── Verify institutional API key integrity (has it been compromised?)
   └── Contact regulator through established relationship to confirm/deny

3. CONTAIN
   ├── Revoke compromised institutional API key immediately
   ├── Roll back any unauthorized L3 status changes (append downgrade to history)
   ├── Issue new API key to legitimate regulator through secure channel
   └── Temporarily increase approval requirements: all L3 changes require Security Lead sign-off

4. REMEDIATE
   ├── Implement additional authentication for regulator feeds (e.g., signed payloads)
   ├── Notify affected facility operators if their status was incorrectly elevated
   ├── Update regulator feed validation rules
   └── Conduct root cause analysis on how impersonation succeeded

5. POST-INCIDENT
   ├── Incident report to regulator partner within 48 hours
   ├── Review and strengthen regulator integration authentication
   ├── Update threat model with new attack patterns
   └── Brief all team members on social engineering indicators
```

---

## Threat 3: Harassment of Pharmacy Owners via Exposed Contact Data

**Threat Reference:** I-1, REP-003
**Severity if Realized:** SEV-2

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| HAR-D1 | Single API key requesting contact data for large number of facilities | API access logs, per-key analytics | >200 contact-data requests in 24h from single key |
| HAR-D2 | Pharmacy owner reports harassment and attributes it to registry data exposure | Abuse reporting channel (email, hotline) | Any report |
| HAR-D3 | Scraping pattern: sequential facility ID enumeration requesting contact fields | API request pattern analysis | Sequential ID requests with contact field selection |
| HAR-D4 | Partner data used outside scope defined in DUA | Partner audit or abuse report | Any confirmed DUA violation |
| HAR-D5 | Social media or public forum posts containing registry contact data in bulk | External monitoring (periodic manual check or automated scan) | Any bulk PII publication attributed to registry |

### Response Procedure

```
1. TRIAGE (On-Call Engineer + Policy Lead)
   ├── If direct harassment report: acknowledge receipt within 4 hours
   ├── Identify the API key(s) that accessed the victim's contact data
   └── Assess scope: single target or systematic campaign

2. INVESTIGATE (Security Lead + Policy Lead)
   ├── Pull complete access log for suspected API key(s)
   ├── Determine data accessed: which facilities, which fields, over what time period
   ├── Contact API key holder to request explanation (if identifiable)
   ├── If DUA exists, review for violation
   └── Preserve evidence for potential law enforcement referral

3. CONTAIN
   ├── Immediately suspend suspected API key(s)
   ├── If bulk PII published externally: issue takedown request to platform
   ├── Offer affected pharmacy owner(s):
   │   ├── Restriction of their contact data from all API responses
   │   ├── Replacement of exposed contact information in registry
   │   └── Guidance on reporting harassment to law enforcement
   └── If DUA violation confirmed: formal notice to partner organization

4. REMEDIATE
   ├── Tighten rate limits on contact data access
   ├── Review access tier requirements: consider stricter vetting for partner keys
   ├── If systematic scraping: implement additional anti-scraping measures
   │   (CAPTCHA on high-volume requests, request signing, IP reputation)
   └── Update DUA template to strengthen enforcement provisions

5. POST-INCIDENT
   ├── Follow up with affected pharmacy owner(s) within 1 week
   ├── If NDPA personal data breach threshold met: notify NDPC within 72 hours
   ├── Publish anonymized incident summary in quarterly transparency report
   └── Review and update harassment detection thresholds
```

---

## Threat 4: Data Scraping

**Threat Reference:** I-2
**Severity if Realized:** SEV-3 (non-PII) / SEV-2 (if PII scraped)

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| SCR-D1 | High-volume sequential pagination through facility listings | API request pattern analysis | >500 paginated requests in 1 hour from single key/IP |
| SCR-D2 | Systematic geographic sweep: bounding box queries covering entire states | Geospatial query monitoring | Queries collectively covering >50% of a state in 24h |
| SCR-D3 | Multiple API keys from same organization or IP range making parallel requests | API key correlation analysis | >3 keys from same IP subnet active simultaneously |
| SCR-D4 | Requests with automated user-agent signatures or no user-agent | Request header analysis | Known bot UA strings or empty UA |
| SCR-D5 | Unusual access time patterns (e.g., perfectly regular intervals suggesting automation) | Time-series analysis on request logs | Request interval standard deviation <0.5 seconds over 100+ requests |

### Response Procedure

```
1. TRIAGE (On-Call Engineer)
   ├── Verify signal against normal usage patterns (some partners legitimately access many records)
   ├── Check if key has an active DUA permitting bulk access
   └── If no DUA or pattern exceeds DUA scope → proceed to INVESTIGATE

2. INVESTIGATE (Security Lead)
   ├── Profile the scraping activity: scope, duration, data accessed
   ├── Determine if PII fields were included in scraped data
   ├── Identify the actor: API key owner, IP geolocation, organization
   └── Assess commercial or malicious intent based on pattern

3. CONTAIN
   ├── Apply progressive enforcement:
   │   ├── First offense: rate-limit key to 10 requests/minute
   │   ├── Continued after rate-limit: suspend key with notice
   │   └── Repeated violation: permanent revocation + IP block
   ├── If PII was scraped: escalate to SEV-2 and invoke Threat 3 procedures
   └── Block identified bot user-agents at CDN/reverse proxy layer

4. REMEDIATE
   ├── Review API design for scraping resistance:
   │   ├── Require API key even for public tier
   │   ├── Implement response-based rate limiting (slow degradation vs hard block)
   │   ├── Consider HMAC request signing for partner tier
   │   └── Reduce maximum page size if current limit enables efficient scraping
   ├── Update terms of use if enforcement language is insufficient
   └── Consider adding honeypot records to detect unauthorized redistribution

5. POST-INCIDENT
   ├── Document scraping pattern for future detection
   ├── Update SCR detection thresholds
   └── If data redistributed: pursue takedown through legal channels
```

---

## Threat 5: Verification Fraud (Bribery / Collusion)

**Threat Reference:** FRD-004, S-3
**Severity if Realized:** SEV-2

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| VFR-D1 | Single verification agent with anomalously high approval rate | Agent performance analytics | Approval rate >95% over 50+ verifications (vs. team average) |
| VFR-D2 | Verification evidence photos with inconsistent or stripped GPS metadata | Evidence metadata validation | GPS coordinates >500m from facility address or EXIF data absent |
| VFR-D3 | Multiple verification evidence photos appear visually similar or identical | Image similarity analysis (perceptual hash) | Perceptual hash similarity >90% across different facilities |
| VFR-D4 | Verification completed unusually quickly relative to travel distance | Time-distance analysis | Verification submitted <30 min after previous verification >10km away |
| VFR-D5 | Facility operator and verification agent share contact information or social connections | Contact cross-reference | Shared phone number, email domain, or social media connection |
| VFR-D6 | Cluster of facilities in same area all verified by same agent despite randomized assignment | Assignment audit | >5 facilities in same ward assigned to same agent in 30-day window |

### Response Procedure

```
1. TRIAGE (Verification Ops Lead)
   ├── Review agent's recent verification portfolio for anomaly pattern
   ├── Check if randomized assignment was properly applied
   └── If multiple signals triggered → escalate to INVESTIGATE

2. INVESTIGATE (Security Lead + Verification Ops Lead)
   ├── Suspend agent from new assignments pending investigation
   ├── Pull all verifications by agent in last 90 days
   ├── Re-verify a random sample (minimum 20%) using independent agent
   ├── Compare original evidence against re-verification findings
   ├── Interview agent if discrepancies found
   └── Check for financial connections between agent and facility operators

3. CONTAIN
   ├── If fraud confirmed:
   │   ├── Revoke agent's access immediately
   │   ├── Downgrade all facilities verified solely by this agent to L0
   │   ├── Flag affected facilities for re-verification by independent agent
   │   └── Notify facility operators that re-verification will occur
   └── If inconclusive: place agent on supervised verification (all submissions require peer review)

4. REMEDIATE
   ├── Increase spot-check rate from 10% to 20% for 90 days
   ├── Implement mandatory photo liveness checks (e.g., agent selfie at location)
   ├── Review and strengthen randomized assignment algorithm
   ├── Consider requiring dual-agent verification for high-risk areas
   └── Update agent onboarding training with fraud case studies

5. POST-INCIDENT
   ├── Document case for internal fraud registry (anonymized)
   ├── Report to law enforcement if criminal fraud threshold met
   ├── Review compensation structure for perverse incentives
   └── Update VFR detection model with new indicators
```

---

## Threat 6: Admin Portal Compromise

**Threat Reference:** E-2
**Severity if Realized:** SEV-1

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| ADM-D1 | Login from unrecognized IP address or geographic location | Auth system geolocation + IP reputation | Login from country not in admin whitelist |
| ADM-D2 | Multiple failed login attempts followed by successful login | Auth system brute-force detection | >5 failed attempts then success within 1 hour |
| ADM-D3 | Admin session active outside normal working hours | Session monitoring | Activity between 23:00–05:00 local time |
| ADM-D4 | Bulk data export or mass status change initiated | Admin action logging | Export >1000 records or status change >50 records in single session |
| ADM-D5 | Admin account used simultaneously from multiple IP addresses | Session concurrency monitoring | >1 active session for same admin user |
| ADM-D6 | Privilege escalation: admin modifying their own role or creating new admin accounts | User management audit log | Self-role-modification or admin creation without approval workflow |

### Response Procedure

```
1. TRIAGE (On-Call Engineer)
   ├── Immediately terminate all active sessions for the suspected admin account
   ├── Lock the admin account pending investigation
   ├── Notify Security Lead and affected admin user through out-of-band channel
   └── If ADM-D4 triggered: assess what data was exported/modified

2. INVESTIGATE (Security Lead)
   ├── Determine if compromise was credential theft, session hijack, or insider
   ├── Audit all actions taken during the suspicious session(s)
   ├── Review auth logs: how was access obtained? Was MFA bypassed?
   ├── Check for persistence: new API keys created, new admin accounts, scheduled tasks
   └── Assess blast radius: what data was accessed, modified, or exported

3. CONTAIN
   ├── Rotate all API keys and admin credentials
   ├── Revoke any API keys or accounts created during compromise window
   ├── Roll back data modifications made during compromise (using audit trail)
   ├── If data exported: treat as data breach (invoke NDPA breach procedures)
   └── Temporarily elevate all admin actions to require dual-approval

4. REMEDIATE
   ├── Force password reset for all admin accounts
   ├── Review and strengthen MFA implementation
   ├── Implement IP allowlisting for admin portal access
   ├── Add anomaly-based session monitoring
   └── Conduct security awareness training focused on phishing/credential theft

5. POST-INCIDENT
   ├── Full incident report within 48 hours
   ├── If personal data breach: notify NDPC within 72 hours (NDPA s.40)
   ├── If data subjects affected: notify them of breach and remediation
   ├── External security audit of admin portal authentication
   └── Update ADM detection rules based on attack vector
```

---

## Threat 7: Insider Validation Level Override

**Threat Reference:** E-3
**Severity if Realized:** SEV-1

### Detection Signals

| Signal ID | Signal | Detection Method | Threshold |
|-----------|--------|-----------------|-----------|
| IVO-D1 | Status change to L3+ without corresponding dual-approval record | Automated audit job (daily) | Any L3+ record missing approval chain |
| IVO-D2 | Direct database write to status table bypassing application layer | Database audit log (pg_audit) | Any INSERT/UPDATE to status_history not from app service account |
| IVO-D3 | Same user both initiates and approves a status change | Workflow audit | Initiator_id == approver_id on any L2+ promotion |
| IVO-D4 | L3 promotion for facility not present in latest regulator feed | Cross-reference job (weekly) | L3 facility with no matching PCN/NAFDAC record |
| IVO-D5 | High volume of status changes by a single admin in short period | Admin action rate monitoring | >20 status changes in 1 hour from single admin |

### Response Procedure

```
1. TRIAGE (On-Call Engineer + Security Lead)
   ├── Freeze the affected facility record(s)
   ├── Verify whether the override was authorized through exception process
   └── If no documented exception → escalate to SEV-1

2. INVESTIGATE (Security Lead + Policy Lead)
   ├── Identify the actor(s) who performed the override
   ├── Determine method: application workflow bypass, direct DB access, or collusion
   ├── Audit all status changes by the identified actor in last 90 days
   ├── Cross-reference against regulator feeds and verification evidence
   └── Interview involved parties separately

3. CONTAIN
   ├── Revert unauthorized status changes (append downgrade with "unauthorized_override" reason)
   ├── Suspend database and admin access for involved actors
   ├── Flag all facilities touched by involved actors for independent review
   └── Notify any partners who may have relied on the falsely elevated status

4. REMEDIATE
   ├── Strengthen database access controls: remove direct write access for all human users
   ├── Implement database triggers that reject status writes not originating from application
   ├── Add automated daily cross-reference: all L3+ records vs. regulator feeds
   ├── Review separation of duties policy
   └── If applicable: initiate HR process for policy violation

5. POST-INCIDENT
   ├── Incident report to project leadership within 24 hours
   ├── If external parties relied on false status: formal notification and correction
   ├── Review and update dual-approval workflow implementation
   └── Add case to insider threat awareness training materials
```

---

## Cross-Reference: Playbook to Risk Register

| Playbook Threat | Risk Register IDs | Threat Model IDs |
|----------------|-------------------|-------------------|
| Fake Pharmacy Registration | FRD-001, FRD-003 | S-1 |
| Impersonation of Regulators | FRD-002, REG-001 | S-2 |
| Harassment via Contact Data | PRV-001, REP-003 | I-1 |
| Data Scraping | PRV-002 | I-2 |
| Verification Fraud | FRD-004 | S-3 |
| Admin Portal Compromise | OPS-002 | E-2 |
| Insider Validation Override | FRD-005 | E-3 |

---

## Playbook Maintenance

1. **After every incident:** Update the relevant playbook section with lessons learned within 1 week.
2. **Quarterly:** Review all detection thresholds against actual traffic patterns; tune to reduce false positives.
3. **Semi-annually:** Tabletop exercise — walk through one playbook scenario with the full team.
4. **On architecture change:** Review all detection signals for continued applicability.
5. **On new threat identification:** Add new playbook section following the template structure above.
