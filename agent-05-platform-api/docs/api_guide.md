# Nigeria Pharmacy Registry — API Developer Guide

## Overview

The NPR API provides programmatic access to the Nigeria Pharmacy Registry, a
national dataset of pharmacy and PPMV (Patent and Proprietary Medicine Vendor)
locations. The API is designed for:

- **Health-tech platforms** integrating pharmacy lookup into patient-facing apps
- **Government partners** synchronising with PCN, NAFDAC, and NHIA datasets
- **Researchers** analysing pharmacy distribution and access patterns
- **Supply-chain systems** routing deliveries to verified dispensing points

**Base URLs:**

| Environment | URL |
|---|---|
| Production | `https://api.npr.ng/v1` |
| Staging | `https://staging-api.npr.ng/v1` |
| Local | `http://localhost:3000/v1` |

All timestamps are UTC. All IDs are UUID v4.

---

## Authentication

### API Key (primary)

Include your key in the `X-API-Key` header on every request:

```bash
curl -H "X-API-Key: npr_live_abc123def456" \
  https://api.npr.ng/v1/pharmacies/search?state=Lagos
```

Keys are issued by the NPR platform team and scoped to an **access tier**
(see below). Keep your key secret — rotate it immediately if compromised.

### OAuth 2.0 (partner / machine-to-machine)

For automated integrations (regulator sync, partner pipelines), use OAuth 2.0
Client Credentials:

```bash
curl -X POST https://auth.npr.ng/oauth/token \
  -d grant_type=client_credentials \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d scope=registry_read
```

The returned `access_token` is a bearer token:

```bash
curl -H "Authorization: Bearer eyJhbG..." \
  https://api.npr.ng/v1/pharmacies/search?state=Lagos
```

### Unauthenticated access

The `/pharmacies/search`, `/pharmacies/{id}`, `/pharmacies/nearest`, and FHIR
endpoints accept unauthenticated requests at the **public** tier. Contact data
is redacted and rate limits are aggressive.

---

## Access Tiers

| Tier | Scopes | Contact Data | Rate Limit | Use Case |
|---|---|---|---|---|
| `public` | None (no key) | Redacted | 60 req/min | Public apps, exploration |
| `registry_read` | `registry_read` | Unmasked | 300 req/min | Health-tech integrations |
| `registry_write` | `registry_write` | Unmasked | 300 req/min | Partner data ingestion |
| `admin` | `admin` | Unmasked | 600 req/min | NPR internal, regulators |

**Contact redaction** at the public tier masks phone numbers as `+234****1234`
and emails as `u***@example.com`. `contact_person` is omitted entirely.

---

## Rate Limits

Every response includes rate-limit headers:

| Header | Description |
|---|---|
| `X-RateLimit-Limit` | Max requests in the current window |
| `X-RateLimit-Remaining` | Remaining requests |
| `X-RateLimit-Reset` | UTC epoch seconds when window resets |

When the limit is exceeded, the API returns `429 Too Many Requests` with a
`Retry-After` header (seconds).

**Contact-data endpoints** (`contacts` field in search/detail responses) have
a separate, stricter sub-limit to prevent bulk harvesting:

| Tier | Contact sub-limit |
|---|---|
| `registry_read` | 100 contact-bearing responses / min |
| `admin` | 300 contact-bearing responses / min |

---

## Endpoints

### Search Pharmacies

```
GET /pharmacies/search
```

Search by name, state, LGA, facility type, or validation level.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `name` | string | Partial name match (min 2 chars, trigram similarity) |
| `state` | string | State name or comma-separated list |
| `lga` | string | LGA name (requires `state`) |
| `facility_type` | string | `community_pharmacy`, `hospital_pharmacy`, `ppmv`, `health_centre_pharmacy` |
| `operational_status` | string | `operational`, `temporarily_closed`, `permanently_closed`, `unknown` |
| `min_validation_level` | string | `L0`, `L1`, `L2`, `L3` — returns this level and above |
| `limit` | integer | Page size (default 20, max 100) |
| `offset` | integer | Pagination offset (default 0) |
| `fields` | string | Comma-separated field list to reduce payload |

**Example — find pharmacies in Lagos at L2 or above:**

```bash
curl -H "X-API-Key: npr_live_abc123def456" \
  "https://api.npr.ng/v1/pharmacies/search?state=Lagos&min_validation_level=L2&limit=10"
```

```json
{
  "meta": {
    "total": 142,
    "limit": 10,
    "offset": 0
  },
  "data": [
    {
      "id": "b7e2c9f4-1a3d-4e5f-8b6c-9d0e1f2a3b4c",
      "name": "HealthPlus Pharmacy",
      "facility_type": "community_pharmacy",
      "operational_status": "operational",
      "validation_level": "L2",
      "validation_label": "Evidence Documented",
      "address": {
        "address_line": "12 Admiralty Way",
        "ward": "Lekki Phase 1",
        "lga": "Eti-Osa",
        "state": "Lagos"
      },
      "geolocation": {
        "latitude": 6.4281,
        "longitude": 3.4219
      },
      "contacts": [
        {
          "contact_type": "phone",
          "contact_value": "+2348012345678",
          "is_primary": true,
          "is_verified": true,
          "verified_at": "2025-02-01T14:30:00Z"
        }
      ],
      "external_identifiers": [
        {
          "type": "pcn_premises_id",
          "value": "PCN/LAG/2024/00142",
          "is_current": true
        }
      ],
      "primary_source": "grid3_health_facilities",
      "updated_at": "2025-03-15T09:12:00Z"
    }
  ]
}
```

**Sparse field selection** — reduce payload for low-bandwidth clients:

```bash
curl "https://api.npr.ng/v1/pharmacies/search?state=Lagos&fields=id,name,validation_level,geolocation"
```

---

### Get Pharmacy by ID

```
GET /pharmacies/{pharmacy_id}
```

Returns the full canonical record. Use `include` to embed related history:

```bash
curl -H "X-API-Key: npr_live_abc123def456" \
  "https://api.npr.ng/v1/pharmacies/b7e2c9f4-1a3d-4e5f-8b6c-9d0e1f2a3b4c?include=validation_history"
```

**Include options:** `validation_history`, `operational_history` (comma-separated).

---

### Find Nearest Pharmacies

```
GET /pharmacies/nearest
```

Geospatial proximity search powered by PostGIS.

**Parameters:**

| Name | Type | Required | Description |
|---|---|---|---|
| `latitude` | number | Yes | Latitude (4.0 – 14.0) |
| `longitude` | number | Yes | Longitude (2.5 – 15.0) |
| `radius_km` | number | No | Search radius in km (default 5, max 50) |
| `facility_type` | string | No | Filter by type |
| `min_validation_level` | string | No | Minimum trust level |
| `limit` | integer | No | Max results (default 20, max 100) |

**Example — nearest PPMVs within 3 km:**

```bash
curl -H "X-API-Key: npr_live_abc123def456" \
  "https://api.npr.ng/v1/pharmacies/nearest?latitude=6.5244&longitude=3.3792&radius_km=3&facility_type=ppmv"
```

```json
{
  "meta": {
    "latitude": 6.5244,
    "longitude": 3.3792,
    "radius_km": 3.0,
    "total": 4
  },
  "data": [
    {
      "id": "d4e5f6a7-b8c9-0123-4567-890abcdef012",
      "name": "Mama Nkechi Chemist",
      "facility_type": "ppmv",
      "validation_level": "L1",
      "distance_km": 0.78,
      "geolocation": {
        "latitude": 6.5300,
        "longitude": 3.3810
      }
    }
  ]
}
```

---

### Validation History

```
GET /pharmacies/{pharmacy_id}/validation-history
```

Returns the append-only audit trail of validation level transitions. Requires
authentication (`registry_read` or above).

```bash
curl -H "X-API-Key: npr_live_abc123def456" \
  "https://api.npr.ng/v1/pharmacies/b7e2c9f4-1a3d-4e5f-8b6c-9d0e1f2a3b4c/validation-history"
```

```json
{
  "meta": {
    "pharmacy_id": "b7e2c9f4-1a3d-4e5f-8b6c-9d0e1f2a3b4c",
    "total": 3
  },
  "data": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "old_level": "L1",
      "new_level": "L2",
      "changed_at": "2025-03-15T09:12:00Z",
      "changed_by": "field_agent:fa-00421",
      "actor_type": "field_agent",
      "source_description": "Field visit — GPS + storefront photo",
      "evidence_reference": "ev-20250315-00421"
    },
    {
      "id": "f0e1d2c3-b4a5-6789-0123-456789abcdef",
      "old_level": "L0",
      "new_level": "L1",
      "changed_at": "2025-02-01T14:30:00Z",
      "changed_by": "verification_agent:va-00087",
      "actor_type": "verification_agent",
      "source_description": "Outbound call confirmed facility exists",
      "evidence_reference": "ev-20250201-00087"
    }
  ]
}
```

---

### Change Feed

```
GET /changes
```

Polling-based change feed for downstream data consumers. Returns provenance
records (inserts, updates, status changes) ordered by `happened_at` ascending.

**Parameters:**

| Name | Type | Required | Description |
|---|---|---|---|
| `since` | datetime | Yes | ISO 8601 UTC timestamp — returns changes after this point |
| `entity_type` | string | No | Filter: `pharmacy_location`, `contact`, `external_identifier`, `validation_status`, `operational_status` |
| `action` | string | No | Filter: `insert`, `update`, `status_change`, `merge`, `delete` |
| `limit` | integer | No | Page size (default 50, max 100) |

**Polling pattern:**

1. Start with `since=2025-01-01T00:00:00Z` (or your sync start date).
2. Read the `next_since` value from the response `meta`.
3. Use it as `since` in your next request.
4. When `has_more` is `false`, sleep and poll again.

```bash
curl -H "X-API-Key: npr_live_abc123def456" \
  "https://api.npr.ng/v1/changes?since=2025-03-01T00:00:00Z&limit=50"
```

```json
{
  "meta": {
    "since": "2025-03-01T00:00:00Z",
    "next_since": "2025-03-15T09:12:00Z",
    "count": 50,
    "has_more": true
  },
  "data": [
    {
      "id": "c1d2e3f4-a5b6-7890-1234-567890abcdef",
      "entity_type": "pharmacy_location",
      "entity_id": "b7e2c9f4-1a3d-4e5f-8b6c-9d0e1f2a3b4c",
      "action": "insert",
      "actor": "system:ingestion-pipeline",
      "actor_type": "system",
      "source_system": "grid3_health_facilities",
      "happened_at": "2025-03-02T08:00:00Z",
      "summary": "New pharmacy ingested from GRID3 dataset"
    }
  ]
}
```

---

### FHIR Endpoints

For interoperability with FHIR R4 systems, the API exposes pharmacy data as
FHIR Location resources.

#### Read a Location

```
GET /fhir/Location/{id}
```

Returns `application/fhir+json`. The resource includes Nigeria-specific
extensions for validation level and ward-level addressing.

```bash
curl -H "Accept: application/fhir+json" \
  "https://api.npr.ng/v1/fhir/Location/b7e2c9f4-1a3d-4e5f-8b6c-9d0e1f2a3b4c"
```

#### Search Locations

```
GET /fhir/Location
```

FHIR search parameters:

| Parameter | Description |
|---|---|
| `name` | Location name (partial match) |
| `address-state` | Nigerian state |
| `type` | Facility type code: `PHARM`, `PPMV`, `HOSPHARM` |
| `near` | Proximity: `lat\|lon\|distance\|units` (e.g. `6.52\|3.38\|5\|km`) |
| `status` | `active`, `inactive`, `suspended` |
| `_count` | Results per page (default 20, max 100) |

---

### Health Check

```
GET /health
```

No authentication required. Returns service status and dependency health:

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2025-03-15T10:00:00Z",
  "checks": {
    "database": "up",
    "postgis": "up"
  }
}
```

---

## Error Handling

All errors return a consistent JSON envelope:

```json
{
  "error": {
    "code": "INVALID_PARAMETER",
    "message": "Human-readable description",
    "details": [
      { "field": "latitude", "issue": "Must be between 4.0 and 14.0" }
    ]
  }
}
```

**Error codes:**

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_PARAMETER` | Malformed or out-of-range parameter |
| 400 | `MISSING_PARAMETER` | Required parameter not provided |
| 401 | `UNAUTHORIZED` | Missing or invalid API key / token |
| 403 | `FORBIDDEN` | Key does not have required scope |
| 404 | `NOT_FOUND` | Resource does not exist |
| 429 | `RATE_LIMITED` | Too many requests (see `Retry-After`) |
| 500 | `INTERNAL_ERROR` | Server error (incident logged) |

---

## Low-Bandwidth Design

The API is designed for environments with intermittent connectivity:

1. **Sparse fields** — Use `fields=id,name,state,validation_level` to cut
   payload size by 60-80%.
2. **Small page sizes** — Default is 20 records. Set `limit=10` or lower for
   mobile clients.
3. **Gzip** — All responses support `Accept-Encoding: gzip`. Typical
   compression ratio is 4:1.
4. **ETags** — Responses include `ETag` headers. Use `If-None-Match` to avoid
   re-downloading unchanged data (returns `304 Not Modified`).
5. **Change feed** — Poll `/changes` instead of re-fetching the entire
   dataset. Designed for incremental sync.

---

## Pagination

All list endpoints use offset-based pagination:

| Parameter | Default | Max |
|---|---|---|
| `limit` | 20 | 100 |
| `offset` | 0 | — |

The `meta.total` field in the response tells you the total number of matching
records. The `X-Total-Count` header mirrors this value.

For the change feed, use cursor-based pagination via `next_since` instead of
offset.

---

## Validation Level Reference

When displaying validation levels to end users, use the `validation_label`
field. Do **not** use unqualified terms like "Verified", "Licensed",
"Approved", or "Trusted".

| Level | Label | Display Guidance |
|---|---|---|
| L0 | Mapped | "This location has been identified but not yet confirmed" |
| L1 | Contact Confirmed | "Contact with this facility has been confirmed" |
| L2 | Evidence Documented | "This location has been physically confirmed with evidence" |
| L3 | Regulator Verified | "This facility has been cross-referenced with official records" |

---

## Versioning

The API is versioned via the URL path (`/v1/`). Breaking changes will ship
under a new version (`/v2/`). Non-breaking additions (new optional fields, new
endpoints) are added to the current version.

---

## Support

- **API status:** `https://status.npr.ng`
- **Email:** platform@npr.ng
- **OpenAPI spec:** `https://api.npr.ng/v1/openapi.yaml`
