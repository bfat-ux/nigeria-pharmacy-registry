# Status & Source Governance

## Source Tier Hierarchy

All data sources are classified into tiers that determine what actions they
can authorize in the registry.

| Tier | Source Type | Example | Can Promote To |
|------|-----------|---------|----------------|
| T1 | Regulator | PCN, NHIA, NAFDAC | L3 (Regulator Verified) |
| T2 | Field | Field agents, site visits | L2 (Evidence Documented) |
| T3 | Partner | NGO datasets, health orgs | L1 (Contact Confirmed) |
| T4 | Community | Crowdsourced, user reports | L0 (Mapped) |
| T5 | System | Automated processes, scrapers | L0 (Mapped) |

### Key Rule: Only T1 sources can promote to L3

The `actor_type = "regulator_sync"` is required for L3 promotion. This is
enforced in `execute_verification()` — the state machine bypass that allows
jumping directly to L3 is gated on this actor type.

## Conflict Resolution

When regulator data conflicts with existing registry data:

### Field Precedence

| Field | Winner | Rationale |
|-------|--------|-----------|
| Registration ID | Regulator (always) | Authoritative source |
| Facility name | Regulator (if T1) | Official registered name |
| State / LGA | Regulator (if T1) | Registration address |
| Phone / Email | Higher tier wins | Most recent confirmed contact |
| GPS coordinates | Field evidence (T2) | Regulators rarely have geocoding |
| Operational status | Most recent evidence | Status changes frequently |

### Conflict Scenarios

1. **Same pharmacy, different regulator IDs across sources**:
   Each regulator ID type is stored independently in `external_identifiers`.
   A pharmacy can have both a PCN premises ID and an NHIA facility code.

2. **Different pharmacies sharing a regulator ID**:
   This indicates a data quality issue. Flag for Registry Admin review.
   The `external_identifiers` unique constraint prevents silent overwrites.

3. **Regulator says facility is active, field evidence says closed**:
   The most recent evidence wins for `operational_status`. The registration
   status (active/expired/suspended) is tracked separately from operational
   status (operating/closed/relocated).

## Provenance Requirements

Every regulator sync operation must log:

1. **Provenance record** — entity_type, entity_id, action, actor (regulator_sync),
   source_system (pcn/nhia/nafdac), source_dataset (batch_id), detail (match scores)

2. **Audit trail** — API request path, actor, timestamp, response status

3. **Validation history** — old_level, new_level, evidence_reference,
   evidence_detail (regulator_details with source, record_id, match_score)

This triple-logging pattern is enforced by routing all promotions through
`execute_verification()`.

## Re-verification Schedule

L3 (Regulator Verified) records expire after **90 days** (quarterly).

When a new regulator batch is imported:
- Records that were previously promoted to L3 from the same regulator source
  get their expiry timer reset if they re-appear in the new batch
- Records that do NOT appear in the new batch retain their current expiry
- After 90 days + 30 day grace period, unrenewed L3 records are downgraded
  to L2 via `POST /api/queue/process-downgrades`

## Regulator Sync API Keys

Regulator sync operations use dedicated admin-tier API keys with:
- `actor_type = "regulator_sync"`
- Full admin scopes for L3 promotion capability
- Logged separately in audit trail for regulatory compliance
