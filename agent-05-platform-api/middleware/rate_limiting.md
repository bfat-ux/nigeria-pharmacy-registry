# Rate Limiting — Middleware Design Spec

## Purpose

Protect the registry from abuse, prevent bulk harvesting of contact data, and
ensure fair access across all consumers. Rate limiting is the primary technical
control for mitigating risk PRV-002 (bulk export / scraping) identified in the
risk register.

---

## Architecture

```
Request → API Gateway → Rate Limiter → Route Handler → Response
                ↓
         Redis (sliding window counters)
```

**Implementation:** Token bucket via Redis, keyed by API key (or IP for
unauthenticated requests). The rate limiter runs as Express/Fastify middleware
before any route handler.

**Store:** Redis with key expiry. Falls back to in-memory store if Redis is
unreachable (degraded mode — applies most restrictive limits).

---

## Rate Limit Tiers

### Global Request Limits

| Tier | Window | Max Requests | Burst |
|---|---|---|---|
| `public` (no key / invalid key) | 1 minute | 60 | 10 |
| `registry_read` | 1 minute | 300 | 50 |
| `registry_write` | 1 minute | 300 | 50 |
| `admin` | 1 minute | 600 | 100 |

### Contact-Data Sub-Limit

Contact data (phone, email, `contact_person`) is the most sensitive
non-patient data in the registry. A separate counter tracks responses that
include unmasked contact fields.

| Tier | Window | Max Contact Responses |
|---|---|---|
| `registry_read` | 1 minute | 100 |
| `admin` | 1 minute | 300 |
| `public` | — | Contacts redacted; no sub-limit needed |

When the contact sub-limit is hit, the API continues to serve requests but
redacts contact data (same as the public tier) until the window resets. The
response includes a `X-Contact-Limit-Exceeded: true` header.

### Geospatial Endpoint Limit

The `/pharmacies/nearest` endpoint is more expensive (PostGIS spatial index
scan). An additional per-key limit prevents abuse:

| Tier | Window | Max Nearest Requests |
|---|---|---|
| `public` | 1 minute | 20 |
| `registry_read` | 1 minute | 60 |
| `admin` | 1 minute | 120 |

---

## Sliding Window Algorithm

Use a **sliding window log** algorithm for accuracy:

1. Each request appends a timestamped entry to a sorted set in Redis
   (`ZADD key timestamp timestamp`).
2. Remove entries older than the window (`ZREMRANGEBYSCORE key 0 (now - window)`).
3. Count remaining entries (`ZCARD key`).
4. If count exceeds the limit, reject with `429`.

Redis key pattern: `ratelimit:{tier}:{api_key_or_ip}:{endpoint_class}`

**TTL:** Set key expiry to `window_seconds + 10` to auto-clean stale keys.

---

## Response Headers

Every response includes:

| Header | Value |
|---|---|
| `X-RateLimit-Limit` | Max requests for the current window |
| `X-RateLimit-Remaining` | Remaining requests |
| `X-RateLimit-Reset` | UTC epoch seconds when the window resets |

On `429` responses, additionally:

| Header | Value |
|---|---|
| `Retry-After` | Seconds until the client should retry |

---

## Rejection Response

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Request rate limit exceeded. See Retry-After header.",
    "details": [
      {
        "field": "X-API-Key",
        "issue": "60 requests per minute exceeded for public tier"
      }
    ]
  }
}
```

HTTP status: `429 Too Many Requests`.

---

## Abuse Detection

Beyond per-key limits, the middleware monitors for abuse patterns:

| Pattern | Detection | Response |
|---|---|---|
| Sequential ID enumeration | >50 sequential `/pharmacies/{id}` calls in 1 min | Temporary block (10 min) |
| Contact harvesting | Contact sub-limit hit >3 times in 1 hour | Flag for review; reduce to public tier |
| Geo-sweeping | >100 unique lat/lon pairs from same key in 1 hour | Flag for review |
| Credential stuffing | >10 invalid API keys from same IP in 5 min | IP-level block (30 min) |

Abuse events are logged to `audit_log` with `event_type = 'rate_limit_abuse'`.

---

## Bypass / Allowlist

- The `/health` endpoint is exempt from rate limiting.
- Internal service-to-service calls (identified by mTLS client cert or
  internal network CIDR) use a dedicated `internal` tier with higher limits.
- Regulator sync jobs (PCN, NAFDAC) use dedicated API keys with custom
  limits set at provisioning time.

---

## Degraded Mode

If Redis is unavailable:

1. Fall back to in-memory `Map<string, number[]>` with the same sliding
   window logic.
2. Apply the **most restrictive** tier limits (public) to all requests.
3. Log a `rate_limiter_degraded` event.
4. Emit a health check warning (`/health` returns `"status": "degraded"`).

---

## Configuration

Rate limit values are defined in environment config, not hardcoded:

```yaml
rate_limiting:
  store: redis
  redis_url: ${REDIS_URL}
  fallback: in_memory
  tiers:
    public:
      global: { window_seconds: 60, max_requests: 60, burst: 10 }
      nearest: { window_seconds: 60, max_requests: 20 }
    registry_read:
      global: { window_seconds: 60, max_requests: 300, burst: 50 }
      contact: { window_seconds: 60, max_requests: 100 }
      nearest: { window_seconds: 60, max_requests: 60 }
    admin:
      global: { window_seconds: 60, max_requests: 600, burst: 100 }
      contact: { window_seconds: 60, max_requests: 300 }
      nearest: { window_seconds: 60, max_requests: 120 }
```

---

## Monitoring

| Metric | Type | Labels |
|---|---|---|
| `npr_requests_total` | Counter | tier, endpoint, status |
| `npr_rate_limit_rejections_total` | Counter | tier, endpoint |
| `npr_contact_limit_exceeded_total` | Counter | tier |
| `npr_abuse_events_total` | Counter | pattern |
