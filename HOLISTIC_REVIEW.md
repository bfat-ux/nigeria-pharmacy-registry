# Holistic Review — Nigeria Pharmacy Registry

## Executive Summary

The repository has strong design intent and solid foundational specifications across schema, ingestion, verification, API design, and policy/risk. The most important gap is **cross-workstream consistency**: several artifacts describe a complete system, but a few key interfaces and inventory assumptions are currently misaligned (especially canonical enums and agent deliverable completeness).

Overall maturity appears to be **advanced design / pre-implementation**, with good documentation depth and clear governance principles, but with integration hardening still required before production build-out.

## What Is Working Well

1. **Clear governance posture**
   - The project consistently emphasizes provenance, append-only validation history, and no patient data.
   - This is reflected in both high-level docs and SQL model design.

2. **Sound data architecture baseline**
   - Core tables, status history, provenance, and geospatial support are documented in a way that supports future scale and interoperability.

3. **Thoughtful risk/compliance integration**
   - NDPA and misuse/threat considerations are not isolated; they are linked to API and workflow design.

4. **Good modular decomposition by workstream**
   - Agent-oriented structure makes ownership and sequencing explicit.

## Critical Cross-Artifact Gaps

### 1) Canonical enum drift (highest priority)

There are conflicting definitions of core domain enums across SQL, ingestion, and API specs. Examples:
- SQL `facility_type` uses `pharmacy`, `ppmv`, `hospital_pharmacy`.
- OpenAPI uses `community_pharmacy`, `hospital_pharmacy`, `ppmv`, `health_centre_pharmacy`.
- Ingestion normalizes unknown values to `unknown`, which is not part of SQL `facility_type`.

**Impact:** high risk of runtime write failures, inconsistent filtering behavior, and non-deterministic API representations.

### 2) Validation level representation mismatch

- SQL uses ladder values like `L0_mapped`, `L1_contact_confirmed`, etc.
- Ingestion pipeline emits short forms (`L0`) plus separate labels.
- API examples also use short forms (`L2`) in response payloads.

**Impact:** brittle mapping logic, increased transformation complexity, and potential incorrect status comparisons.

### 3) Integration inventory mismatch

Integration docs indicate Agent 08 deliverables are complete, but no `agent-08-*` directory exists in the repository.

**Impact:** downstream assumptions in dependency tracking may be inaccurate; execution order and “satisfied dependency” claims may be premature.

### 4) Compliance metadata not fully operationalized in source registry

Source catalog has rich provenance metadata, but does not yet encode per-source lawful basis and retention obligations as structured fields.

**Impact:** harder enforcement/auditing of NDPA obligations at ingestion-time and during retention workflows.

## Recommended Priority Plan

## P0 (Immediate)

1. **Establish a single canonical vocabulary contract**
   - Create a shared enum source (or compatibility matrix) for:
     - `facility_type`
     - `operational_status`
     - `validation_level`
   - Enforce in SQL, ingestion normalization, and OpenAPI schemas.

2. **Add schema compatibility tests**
   - Add automated checks that compare enum values between:
     - SQL DDL
     - OpenAPI components
     - ingestion canonical mapping output

3. **Repair dependency tracker truthfulness**
   - Update Agent 07 tracker to reflect actual in-repo deliverables.
   - Mark missing artifacts explicitly as blocked/not-delivered.

## P1 (Near-term)

1. **Strengthen ingestion validation paths**
   - Reject non-canonical `facility_type` early with actionable errors (instead of silently emitting unsupported values).
   - Emit machine-readable rejection reasons and source-level quality counters.

2. **Operationalize compliance controls in metadata**
   - Add structured fields in `source_registry.json` for lawful basis, retention class, sharing constraints, and PII handling requirements.

3. **Create implementation readiness checklist**
   - Add a “spec-to-build readiness” gate spanning data model, API model, security middleware, and verification ops.

## P2 (Medium-term)

1. **Formalize contract versioning**
   - Version all cross-workstream contracts (vocabulary, status ladder, evidence types, source tiers).

2. **End-to-end dry run**
   - Use synthetic sample data to run: ingest -> dedup -> validation status update -> API retrieval -> provenance audit trace.

## Suggested Success Metrics

- 0 enum mismatch failures across SQL/OpenAPI/ingestion compatibility tests.
- 100% of source entries include structured lawful basis + retention profile.
- Dependency tracker “complete” claims match repository contents.
- Synthetic E2E run completes with full provenance chain and expected validation transitions.

## Overall Assessment

Strong architecture and governance design. The next milestone should focus less on adding new documents and more on **contract consolidation + integration verification** so implementation can proceed without semantic drift.
