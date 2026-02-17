# Dispute and Correction Workflow

> Nigeria Pharmacy Registry — Agent 04
> Version: 1.0
> Last updated: 2026-02-17

## 1. Purpose

Pharmacies and PPMVs listed in the registry have the right to review, dispute,
and request corrections to their records. This document defines the process for
submitting disputes, the investigation and resolution workflow, and the
escalation paths for unresolved cases.

**Principles:**
- Disputes are treated as first-class verification events — they generate
  evidence records and audit trails like any other status change.
- The burden of proof varies by dispute type: factual corrections (e.g., wrong
  address) require minimal evidence; status disputes (e.g., "we are licensed")
  require supporting documentation.
- No record is deleted from the registry. Disputed records may be corrected,
  re-classified, or marked inactive, but the original data and full history
  are always preserved.

---

## 2. Dispute Channels

Facilities may submit disputes through any of the following channels:

| Channel | Description | Response SLA |
|---------|-------------|-------------|
| **Registry Web Portal** | Online form with structured fields and document upload | Acknowledge within 2 business days |
| **Phone Hotline** | Dedicated number; agent logs the dispute in the system | Acknowledge during the call |
| **Email** | Sent to registry-disputes@[domain] with details | Acknowledge within 3 business days |
| **Field Verifier** | Raised during a field visit; FV logs on behalf of facility | Logged immediately |
| **Partner Referral** | State pharmacy council or PCN forwards a dispute | Acknowledge within 2 business days |

All disputes, regardless of channel, are entered into the system as a
**dispute record** with a unique dispute ID (UUID v4).

---

## 3. Dispute Types

| Type Code | Label | Description | Example |
|-----------|-------|-------------|---------|
| `factual_correction` | Factual Correction | Incorrect name, address, phone, or other factual field | "Our pharmacy name is spelled wrong" |
| `status_dispute` | Status Dispute | Disagreement with current validation level or operating status | "We are licensed by PCN but show as L0" |
| `duplicate_report` | Duplicate Report | Facility appears multiple times in the registry | "We are listed twice under different names" |
| `closure_report` | Closure/Relocation Report | Facility has closed or moved to a new address | "We relocated to a new address last month" |
| `ownership_dispute` | Ownership Dispute | Dispute over who controls or represents the facility record | "The listed contact is no longer associated with us" |
| `removal_request` | Removal Request | Request to be removed from the registry | "We do not want to be listed" |
| `verification_challenge` | Verification Challenge | Challenge to a specific verification finding | "The verifier recorded incorrect GPS coordinates" |

---

## 4. Dispute Record Schema

Every dispute generates a record with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `dispute_id` | UUID | Yes | Unique dispute identifier |
| `facility_id` | UUID | Yes | The registry record being disputed |
| `dispute_type` | enum | Yes | One of the type codes above |
| `submitted_by_name` | string | Yes | Name of the person submitting |
| `submitted_by_role` | string | Yes | Relationship to the facility (owner, pharmacist, staff, other) |
| `submitted_by_contact` | string | Yes | Phone or email for follow-up |
| `submitted_at` | datetime | Yes | ISO 8601 timestamp (UTC) |
| `submission_channel` | enum | Yes | `web_portal`, `phone`, `email`, `field_visit`, `partner_referral` |
| `description` | text | Yes | Free-text description of the dispute (max 5000 chars) |
| `requested_change` | text | Yes | What the submitter wants corrected |
| `supporting_documents` | array[URI] | No | URIs to uploaded evidence (photos, license copies, etc.) |
| `status` | enum | Yes | `submitted`, `under_review`, `additional_info_requested`, `resolved_accepted`, `resolved_rejected`, `escalated`, `withdrawn` |
| `assigned_to` | UUID | No | Actor ID of the assigned reviewer |
| `resolution_notes` | text | No | Explanation of the resolution decision |
| `resolved_at` | datetime | No | Timestamp when the dispute was resolved |
| `created_at` | datetime | Yes | System insertion timestamp |
| `created_by` | string | Yes | Actor who entered the record |

---

## 5. Dispute Resolution Workflow

### 5.1 Workflow Diagram

```
[Facility submits dispute]
        │
        ▼
[System: Create dispute record, assign dispute_id]
        │
        ▼
[System: Acknowledge receipt to submitter]
        │
        ▼
[System: Route to appropriate reviewer based on dispute_type]
        │
        ├─── factual_correction ──────► Data Steward
        ├─── status_dispute ──────────► Registry Admin
        ├─── duplicate_report ─────────► Data Steward
        ├─── closure_report ──────────► Verification Agent
        ├─── ownership_dispute ────────► Registry Admin
        ├─── removal_request ─────────► Registry Admin
        └─── verification_challenge ──► Data Steward + original verifier excluded
                │
                ▼
        [Reviewer: Investigate]
                │
        ┌───────┴───────┐
        ▼               ▼
[Need more info?]   [Sufficient evidence]
        │                   │
        ▼                   ▼
[Request additional    [Make decision]
 info from submitter]       │
        │             ┌─────┴─────┐
        │             ▼           ▼
        │        [Accept]    [Reject]
        │             │           │
        │             ▼           ▼
        │     [Apply changes] [Notify submitter
        │     [Log evidence]   with reason]
        │             │           │
        └─────────────┴───────────┘
                      │
                      ▼
              [Close dispute]
```

### 5.2 Step-by-Step Procedure

| Step | Actor | Action | SLA |
|------|-------|--------|-----|
| 1 | Facility | Submits dispute via any channel. | — |
| 2 | System | Creates dispute record, assigns `dispute_id`, sets status to `submitted`. | Immediate |
| 3 | System | Sends acknowledgment to submitter with `dispute_id` for tracking. | Within acknowledgment SLA for channel |
| 4 | System | Routes dispute to the appropriate reviewer based on `dispute_type`. | Within 1 business day |
| 5 | Reviewer | Sets status to `under_review`. Reviews the dispute, the current registry record, and all associated evidence. | Begin review within 3 business days |
| 6a | Reviewer | If additional information is needed: sets status to `additional_info_requested`, contacts the submitter. | — |
| 6b | Submitter | Provides additional information or documents within 14 days. | 14 days |
| 6c | System | If no response after 14 days: send reminder. If no response after 28 days: close as `withdrawn`. | — |
| 7 | Reviewer | Makes a resolution decision: accept or reject. | Resolve within 10 business days of having sufficient information |
| 8a | Reviewer (Accept) | Applies corrections to the registry. Creates an evidence record of type `dispute_evidence`. Updates `validation_status_history` if level changed. Sets dispute status to `resolved_accepted`. | — |
| 8b | Reviewer (Reject) | Documents the rejection reason in `resolution_notes`. Sets dispute status to `resolved_rejected`. | — |
| 9 | System | Notifies the submitter of the resolution outcome via their preferred channel. Includes escalation instructions if rejected. | Within 1 business day of resolution |

---

## 6. Resolution Guidelines by Dispute Type

### 6.1 Factual Correction

- **Standard of proof:** Submitter's statement is sufficient for minor corrections
  (spelling, phone number). Address changes require supporting evidence (utility
  bill, lease, or photo of new signage).
- **Reviewer:** Data Steward.
- **If accepted:** Update the canonical record. Append provenance entry with
  `source: dispute_resolution`. No level change unless the correction invalidates
  existing evidence.

### 6.2 Status Dispute

- **Standard of proof:** Submitter must provide documentary evidence supporting
  the claimed status (e.g., valid PCN license copy for L3 claim).
- **Reviewer:** Registry Admin.
- **If accepted:** Create new evidence record and transition to the supported
  level. Follow normal transition procedures.
- **If partially accepted:** e.g., license is valid but for a different facility
  type — document and explain.

### 6.3 Duplicate Report

- **Standard of proof:** Submitter identifies the duplicate records.
- **Reviewer:** Data Steward.
- **Process:** Run the deduplication algorithm (Agent 03) against the flagged
  records. If confirmed duplicate, merge following `merge_rules.yaml`. Retain
  the record with the higher validation level as canonical.

### 6.4 Closure/Relocation Report

- **Standard of proof:** Submitter's statement. May trigger a field visit to
  confirm if the report is third-party.
- **Reviewer:** Verification Agent.
- **If closure confirmed:** Set operating status to `closed` with effective date.
  Record remains in registry at its current level but is flagged as inactive.
- **If relocation confirmed:** Create a new record at L0 for the new address.
  Link it to the old record. Old record is marked `relocated` with a reference
  to the new record ID.

### 6.5 Ownership Dispute

- **Standard of proof:** Both parties may need to submit evidence. This is the
  most complex dispute type.
- **Reviewer:** Registry Admin.
- **Process:** Contact both the current listed contact and the disputant.
  Request business registration documents, PCN license, or other ownership
  evidence. If unresolvable, escalate to a senior Registry Admin or refer to
  external mediation (e.g., state pharmacy council).
- **Interim:** The record is frozen at its current level until resolved. A
  `dispute_pending` flag is set.

### 6.6 Removal Request

- **Reviewer:** Registry Admin.
- **Policy:** The registry is a public-interest directory. Legitimate, operating,
  licensed pharmacies cannot be removed. However:
  - If the facility has permanently closed → mark as inactive.
  - If the facility was listed in error (e.g., not a pharmacy) → reclassify
    or mark as `invalid_listing` with reason.
  - If the request cites data protection concerns → review under NDPA
    compliance guidelines (Agent 06) and respond accordingly.
- **The record is never deleted.** It may be marked inactive or unlisted from
  public queries, but the data and audit trail are retained.

### 6.7 Verification Challenge

- **Standard of proof:** Submitter must explain what is incorrect in the
  verification finding.
- **Reviewer:** Data Steward. The original verifier is EXCLUDED from the review
  to avoid bias.
- **Process:** Review the original evidence. If the challenge is about GPS
  coordinates or photos, a new field visit by a different FV may be ordered.
  If the challenge is about a regulator cross-reference, re-run the match
  manually.

---

## 7. Escalation Path

If a dispute is rejected and the submitter disagrees with the outcome:

| Escalation Level | Who | Timeframe | Action |
|-----------------|-----|-----------|--------|
| **Level 1: Initial Review** | Data Steward or Registry Admin | Standard workflow | As described above |
| **Level 2: Senior Review** | Senior Registry Admin | Within 5 business days of escalation request | Re-reviews all evidence and the initial decision independently |
| **Level 3: External Referral** | State Pharmacy Council or PCN | Within 15 business days | For disputes involving licensing status or regulatory standing |
| **Level 4: Formal Complaint** | Registry Governance Board | Within 30 business days | For unresolved disputes after Level 3 |

### 7.1 Escalation Procedure

1. Submitter contacts the registry (any channel) and references their
   `dispute_id`, stating they wish to escalate.
2. System creates an escalation record linked to the original dispute.
3. The escalation is routed to the next level reviewer, who has access to the
   full dispute history.
4. The escalation reviewer's decision is final at their level. Further
   escalation follows the same pattern.

---

## 8. Dispute Metrics and Reporting

### 8.1 Tracked Metrics

| Metric | Target |
|--------|--------|
| Acknowledgment within SLA | ≥ 95% |
| Resolution within 10 business days | ≥ 80% |
| Disputes requiring escalation | ≤ 10% |
| Submitter satisfaction (post-resolution survey) | ≥ 70% positive |

### 8.2 Monthly Dispute Report

Generated on the 1st of each month:

- Total disputes received by type and channel.
- Resolution outcomes: accepted, rejected, withdrawn.
- Average resolution time by type.
- Escalation rate.
- State-level breakdown.
- Systemic issues identified (e.g., repeated disputes about a specific
  data source suggesting ingestion quality problems).

---

## 9. Submitter Communication Templates

### 9.1 Acknowledgment

> Dear [Name],
>
> We have received your dispute regarding [Facility Name] (Dispute ID:
> [dispute_id]). Our team will review your submission and respond within
> 10 business days.
>
> You can check the status of your dispute at any time by visiting
> [portal URL] or calling [hotline number] with your Dispute ID.
>
> Regards,
> Nigeria Pharmacy Registry — Dispute Resolution Team

### 9.2 Additional Information Request

> Dear [Name],
>
> Thank you for your dispute submission (Dispute ID: [dispute_id]).
>
> To complete our review, we need the following additional information:
>
> [List of requested items]
>
> Please respond within 14 days. If we do not hear from you within 28 days,
> the dispute will be closed.
>
> Regards,
> Nigeria Pharmacy Registry — Dispute Resolution Team

### 9.3 Resolution — Accepted

> Dear [Name],
>
> Your dispute (Dispute ID: [dispute_id]) regarding [Facility Name] has
> been reviewed and accepted.
>
> The following changes have been made to your registry record:
>
> [Summary of changes]
>
> If you have further questions, please contact us at [hotline/email].
>
> Regards,
> Nigeria Pharmacy Registry — Dispute Resolution Team

### 9.4 Resolution — Rejected

> Dear [Name],
>
> Your dispute (Dispute ID: [dispute_id]) regarding [Facility Name] has
> been reviewed. After careful consideration, we are unable to make the
> requested change for the following reason:
>
> [Reason]
>
> If you disagree with this decision, you may request an escalation review
> by contacting us at [hotline/email] and referencing your Dispute ID.
>
> Regards,
> Nigeria Pharmacy Registry — Dispute Resolution Team
