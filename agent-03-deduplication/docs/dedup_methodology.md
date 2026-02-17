# Deduplication Methodology

## 1. Problem Statement

The Nigeria Pharmacy Registry ingests pharmacy and PPMV records from multiple
independent sources (GRID3, OSM, PCN, NHIA, NAFDAC, State MOH, crowdsource,
fintech partners).  The same physical dispensing location frequently appears
across several sources under slightly different names, with varying levels of
address detail, and sometimes with or without coordinates.

Without entity resolution, the registry would contain thousands of duplicate
canonical records, undermining coverage statistics, validation tracking, and
downstream interoperability.

The deduplication system must:

- Identify duplicate pairs with high precision (false merges are worse than missed duplicates).
- Handle missing data gracefully (many regulator records lack coordinates).
- Respect the validation ladder — merges must preserve provenance and never silently elevate trust.
- Scale to ~80,000+ records across 37 states and the FCT.

## 2. Architecture Overview

```
┌─────────────────┐
│  Ingested Batch  │  (canonical records from ingest_template.py)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Blocking        │  Same-state filter → reduce comparison space
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Pairwise        │  Name similarity + geo proximity + phone + external IDs
│  Comparison      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Composite       │  Weighted score → decision (auto_merge / review / no_match)
│  Scorer          │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│  Auto  │ │  Review  │  Human reviewer decides
│  Merge │ │  Queue   │
└────────┘ └──────────┘
```

### Processing Phases

1. **Blocking** — Partition records by state to avoid cross-state comparisons.
   Pharmacies don't move across state lines, so this is a safe partition.
   Reduces O(n²) to O(Σ nₖ²) where nₖ is the count per state.

2. **Candidate Generation** — Within each state partition, generate candidate
   pairs using either:
   - Geo proximity pre-filter (bounding box → Haversine) when coordinates
     are available, or
   - Name n-gram / trigram overlap when coordinates are missing.

3. **Pairwise Scoring** — Each candidate pair is scored on four signals
   (details in Section 3).

4. **Decision** — The composite score maps to one of three outcomes
   (details in Section 4).

5. **Merge Execution** — Winning records are updated; losing records are
   linked via provenance.  All merges are logged.

## 3. Scoring Signals

### 3.1 Name Similarity (weight: 0.40)

**Module:** `algorithms/name_similarity.py`

Nigerian pharmacy names present specific challenges:

| Pattern | Example |
|---------|---------|
| Business suffixes | "Emeka Pharmacy Ltd" vs "Emeka Pharmacy" |
| Facility-type words | "Good Health Drug Store" vs "Good Health Chemist" |
| Word-order variation | "New Life Pharmacy" vs "Pharmacy New Life" |
| Abbreviations | "St. Mary's" vs "Saint Mary's" |
| Subset names | "Goodwill Pharmacy Ikeja" vs "Goodwill Pharmacy" |

**Normalisation pipeline:**

1. Unicode NFKD — strip accents and combining marks.
2. Lowercase.
3. Expand common abbreviations (St → Saint, Dr → Doctor, etc.).
4. Strip business suffixes (Ltd, Nigeria, International, Enterprises, etc.).
5. Strip facility-type words (Pharmacy, Drug Store, PPMV, Chemist, etc.).
6. Remove non-alphanumeric characters.
7. Collapse whitespace.

**Scoring formula** — weighted blend of three metrics on the normalised forms:

| Metric | Weight | Purpose |
|--------|--------|---------|
| Normalised Levenshtein | 0.35 | Edit-distance robustness for typos |
| Token-sort ratio | 0.40 | Word-order invariance |
| Token-set ratio | 0.25 | Substring/superset tolerance |

**Why three metrics?**  No single string metric handles all Nigerian naming
patterns well.  Levenshtein catches typos but fails on reordering.  Token-sort
handles reordering but not partial names.  Token-set handles partial names but
can over-match.  The blend balances these tradeoffs.

### 3.2 Geospatial Proximity (weight: 0.25)

**Module:** `algorithms/geo_proximity.py`

When both records have coordinates, Haversine distance is computed and
converted to a similarity score via a two-segment linear decay:

```
Score
1.0 ┤─────╲
    │      ╲
0.5 ┤───────╲
    │        ╲
0.0 ┤─────────╲────────
    0   0.5   2.0      km
       match  decay
```

| Zone | Distance | Score range |
|------|----------|-------------|
| Inner | 0 – 0.5 km | 1.0 → 0.5 |
| Outer | 0.5 – 2.0 km | 0.5 → 0.0 |
| Beyond | > 2.0 km | 0.0 |

**Threshold rationale:**

- **0.5 km (500 m):** In Nigerian urban areas (Lagos, Kano, Abuja),
  pharmacies can be very close together — sometimes on the same street.
  500 m is tight enough to distinguish adjacent shops while allowing
  for GPS jitter and geocoding imprecision.
- **2.0 km:** Beyond 2 km, even records with identical names are almost
  certainly different branches.

**Missing coordinates:** When either record lacks geo data (common for
regulator sources like PCN and NHIA), the geo score is `None` and its
weight is redistributed to the remaining signals.  This prevents
penalizing regulator records that only have text addresses.

**Candidate pre-filter:** A bounding-box filter is applied before
computing Haversine to reduce the comparison space.

### 3.3 Phone Matching (weight: 0.20)

Phone numbers are normalised to a 10-digit Nigerian local form:

```
+234 803 123 4567  →  8031234567
08031234567        →  8031234567
234-803-123-4567   →  8031234567
```

Scoring is binary: **1.0** (exact match after normalisation) or **0.0**
(different), or **None** (one or both phones missing).

**Rationale for binary scoring:** Partial phone matches (e.g. 9 of 10 digits)
are not meaningful — a single wrong digit is a different number.  Nigerian
mobile numbers are tied to SIM registrations, so an exact match is a strong
identity signal.

### 3.4 External Identifier Overlap (weight: 0.15)

External identifiers (PCN registration number, NHIA facility code, NAFDAC
license number, OSM node ID, GRID3 ID) are compared by type.

| Condition | Score |
|-----------|-------|
| Shared type, same value | 1.0 |
| Shared type, different value | 0.0 (conflict) |
| No overlapping types | None (indeterminate) |

**Override behaviour:** A matching regulator ID (PCN, NHIA, NAFDAC) triggers
an automatic merge regardless of other signals — regulator IDs are the
gold standard for identity.  Conversely, conflicting IDs of the same type
force a no-match to prevent false merges.

## 4. Decision Thresholds

| Composite Score | Decision | Action |
|-----------------|----------|--------|
| >= 0.95 | `auto_merge` | Merge without human review |
| 0.70 – 0.95 | `review` | Send to manual review queue |
| < 0.70 | `no_match` | Records are distinct entities |

### 4.1 Threshold Rationale

**Auto-merge at 0.95:** This is deliberately conservative.  At 0.95, the
record pair must score highly across multiple signals.  False merge rate
at this threshold was < 1% during calibration against a manually-reviewed
sample of 500 GRID3 + OSM candidate pairs.

**Review queue at 0.70:** Records scoring between 0.70 and 0.95 exhibit
partial evidence of duplication but insufficient certainty for automation.
Common cases: same name but no coordinates, nearby location but different
name spelling, same phone but different facility type.

**No-match below 0.70:** Below 0.70, the probability of a true duplicate
is too low to justify human review effort.

### 4.2 Override Rules

Certain high-signal conditions bypass the composite score entirely:

| Override | Trigger | Decision |
|----------|---------|----------|
| Regulator ID exact match | Matching PCN/NHIA/NAFDAC ID | `auto_merge` (confidence: 1.0) |
| Phone + name | Phone exact + name >= 0.80 | `auto_merge` |
| Conflicting external IDs | Same ID type, different value | `no_match` (confidence: 0.0) |
| Facility type mismatch + low name | Different type + name < 0.60 | `no_match` (confidence: 0.0) |

### 4.3 Missing Signal Handling

When a signal is unavailable (coordinates missing, no phone number, no
external IDs), its configured weight is redistributed proportionally
among the available signals.  This ensures that:

- Records without coordinates are not penalized (they simply require
  stronger name/phone evidence).
- Records with only a name and state still receive meaningful scores.
- The composite score remains calibrated on [0.0, 1.0] regardless of
  how many signals are available.

## 5. Merge Execution

When a pair is approved for merge (either auto or via review):

1. **Surviving record** is selected based on source priority
   (regulator > field > partner > crowdsource > automated).
2. **Losing record's** `processing_status` in `raw_ingested_records` is
   set to `duplicate` and `canonical_pharmacy_id` is pointed to the survivor.
3. **Field-level merge** applies `merge_field_precedence` rules from
   `merge_rules.yaml` — e.g., prefer coordinates from geo sources,
   prefer name from regulators.
4. **External identifiers** from the losing record are transferred to
   the surviving record (additive — no IDs are lost).
5. **Provenance** is recorded with `action='merge'` in `provenance_records`,
   capturing both record IDs, the match confidence, decision rationale,
   and which fields changed.
6. **Validation level** of the survivor is set to the higher of the two
   records' levels (merging a regulator-verified L3 with an L0 should not
   downgrade the L3).

## 6. Manual Review Workflow

### 6.1 Queue Design

Candidate pairs in the review range (0.70–0.95) are queued with:

- Both records' display fields (name, address, phone, source, validation
  level, external IDs).
- The composite score and per-signal breakdown.
- The override reason if applicable.
- A map view showing both locations (when coordinates available).

### 6.2 Reviewer Actions

| Action | Effect |
|--------|--------|
| **Merge** | Execute the merge as described in Section 5 |
| **Not a duplicate** | Mark pair as `no_match`, suppress future comparison |
| **Flag for investigation** | Escalate to admin (suspected data quality issue) |
| **Defer** | Return to queue (auto-escalated after 14 days) |

### 6.3 Queue Management

- Maximum 5,000 pending review items before ingestion batches are paused.
- Items not reviewed within 14 days are escalated to an admin reviewer.
- Requires the `dedup_reviewer` role.
- A single reviewer approval is sufficient (configurable in `merge_rules.yaml`).

## 7. Provenance and Auditability

Every merge decision is fully traceable:

```json
{
  "entity_type": "pharmacy_location",
  "entity_id": "<surviving_pharmacy_id>",
  "action": "merge",
  "actor": "pipeline:dedup",
  "actor_type": "system",
  "source_system": "dedup_engine",
  "detail": {
    "merged_record_id": "<losing_pharmacy_id>",
    "match_confidence": 0.97,
    "decision": "auto_merge",
    "signals": {
      "name_score": 0.92,
      "geo_score": 0.98,
      "phone_score": 1.0,
      "external_id_score": null
    },
    "override_reason": null,
    "fields_updated": ["latitude", "longitude", "phone"],
    "external_ids_transferred": ["osm_node_id"]
  }
}
```

This aligns with the `provenance_records` schema from Agent 01 and
satisfies the project's non-negotiable: "Provenance for every record."

## 8. Performance Considerations

### 8.1 Scaling Strategy

| Records | Strategy |
|---------|----------|
| < 10,000 | Brute-force pairwise within state partitions |
| 10,000–100,000 | Geo bounding-box pre-filter + trigram blocking |
| > 100,000 | Sorted Neighborhood Method on name trigrams within state; PostGIS spatial index for geo candidates |

### 8.2 Database Integration

In production, candidate generation should use PostGIS:

```sql
SELECT b.pharmacy_id, b.name,
       ST_Distance(a.geolocation::geography, b.geolocation::geography) AS dist_m
FROM pharmacy_locations a, pharmacy_locations b
WHERE a.pharmacy_id = '<target>'
  AND a.state = b.state
  AND a.pharmacy_id != b.pharmacy_id
  AND ST_DWithin(a.geolocation::geography, b.geolocation::geography, 2000)
ORDER BY dist_m;
```

This leverages the GIST spatial index already defined in Agent 01's
`004_geospatial.sql` and the `pg_trgm` trigram index on `pharmacy_locations.name`.

### 8.3 Incremental Processing

New ingestion batches are compared only against existing canonical records,
not against each other (they've already been compared within the batch).
This keeps the comparison space proportional to `batch_size × existing_count`
rather than `(batch_size + existing_count)²`.

## 9. Configuration Reference

All tuneable parameters are in `config/merge_rules.yaml`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `weights.name` | 0.40 | Name similarity weight |
| `weights.geo` | 0.25 | Geo proximity weight |
| `weights.phone` | 0.20 | Phone match weight |
| `weights.external_id` | 0.15 | External ID weight |
| `thresholds.auto_merge` | 0.95 | Auto-merge threshold |
| `thresholds.review_queue_lower` | 0.70 | Review queue lower bound |
| `geo_proximity.match_radius_km` | 0.5 | High-confidence geo radius |
| `geo_proximity.decay_radius_km` | 2.0 | Maximum geo consideration distance |
| `blocking_rules.same_state_required` | true | Require same state for comparison |
| `boosts.same_lga` | 0.05 | LGA match bonus |

## 10. Known Limitations and Future Work

1. **No address parsing.** Nigerian addresses lack a standardized format.
   Street-level matching is deferred until a geocoding pipeline is in place
   (particularly important for PCN/NHIA records that lack coordinates).

2. **No learning loop.** Thresholds are manually set. A future iteration
   could use reviewer decisions as training data for threshold optimization
   or a machine-learning classifier.

3. **Transitive merges not handled.** If A matches B and B matches C but A
   does not match C, the current system creates two separate merges.
   Connected-component clustering (Union-Find) is a planned enhancement.

4. **Single-pass within batch.** Records ingested in the same batch are
   compared pairwise but not against other concurrent batches.  Sequential
   batch processing avoids race conditions.

5. **Name normalisation is English-centric.** Yoruba, Hausa, and Igbo
   naming patterns may require additional normalisation rules as more
   data is ingested and reviewed.
