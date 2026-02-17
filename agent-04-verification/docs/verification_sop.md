# Verification Standard Operating Procedures

> Nigeria Pharmacy Registry — Agent 04
> Version: 1.0
> Last updated: 2026-02-17

## 1. Purpose

This document defines the standard operating procedures for advancing pharmacy
and PPMV records through the Validation Ladder (L0 → L1 → L2 → L3). Each
transition requires specific evidence, follows a defined workflow, and produces
an append-only audit trail in the `validation_status_history` table.

**Cardinal rule:** A record NEVER moves up the ladder without logged evidence.
Status changes are NEVER in-place updates — every transition appends a new row
to the history table with the actor, timestamp, evidence reference, and outcome.

---

## 2. Roles

| Role | Description |
|------|-------------|
| **Verification Agent (VA)** | Conducts outbound calls and records evidence for L0→L1 transitions. |
| **Field Verifier (FV)** | Performs location visits, captures photos, and collects L1→L2 evidence. |
| **Data Steward (DS)** | Reviews evidence, approves level transitions, handles edge cases. |
| **Registry Admin (RA)** | Manages regulator data imports, approves L2→L3 transitions, oversees disputes. |
| **System** | Automated processes (batch regulator cross-reference, scheduled re-verification). |

---

## 3. Transition: L0 (Mapped) → L1 (Contact Confirmed)

### 3.1 Objective

Confirm that the pharmacy/PPMV at the recorded address is reachable via phone
or email and that a responsible person acknowledges the listing.

### 3.2 Prerequisites

- Record exists at L0 with a `source_id` and provenance trail.
- At least one phone number or email address is on file.

### 3.3 Procedure

| Step | Actor | Action |
|------|-------|--------|
| 1 | System | Generate a daily work queue of L0 records prioritized by: (a) state coverage targets, (b) record age, (c) data source reliability score. |
| 2 | VA | Claim a batch from the work queue (max 50 records per session). |
| 3 | VA | Attempt outbound contact using the phone script (Section 3.5) or email template (Section 3.6). |
| 4 | VA | Record the outcome in the evidence record (see `evidence_schema.json`). |
| 5 | VA | If contact confirmed → submit L1 transition request with evidence. |
| 5a | VA | If contact failed → log the attempt; after 3 failed attempts across different days/times, flag for Data Steward review. |
| 6 | DS | Review flagged records. Options: (a) update contact info and re-queue, (b) mark as "Contact Unverifiable" with reason. |

### 3.4 Success Criteria

A contact attempt is "confirmed" when ALL of the following are true:

1. A person answered and identified themselves as associated with the pharmacy/PPMV.
2. The person confirmed the facility name (exact or recognizable variant).
3. The person confirmed the facility is currently operating.
4. The VA captured the respondent's name and role (e.g., pharmacist, attendant, owner).

### 3.5 Phone Verification Script

> **Opening:**
>
> "Good [morning/afternoon]. My name is [Agent Name] calling from the Nigeria
> Pharmacy Registry. We are building a national directory of licensed pharmacies
> and patent medicine stores. I am calling to confirm some basic details about
> your facility. This will take about two minutes. May I proceed?"
>
> **If yes, continue:**
>
> 1. "Can you confirm the name of your pharmacy or medicine store?"
>    — Record response. Compare against registry name.
>
> 2. "Is the facility currently open and dispensing?"
>    — Record yes/no. If no, ask: "When did it close?" and record date.
>
> 3. "Can you confirm your street address or nearest landmark?"
>    — Record response. Compare against registry address.
>
> 4. "May I have the name and role of the person I am speaking with?"
>    — Record name and role (pharmacist, attendant, owner, manager).
>
> 5. "Is there a preferred phone number for future contact?"
>    — Record if different from the number called.
>
> **Closing:**
>
> "Thank you for your time. Your facility has been confirmed in our registry.
> If you ever need to update your details, you can contact us at [registry
> contact]. Have a good day."

### 3.6 Email Verification Template

**Subject:** Nigeria Pharmacy Registry — Confirm Your Facility Listing

**Body:**

> Dear [Facility Contact],
>
> The Nigeria Pharmacy Registry is building a national directory of licensed
> pharmacies and patent medicine vendors. Our records show the following
> information for your facility:
>
> - **Facility Name:** [name]
> - **Address:** [address]
> - **LGA / State:** [lga], [state]
>
> Please reply to this email to confirm or correct these details. You may
> also call us at [registry phone] or visit [registry portal URL].
>
> If we do not hear from you within 14 days, we will attempt to reach you
> by phone.
>
> Regards,
> Nigeria Pharmacy Registry Team

### 3.7 Evidence Requirements for L1

| Field | Required | Description |
|-------|----------|-------------|
| `evidence_type` | Yes | `contact_confirmation` |
| `capture_method` | Yes | `phone_call` or `email_reply` or `sms_reply` |
| `captured_by` | Yes | VA's actor ID (UUID) |
| `captured_at` | Yes | ISO 8601 timestamp (UTC) |
| `respondent_name` | Yes | Name of person who confirmed |
| `respondent_role` | Yes | Role at the facility |
| `facility_name_confirmed` | Yes | Name as stated by respondent |
| `operating_status_confirmed` | Yes | `operating`, `closed`, `relocated` |
| `verification_notes` | No | Free-text notes on the call/exchange |
| `call_duration_seconds` | No | Duration of phone call |

---

## 4. Transition: L1 (Contact Confirmed) → L2 (Evidence Documented)

### 4.1 Objective

Obtain physical location evidence confirming the pharmacy/PPMV exists at the
stated address. This may include a storefront photograph, GPS coordinates
captured on-site, or a combination of both.

### 4.2 Prerequisites

- Record is at L1 with a confirmed contact.
- Record has been at L1 for at least 48 hours (cooling period to allow
  contact corrections).

### 4.3 Procedure

| Step | Actor | Action |
|------|-------|--------|
| 1 | System | Generate field verification batches grouped by LGA for route efficiency. |
| 2 | FV | Receive assignment with facility list, addresses, and contact numbers. |
| 3 | FV | Travel to each facility. At each location: |
| 3a | FV | Capture a geo-tagged photograph of the storefront showing signage. |
| 3b | FV | Record GPS coordinates via the verification app (minimum accuracy: 25m). |
| 3c | FV | Visually confirm that the facility is operating (stock visible, staff present). |
| 3d | FV | Introduce self and provide registry information card to the facility. |
| 4 | FV | Submit evidence package through the verification app. |
| 5 | DS | Review submitted evidence within 72 hours. |
| 5a | DS | If evidence is complete and consistent → approve L2 transition. |
| 5b | DS | If evidence is incomplete or inconsistent → return to FV with notes. |

### 4.4 Success Criteria

1. At least one geo-tagged photograph clearly shows the facility signage.
2. GPS coordinates are within 100 meters of the registered address.
3. The facility appears operational at time of visit.
4. The evidence package passes Data Steward review.

### 4.5 Evidence Requirements for L2

| Field | Required | Description |
|-------|----------|-------------|
| `evidence_type` | Yes | `location_confirmation` |
| `capture_method` | Yes | `field_visit` |
| `captured_by` | Yes | FV's actor ID (UUID) |
| `captured_at` | Yes | ISO 8601 timestamp (UTC) |
| `storage_reference` | Yes | URI to stored photo(s) in evidence bucket |
| `gps_latitude` | Yes | Latitude captured on-site (WGS84) |
| `gps_longitude` | Yes | Longitude captured on-site (WGS84) |
| `gps_accuracy_meters` | Yes | Reported GPS accuracy |
| `facility_operational` | Yes | Boolean — was the facility open and operating? |
| `signage_visible` | Yes | Boolean — was facility signage visible? |
| `verification_notes` | No | Observations (e.g., "co-located with clinic") |
| `photo_count` | No | Number of photos captured |

### 4.6 Photo Capture Guidelines

1. **Minimum one photo** showing the exterior with readable signage.
2. Photos must have EXIF GPS metadata enabled (the verification app enforces this).
3. Do NOT photograph patients, prescriptions, or controlled substance storage.
4. If signage is absent, photograph the building with a landmark reference and
   note in `verification_notes`.
5. Maximum file size per photo: 10 MB. Accepted formats: JPEG, PNG.

---

## 5. Transition: L2 (Evidence Documented) → L3 (Regulator/Partner Verified)

### 5.1 Objective

Cross-reference the pharmacy/PPMV record against official regulatory datasets
(PCN, NAFDAC, state pharmacy councils) or trusted partner datasets (health
facility registries, NHIA provider lists).

### 5.2 Prerequisites

- Record is at L2 with documented location evidence.
- At least one regulator or partner dataset is available for the record's state.

### 5.3 Procedure

| Step | Actor | Action |
|------|-------|--------|
| 1 | System | When a new regulator/partner dataset is imported, trigger a batch cross-reference job. |
| 2 | System | For each L2 record in the dataset's coverage area, run the matching algorithm (name similarity + geo-proximity). |
| 3 | System | Classify matches: `exact_match` (score ≥ 0.90), `probable_match` (0.70–0.89), `no_match` (< 0.70). |
| 4a | System | `exact_match` → auto-approve L3 transition. Log the regulator record ID as evidence. |
| 4b | RA | `probable_match` → manual review. RA compares registry record with regulator record fields. |
| 4c | System | `no_match` → no action. Record remains at L2. |
| 5 | RA | For manual reviews: approve L3 with evidence, or reject with reason. |
| 6 | System | Update `validation_status_history` with the transition and link to regulator source. |

### 5.4 Success Criteria

1. A matching record is found in an official or trusted partner dataset.
2. The match score meets the threshold (≥ 0.90 for auto-approval, ≥ 0.70 for manual review).
3. The regulator record ID, dataset name, and match score are logged as evidence.

### 5.5 Evidence Requirements for L3

| Field | Required | Description |
|-------|----------|-------------|
| `evidence_type` | Yes | `regulator_crossref` |
| `capture_method` | Yes | `batch_crossref` or `manual_crossref` |
| `captured_by` | Yes | `system` or RA's actor ID |
| `captured_at` | Yes | ISO 8601 timestamp (UTC) |
| `regulator_source` | Yes | Name of the regulatory dataset (e.g., `pcn_2025_q4`) |
| `regulator_record_id` | Yes | ID of the matching record in the regulator dataset |
| `match_score` | Yes | Composite match score (0.00–1.00) |
| `match_type` | Yes | `exact_match` or `probable_match` |
| `license_number` | No | PCN/NAFDAC license number if available |
| `license_expiry` | No | License expiry date if available |
| `verification_notes` | No | Notes from manual review |

---

## 6. Verification Quality Controls

### 6.1 Daily Reconciliation

- The system generates a daily summary: attempts made, transitions approved,
  transitions rejected, records flagged.
- Data Stewards review the summary by 10:00 AM the following business day.

### 6.2 Random Audit Sampling

- 5% of L1 transitions are randomly selected for re-verification by a
  different VA within 7 days.
- 3% of L2 transitions are randomly selected for re-visit by a different FV
  within 30 days.
- Discrepancies trigger a full review and potential rollback.

### 6.3 Rollback Procedure

If a transition is found to be erroneous:

1. DS or RA appends a `status_rollback` entry to `validation_status_history`.
2. The record reverts to its previous validation level.
3. The rollback reason and evidence are logged.
4. The original transition evidence is preserved (never deleted) but flagged
   as `superseded`.

### 6.4 Fraud Indicators

Flag for investigation if any of the following are detected:

- Same phone number confirmed for > 5 distinct facilities.
- GPS coordinates for a storefront photo are > 500m from registered address.
- A VA or FV processes > 200% of the team's average daily volume.
- Multiple facilities at the exact same GPS coordinates (unless co-located
  in a market or mall, which must be noted).

---

## 7. Connectivity and Offline Considerations

- The verification app supports offline data capture with sync-on-connect.
- Evidence packages queued offline must sync within 72 hours of capture.
- Offline-captured GPS coordinates are timestamped at capture time, not
  sync time.
- VAs conducting phone verification do not require app connectivity — they
  may log outcomes on paper forms that a DS enters into the system within
  24 hours.

---

## 8. Data Retention

- All evidence records are retained for the lifetime of the registry record.
- Photographs are stored in an evidence storage bucket with access logging.
- Call recordings (if captured) are retained for 12 months, then deleted
  unless flagged for dispute or audit.
- Evidence is NEVER modified after capture — corrections are appended as
  new evidence records with references to the original.
