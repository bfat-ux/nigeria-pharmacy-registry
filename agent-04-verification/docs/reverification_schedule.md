# Re-verification Schedule

> Nigeria Pharmacy Registry — Agent 04
> Version: 1.0
> Last updated: 2026-02-17

## 1. Purpose

Verification is not a one-time event. Pharmacies close, relocate, change
ownership, and let licenses lapse. This document defines the cadence for
scheduled re-verification and the triggers for out-of-cycle re-verification
at each validation level.

**Principle:** A record's validation level represents current confidence, not
historical achievement. If re-verification fails, the record moves DOWN the
ladder.

---

## 2. Scheduled Re-verification Cadence

| Current Level | Re-verification Interval | Method | Responsible Role |
|---------------|--------------------------|--------|------------------|
| L1 (Contact Confirmed) | **12 months** from last L1 evidence date | Outbound phone call or email | Verification Agent |
| L2 (Evidence Documented) | **18 months** from last L2 evidence date | Field visit or remote confirmation | Field Verifier |
| L3 (Regulator Verified) | **On regulator dataset sync** (typically quarterly) | Automated batch cross-reference | System + Registry Admin |

### 2.1 L1 Re-verification Details

- The system generates a monthly queue of L1 records approaching their
  12-month anniversary (30-day advance window).
- VAs follow the same phone/email procedure as the original L0→L1 transition
  (see `verification_sop.md`, Section 3).
- **Pass:** Contact confirmed → new L1 evidence record appended, timer resets.
- **Fail (3 attempts):** Record flagged for Data Steward review.
  - If contact info updated and re-confirmed → timer resets.
  - If unreachable after updated attempts → record downgrades to L0 with
    `reverification_failed` reason.

### 2.2 L2 Re-verification Details

- The system generates a monthly queue of L2 records approaching their
  18-month anniversary (45-day advance window).
- For urban areas with high facility density, re-verification may be
  conducted via remote methods:
  - Request a timestamped photo from the facility contact (via WhatsApp
    or the registry portal) showing the current storefront.
  - Verify GPS metadata on the submitted photo.
- For rural areas or flagged records, a physical field visit is required.
- **Pass:** Location confirmed → new L2 evidence record appended, timer resets.
- **Fail:** Record downgrades to L1 (contact may still be valid) with
  `location_reverification_failed` reason.

### 2.3 L3 Re-verification Details

- Triggered automatically when a new regulator/partner dataset is imported.
- The system re-runs the cross-reference matching for all L3 records in the
  dataset's coverage area.
- **Still matched:** Evidence record updated with new dataset version. No
  status change.
- **No longer matched:** Record flagged for Registry Admin review.
  - RA investigates: license expired? Data entry error in regulator dataset?
    Facility delisted?
  - If legitimate removal → record downgrades to L2 with
    `regulator_match_lost` reason.
  - If data error → RA documents the discrepancy and retains L3.

---

## 3. Out-of-Cycle Re-verification Triggers

These events trigger immediate re-verification regardless of the scheduled
cadence.

### 3.1 Automatic Triggers (System-Initiated)

| Trigger | Action | Target Level |
|---------|--------|--------------|
| **Complaint received** via registry portal or hotline | Queue for VA contact within 48 hours | Re-verify at current level |
| **Regulator alert** — facility flagged in regulatory action (suspension, sanction) | Immediate RA review; freeze record at current level pending investigation | L3 → re-assess |
| **Duplicate detected** by deduplication pipeline | Data Steward review to confirm identity; may require field visit | Re-verify at current level |
| **Batch GPS anomaly** — registered coordinates fall outside the declared LGA | Queue for field re-verification | L2 |
| **Contact info change** submitted by facility | Re-verify contact at L1 before updating canonical record | L1 |
| **Ownership change** reported or detected | Full re-verification from L1 | L1, L2 |

### 3.2 Manual Triggers (Human-Initiated)

| Trigger | Initiated By | Action |
|---------|-------------|--------|
| **Data Steward flag** during routine audit | DS | Queue for re-verification at the flagged level |
| **Partner report** — trusted partner (e.g., state pharmacy council) reports discrepancy | RA | Investigate and re-verify as needed |
| **Media or public report** — news of pharmacy closure, relocation, or regulatory action | RA | Investigate and re-verify from L1 |
| **Random audit sample** — selected during quality control | System + DS | Re-verify at current level with different verifier |

---

## 4. Downgrade Rules

When re-verification fails, the record moves DOWN the ladder. Downgrades
follow strict rules to maintain data integrity.

### 4.1 Downgrade Matrix

| Current Level | Failure Type | Downgrade To | Reason Code |
|---------------|-------------|--------------|-------------|
| L1 | Contact unreachable (3 attempts) | L0 | `contact_reverification_failed` |
| L1 | Facility reported closed | L0 | `facility_closed` |
| L2 | Location not confirmed at address | L1 | `location_reverification_failed` |
| L2 | Facility relocated (new address) | L0 | `facility_relocated` |
| L3 | No longer in regulator dataset | L2 | `regulator_match_lost` |
| L3 | License expired and not renewed | L2 | `license_expired` |
| Any | Fraud detected | L0 | `fraud_detected` |

### 4.2 Downgrade Procedure

1. The actor (VA, FV, System, or RA) creates a new evidence record with
   `evidence_type: reverification` and `outcome: denied`.
2. A new row is appended to `validation_status_history` recording the
   downgrade, reason code, and evidence reference.
3. The facility contact is notified (if contact info is available) with
   instructions on how to request re-verification or file a dispute.
4. Downgraded records are eligible for immediate re-verification if new
   evidence is submitted.

---

## 5. Re-verification Scheduling Logic

### 5.1 Queue Generation

The system runs a daily job to generate re-verification queues:

```
FOR each record in pharmacy_locations:
  days_since_last_evidence = NOW() - last_evidence_date_for_current_level
  advance_window = get_advance_window(current_level)
  interval = get_interval(current_level)

  IF days_since_last_evidence >= (interval - advance_window):
    ADD to re-verification queue for current_level
    PRIORITY = days_since_last_evidence / interval  -- higher = more overdue
```

### 5.2 Priority Factors

Within each queue, records are prioritized by:

1. **Overdue ratio** — how far past the re-verification window.
2. **State coverage targets** — states with fewer verified records get priority.
3. **Risk flags** — records with complaints or anomalies are prioritized.
4. **Facility type** — pharmacies (higher risk) before PPMVs.

### 5.3 Capacity Planning

| Role | Expected daily capacity | Notes |
|------|------------------------|-------|
| VA (phone) | 40–60 contact attempts | Depends on answer rates |
| FV (field) | 8–15 facility visits | Depends on geographic density |
| DS (review) | 80–120 evidence reviews | Includes flagged records |

---

## 6. Grace Periods

To avoid unnecessary churn, grace periods apply before downgrades take effect:

| Level | Grace Period After Failed Re-verification | During Grace Period |
|-------|------------------------------------------|---------------------|
| L1 | 30 days | Record remains L1 but flagged as "re-verification pending" |
| L2 | 45 days | Record remains L2 but flagged as "re-verification pending" |
| L3 | 14 days (after RA review) | Record remains L3 pending RA decision |

During the grace period:
- The record is still queryable via the API but carries a `reverification_pending` flag.
- The facility is notified and given an opportunity to respond.
- If new evidence is submitted and accepted, the flag is cleared and the
  timer resets.
- If the grace period expires without resolution, the downgrade takes effect.

---

## 7. Reporting

### 7.1 Monthly Re-verification Report

Generated on the 1st of each month, containing:

- Total records due for re-verification by level.
- Records re-verified successfully vs. failed vs. pending.
- Downgrades executed by reason code.
- Average time from queue entry to re-verification completion.
- State-level breakdown of re-verification status.

### 7.2 Annual Verification Health Report

Generated annually, containing:

- Percentage of records at each validation level over time (trend).
- Re-verification pass/fail rates by level and state.
- Average record lifetime at each level.
- Identified systemic issues (e.g., high failure rates in specific LGAs).
- Recommendations for cadence adjustments.
