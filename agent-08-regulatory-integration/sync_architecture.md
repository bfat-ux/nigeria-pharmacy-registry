# Regulator Sync Architecture

## Overview

The regulator sync pipeline ingests batch CSV data from PCN, NHIA, and NAFDAC,
matches records against the existing pharmacy registry, and promotes confirmed
matches to L3 (Regulator Verified) through the standard verification ladder.

## Data Flow

```
CSV Upload ──> Parse & Stage ──> Match Pipeline ──> Review/Approve ──> L3 Promotion
                    │                   │                                    │
                    v                   v                                    v
           regulator_staging     composite_scorer              execute_verification()
              _records          (Agent 03 reuse)               + external_identifiers
```

### Step-by-Step Pipeline

1. **Upload** — Admin uploads a CSV file via `POST /api/regulator/upload` with
   `regulator_source` (pcn/nhia/nafdac) and optional `extract_date`.

2. **Parse** — CSV is decoded (UTF-8 with BOM support), columns are normalized
   using flexible mappings per regulator source. File SHA-256 hash prevents
   duplicate uploads.

3. **Stage** — Parsed records are inserted into `regulator_staging_records` with
   `match_status = 'pending'`. Batch metadata tracked in `regulator_sync_batches`.

4. **Match** — For each staged record:
   - **Fast path**: Check `external_identifiers` table for exact registration ID
     match. If found, score = 1.0 (auto_matched).
   - **Composite scoring**: Query pharmacies in the same state (blocking rule),
     run `compute_match()` from Agent 03's composite scorer. Best match classified
     by score threshold.

5. **Classify** — Based on match score:
   | Score Range | Status | Action |
   |-------------|--------|--------|
   | >= 0.90 | `auto_matched` | Eligible for batch approval |
   | 0.70 - 0.89 | `probable_match` | Requires manual review |
   | < 0.70 | `no_match` | Potential new pharmacy |

6. **Approve** — `POST /api/regulator/batches/{id}/approve` promotes all
   auto_matched records to L3 via `execute_verification()`. Each promotion:
   - Inserts validation_status_history entry
   - Logs provenance with actor_type = regulator_sync
   - Logs audit trail
   - Upserts external_identifier with regulator ID

7. **Manual Review** — `POST /api/regulator/batches/{id}/review/{record_id}`
   for probable matches. Reviewer can approve (with optional pharmacy ID override)
   or reject.

## Database Schema

### regulator_sync_batches

Tracks each uploaded CSV batch with aggregate match counts.

| Column | Type | Description |
|--------|------|-------------|
| id | uuid | Primary key |
| regulator_source | enum | pcn, nhia, nafdac |
| file_name | text | Original filename |
| file_hash | text | SHA-256 for idempotency (unique) |
| extract_date | date | When regulator generated the data |
| record_count | integer | Total records in batch |
| auto_matched_count | integer | Score >= 0.90 |
| probable_count | integer | Score 0.70-0.89 |
| no_match_count | integer | Score < 0.70 |
| promoted_count | integer | Successfully promoted to L3 |
| status | text | processing, completed, failed |

### regulator_staging_records

One row per regulator CSV record with match results and promotion tracking.

| Column | Type | Description |
|--------|------|-------------|
| id | uuid | Primary key |
| batch_id | uuid | FK to regulator_sync_batches |
| regulator_source | enum | Source regulator |
| raw_name | text | Facility name from CSV |
| raw_registration_id | text | Regulator ID (PCN premises, NHIA code, NAFDAC license) |
| raw_state / raw_lga | text | Location from CSV |
| raw_data | jsonb | Full original CSV row |
| match_status | enum | pending -> auto_matched/probable_match/no_match -> promoted/rejected |
| matched_pharmacy_id | uuid | Best matching pharmacy |
| match_score | real | Composite match confidence 0.0-1.0 |
| match_details | jsonb | Detailed scoring breakdown |

## Matching Strategy

### Signal Weights (from Agent 03 composite scorer)

| Signal | Default Weight | Notes |
|--------|---------------|-------|
| Name similarity | 0.40 | Fuzzy matching with Nigerian pharmacy name normalization |
| Geo proximity | 0.25 | Often NULL for regulator data — weight redistributed |
| Phone matching | 0.20 | Exact normalized Nigerian phone comparison |
| External ID overlap | 0.15 | Override: exact regulator ID = 1.0 auto-merge |

When geo data is missing (typical for PCN/NHIA/NAFDAC), its weight is
redistributed proportionally among available signals.

### Performance

- **Blocking rule**: Same-state filter reduces comparison space
- State cache prevents redundant pharmacy queries within a batch
- Batch insert via `execute_batch()` for staging
- 25K PCN records across 37 states = ~675 per state avg, each compared against
  ~186 registry pharmacies per state = manageable in pure Python with rapidfuzz

## Batch Size Limits

Default 5,000 records per upload, max 25,000. Designed for low-bandwidth
environments where large uploads may time out.
