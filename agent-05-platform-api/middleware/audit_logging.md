# Audit Logging — Middleware Design Spec

## Purpose

Maintain an immutable, append-only audit trail of every API request and
significant system event. This supports the project's non-negotiable:
**"Provenance for every record — every insert, update, and status change must
have a logged source, timestamp, and actor."**

Audit logging writes to the `audit_log` table defined in
`agent-01-data-architecture/sql/003_provenance_audit.sql`.

---

## Architecture

```
Request → Auth Middleware → Audit Middleware (pre) → Route Handler → Audit Middleware (post) → Response
                                    ↓                                        ↓
                              audit_log (start)                     audit_log (complete)
```

The middleware captures request metadata on entry and enriches it with response
metadata on exit. A single `audit_log` row is written per request.

---

## What Gets Logged

### Per-Request Fields

| Field | Source | Example |
|---|---|---|
| `event_type` | Derived from route | `api_request` |
| `event_action` | HTTP method + route pattern | `GET /pharmacies/search` |
| `actor` | API key ID or OAuth subject | `apikey:ak-00142` |
| `actor_type` | From auth tier | `api_user` |
| `resource_type` | From route | `pharmacy_location` |
| `resource_id` | From path param (if present) | `b7e2c9f4-...` |
| `request_path` | Full path with query | `/v1/pharmacies/search?state=Lagos` |
| `request_method` | HTTP method | `GET` |
| `request_ip` | Client IP (X-Forwarded-For aware) | `41.204.12.55` |
| `response_status` | HTTP status code | `200` |
| `duration_ms` | Wall-clock time | `42` |
| `detail` | JSONB with additional context | `{"query_params": {...}}` |

### Detail JSONB Contents

The `detail` column captures context that varies by endpoint:

```json
{
  "query_params": {
    "state": "Lagos",
    "min_validation_level": "L2"
  },
  "result_count": 142,
  "contact_data_included": true,
  "rate_limit_remaining": 248,
  "user_agent": "NprSync/1.0"
}
```

**Sensitive data exclusion:** Request bodies, response bodies, API key values,
and bearer tokens are **never** written to the audit log.

---

## Event Types

| Event Type | Trigger | Actor |
|---|---|---|
| `api_request` | Every API call | Caller (key/token/anonymous) |
| `auth_failure` | Invalid or expired credential | `anonymous` |
| `rate_limit_exceeded` | 429 returned | Caller |
| `rate_limit_abuse` | Abuse pattern detected | Caller |
| `contact_data_access` | Response includes unmasked contacts | Caller |
| `validation_change` | Validation level transition (via write API) | Caller |
| `data_ingestion` | Batch ingestion completed | System |
| `system_error` | Unhandled exception | `system` |

---

## Immutability

The `audit_log` table enforces immutability:

1. **No UPDATE or DELETE** — The application database role used by the API
   has only `INSERT` and `SELECT` grants on `audit_log`.
2. **No soft deletes** — There is no `deleted_at` column.
3. **Retention** — Minimum 3-year retention per data governance spec.
   Archival to cold storage (S3/GCS) after 12 months for query performance.
4. **Tamper detection** — Each row includes a `created_at` timestamp set by
   `DEFAULT now()`. The application cannot override this value.

---

## Write Strategy

### Asynchronous by Default

To avoid adding latency to API responses, audit log writes use a **buffered
async** strategy:

1. Middleware captures audit data into an in-memory buffer.
2. A background worker flushes the buffer to PostgreSQL every 1 second or
   when the buffer reaches 100 entries (whichever comes first).
3. If the database is unreachable, the buffer writes to a local WAL file
   (`/var/log/npr/audit_wal.jsonl`) and replays on reconnection.

### Synchronous for Critical Events

These event types are written synchronously (blocking the response) to
guarantee capture:

- `validation_change`
- `rate_limit_abuse`
- `auth_failure` (repeated)

---

## Correlation

Every request is assigned a unique `X-Request-Id` (UUID v4) in the first
middleware layer. This ID:

- Is included in the `detail` JSONB of the audit log entry.
- Is returned to the caller in the `X-Request-Id` response header.
- Is propagated to internal service calls for distributed tracing.
- Links the `audit_log` entry to any `provenance_records` created during
  the request.

---

## Contact Data Access Tracking

When a response includes unmasked contact data (tier `registry_read` or
above), the middleware:

1. Logs a `contact_data_access` event.
2. Records the count of contact records returned.
3. Records the pharmacy IDs whose contacts were accessed.

This creates an auditable trail of who accessed contact data and when,
supporting NDPA compliance requirements.

---

## Log Retention and Archival

| Age | Storage | Access |
|---|---|---|
| 0–12 months | PostgreSQL `audit_log` table | Full query via SQL |
| 12–36 months | Cold storage (compressed JSONL) | Restore on demand |
| >36 months | Deleted per retention policy | — |

Archival is performed by a scheduled job that:
1. Selects rows older than 12 months.
2. Exports to compressed JSONL in object storage.
3. Deletes archived rows from PostgreSQL (admin-only operation).

---

## Query Patterns

Common audit queries the middleware must support efficiently:

| Query | Index Support |
|---|---|
| All actions by a specific API key | `idx_audit_log_actor` |
| All access to a specific pharmacy | `idx_audit_log_resource` |
| All events in a time range | `idx_audit_log_created_at` |
| All contact data access events | `idx_audit_log_event_type` |
| Failed auth attempts by IP | `idx_audit_log_request_ip` |

These indexes are defined in `003_provenance_audit.sql`.

---

## Configuration

```yaml
audit_logging:
  enabled: true
  async_buffer_size: 100
  flush_interval_ms: 1000
  wal_path: /var/log/npr/audit_wal.jsonl
  sync_event_types:
    - validation_change
    - rate_limit_abuse
  retention:
    hot_months: 12
    cold_months: 36
  excluded_paths:
    - /health
    - /favicon.ico
```

---

## Monitoring

| Metric | Type | Description |
|---|---|---|
| `npr_audit_writes_total` | Counter | Total audit log entries written |
| `npr_audit_write_errors_total` | Counter | Failed writes (buffered to WAL) |
| `npr_audit_buffer_size` | Gauge | Current buffer depth |
| `npr_audit_wal_size_bytes` | Gauge | Unprocessed WAL file size |
| `npr_contact_access_total` | Counter | Contact data access events |
