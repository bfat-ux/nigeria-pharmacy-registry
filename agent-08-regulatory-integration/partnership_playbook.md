# Regulatory Partnership Playbook

## Overview

This playbook defines the engagement strategy for obtaining and integrating
regulatory data from Nigeria's three primary pharmacy oversight bodies.

## Priority Order

| # | Regulator | Priority | Est. Records | Rationale |
|---|-----------|----------|-------------|-----------|
| 1 | PCN | CRITICAL | 25,000 | Sole authority for pharmacy registration |
| 2 | NHIA | High | 8,000 | Accreditation cross-reference |
| 3 | NAFDAC | Medium | 15,000 | Drug retailer licensing, overlaps with PCN |

## PCN (Pharmacists Council of Nigeria)

### Data Format

Expected fields: `premises_name`, `registration_number`, `state`, `lga`,
`facility_category` (community_pharmacy, hospital_pharmacy, ppmv, wholesale),
`registration_date`, `expiry_date`, `registration_status`, `superintendent_pharmacist`,
`phone`, `email`.

### Engagement Protocol

1. Formal data-sharing request via official channels
2. Define data contract: field mapping, refresh cadence, delivery format (CSV)
3. Pilot with one state (e.g., Lagos — highest pharmacy density)
4. Quarterly refresh schedule aligned with PCN registration cycles
5. Escalation path for data quality issues

### Key Constraints

- PCN premises registration number is the authoritative identifier
- No override permitted on PCN ID conflicts
- Data is not openly downloadable — requires partnership agreement
- Registration status (active/expired/suspended/revoked) must be tracked

## NHIA (National Health Insurance Authority)

### Data Format

Expected fields: `facility_name`, `facility_code`, `state`, `lga`,
`facility_type`, `accreditation_status`.

### Engagement Protocol

1. NHIA accreditation data complements PCN registration
2. Accredited facilities are a subset of registered pharmacies
3. Cross-reference NHIA facility codes with existing registry entries
4. Semi-annual refresh aligned with accreditation cycles

## NAFDAC (National Agency for Food, Drug Administration and Control)

### Data Format

Expected fields: `outlet_name`, `license_number`, `state`, `lga`,
`license_type`, `license_status`.

### Engagement Protocol

1. NAFDAC licenses drug retailers, significant overlap with PCN-registered pharmacies
2. License number serves as a secondary regulatory identifier
3. Useful for validating operational status (active license = likely operating)
4. Annual refresh aligned with license renewal cycles

## Data Contract Template

For each regulator partnership, establish:

- **Delivery format**: CSV (UTF-8, comma-delimited)
- **Refresh cadence**: Quarterly (PCN), semi-annual (NHIA), annual (NAFDAC)
- **Minimum fields**: facility name, registration/license ID, state
- **Quality expectations**: < 5% missing names, < 10% missing state
- **Notification**: Email alert when new dataset is available
- **Error handling**: Rejected records reported back with reasons

## Escalation Procedures

| Issue | Severity | Response |
|-------|----------|----------|
| Missing required fields in batch | Low | Skip records, report in batch summary |
| > 20% records fail to match | Medium | Investigate data quality, contact regulator |
| Conflicting registration IDs | High | Flag for Registry Admin manual review |
| Suspected stale data (> 6 months old) | Medium | Request fresh extract before processing |
