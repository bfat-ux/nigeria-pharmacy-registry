# FHIR R4 Mapping Specification

## Overview

The Nigeria Pharmacy Registry (NPR) maintains an internal relational+geospatial data model optimized for ingestion, deduplication, and progressive validation. For interoperability with Nigeria's health information ecosystem (NHIA, state HIEs, development partners), the registry exposes a FHIR R4 mapping layer that translates internal records into standard FHIR resources.

This document describes the mapping decisions, Nigeria-specific extensions, and operational constraints.

---

## FHIR Resources Used

| FHIR Resource  | NPR Source                              | Purpose                                      |
|----------------|-----------------------------------------|----------------------------------------------|
| Location       | `pharmacy_locations`, `contacts`, `external_identifiers` | Represents a physical dispensing endpoint    |
| Organization   | `pharmacy_locations` (future: `organizations` table)      | Represents the entity that owns/operates a location |

### Why Location + Organization (not just Location)?

FHIR separates the physical place (Location) from the legal/operational entity (Organization). This matters for Nigeria because:

- A single owner may operate multiple pharmacy premises
- PCN licenses are issued to premises (Location) but tied to a responsible pharmacist and legal entity (Organization)
- NHIA contracts are with organizations, not locations

Currently the NPR schema does not have a separate `organizations` table. The Organization mapping is synthesized from pharmacy_locations data. When a dedicated organizations table is introduced, the mapping activates fully.

---

## Nigeria-Specific Extensions

Standard FHIR R4 does not cover several concepts critical to the Nigerian pharmacy context. The following custom extensions are defined:

### 1. Validation Level (`validation-level`)

- **URL:** `https://nigeria-pharmacy-registry.internal/fhir/StructureDefinition/validation-level`
- **Type:** Coding
- **Binding:** `https://nigeria-pharmacy-registry.internal/fhir/CodeSystem/validation-level`
- **Rationale:** The NPR validation ladder (L0-L4) is central to the registry's trust model. No standard FHIR field captures progressive data quality verification.

| Code | Display                   | Definition                                          |
|------|---------------------------|-----------------------------------------------------|
| L0   | Mapped                    | Record exists from an ingested data source          |
| L1   | Contact Confirmed         | Phone/email contact verified via outbound call/msg  |
| L2   | Evidence Documented       | Location confirmed + optional storefront photo      |
| L3   | Regulator/Partner Verified| Cross-referenced with official or partner dataset   |
| L4   | High-Assurance            | Biometric check-in or in-person audit (future)      |

### 2. Address Ward (`address-ward`)

- **URL:** `https://nigeria-pharmacy-registry.internal/fhir/StructureDefinition/address-ward`
- **Type:** string
- **Applied to:** `Location.address`, `Organization.address`
- **Rationale:** Nigeria's administrative hierarchy is State > LGA > Ward. FHIR Address has `state` and `district` (mapped to LGA) but no ward-level field.

### 3. Primary Data Source (`primary-source`)

- **URL:** `https://nigeria-pharmacy-registry.internal/fhir/StructureDefinition/primary-source`
- **Type:** string
- **Applied to:** Location
- **Rationale:** Provenance tracking is a non-negotiable. This extension exposes the primary ingestion source for each record.

---

## Facility Type Code System

Standard FHIR and HL7 code systems do not include PPMVs (Patent and Proprietary Medicine Vendors), a large and important category in Nigeria's pharmaceutical landscape. A custom code system is defined:

- **System URL:** `https://nigeria-pharmacy-registry.internal/fhir/CodeSystem/facility-type`

| Code      | Display                                  |
|-----------|------------------------------------------|
| PHARM     | Community Pharmacy                       |
| PPMV      | Patent and Proprietary Medicine Vendor   |
| HOSPHARM  | Hospital Pharmacy                        |

In the Organization resource, dual coding is used: HL7's standard `organization-type` (`prov` = Healthcare Provider) plus the Nigeria-specific facility-type code.

---

## Identifier Systems

External identifiers map to `Location.identifier` and `Organization.identifier` with the following system URIs:

| Identifier Type    | FHIR System URI                        | Issuing Authority |
|--------------------|----------------------------------------|-------------------|
| PCN Premises ID    | `https://pcn.gov.ng/premises`          | PCN               |
| NHIA Facility ID   | `https://nhia.gov.ng/facilities`       | NHIA              |
| OSM Node ID        | `https://www.openstreetmap.org/node`   | OpenStreetMap     |
| GRID3 ID           | `https://grid3.gov.ng/facilities`      | GRID3 Nigeria     |
| CAC Registration   | `https://cac.gov.ng/companies`         | CAC (future)      |

---

## Key Mapping Decisions

### Location.status vs. operational_status

FHIR `Location.status` only supports three values: `active`, `suspended`, `inactive`. The NPR has four operational statuses:

| NPR Status            | FHIR Status  | Notes                                    |
|-----------------------|-------------- |------------------------------------------|
| `operational`         | `active`      | Direct mapping                           |
| `temporarily_closed`  | `suspended`   | Direct mapping                           |
| `permanently_closed`  | `inactive`    | Direct mapping                           |
| `unknown`             | `active`      | Defaults to active with data-absent-reason extension |

### Geolocation

PostGIS `geography(point, 4326)` maps directly to `Location.position.latitude` / `Location.position.longitude`. Both use WGS84 (SRID 4326), so no coordinate transformation is needed.

### Contact Data Sensitivity

Phone numbers and emails are sensitive (risk of harassment — see threat model). The FHIR endpoint:

- Only exports contacts marked `is_primary = true`
- Omits unverified contacts by default
- Rate limits the FHIR endpoint to prevent bulk scraping
- Does NOT include contact_person names in public FHIR responses unless explicitly authorized

### WhatsApp Contacts

FHIR `ContactPoint.system` has no WhatsApp value. WhatsApp contacts are mapped as:

- `system`: `other`
- `extension`: custom extension indicating WhatsApp platform

---

## FHIR Endpoint Design

The FHIR mapping layer is **read-only** — it projects internal data as FHIR resources. Writes come through the native NPR API.

### Planned Endpoints

| Endpoint                          | FHIR Operation           |
|-----------------------------------|--------------------------|
| `GET /fhir/Location/{id}`        | Read                     |
| `GET /fhir/Location?name=...`    | Search by name           |
| `GET /fhir/Location?address-state=...` | Search by state    |
| `GET /fhir/Location?near=...`    | Search by proximity      |
| `GET /fhir/Organization/{id}`    | Read                     |

### Pagination

FHIR Bundle pagination with `_count` and page links. Default page size: 20. Maximum: 100.

---

## Future Considerations

1. **FHIR HealthcareService:** When the registry tracks specific services (e.g., immunization, family planning commodities), a HealthcareService resource may be added.
2. **Nigeria HFR Integration:** If Nigeria establishes a national Health Facility Registry with FHIR endpoints, bidirectional sync should use these mappings.
3. **IHE mCSD Profile:** The Mobile Care Services Discovery (mCSD) profile is the natural fit for a facility registry. NPR should aim for mCSD conformance.
4. **Provenance Resource:** FHIR Provenance resources could expose the full audit trail via FHIR, but this is deferred until there is partner demand.
