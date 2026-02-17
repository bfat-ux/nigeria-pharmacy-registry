# Authentication & Authorization — Middleware Design Spec

## Purpose

Control access to registry data based on caller identity and scope. The auth
model balances open access to non-sensitive facility data with strict
protection of contact information and write operations.

---

## Architecture

```
Request
  ↓
Extract credential (X-API-Key header OR Authorization: Bearer token)
  ↓
Validate credential → Resolve tier + scopes
  ↓
Attach auth context to request (tier, scopes, actor_id, actor_type)
  ↓
Route handler (checks required scope)
  ↓
Response middleware (redacts contact data if tier = public)
```

The auth middleware runs after rate-limit identification (since rate limits
depend on the resolved tier) but before any route handler.

---

## Authentication Methods

### 1. API Key (`X-API-Key` header)

Primary method for most consumers.

**Key format:** `npr_{env}_{32 random alphanumeric chars}`

- `npr_live_abc123...` — production
- `npr_test_xyz789...` — staging/sandbox

**Storage:** API keys are stored as bcrypt hashes in a `api_keys` table. The
plaintext key is shown once at provisioning time and never stored.

```sql
create table api_keys (
    id              uuid primary key default gen_random_uuid(),
    key_prefix      varchar(16) not null,          -- "npr_live_abc1" (for lookup)
    key_hash        varchar(72) not null,           -- bcrypt hash
    name            varchar(200) not null,          -- human label
    tier            varchar(50) not null,           -- public, registry_read, registry_write, admin
    scopes          text[] not null default '{}',   -- fine-grained scopes
    owner_email     varchar(200) not null,
    owner_org       varchar(200),
    is_active       boolean not null default true,
    rate_limit_override jsonb,                      -- custom limits (null = use tier default)
    expires_at      timestamptz,
    last_used_at    timestamptz,
    created_at      timestamptz not null default now(),
    created_by      varchar(200) not null
);

create index idx_api_keys_prefix on api_keys (key_prefix) where is_active = true;
```

**Lookup flow:**
1. Extract the first 16 characters of the key as `key_prefix`.
2. Query `api_keys` by `key_prefix` where `is_active = true`.
3. Verify the full key against `key_hash` using bcrypt.
4. Check `expires_at` (if set).
5. Update `last_used_at`.

**Caching:** Resolved key → tier/scope mappings are cached in Redis for 5
minutes to avoid bcrypt verification on every request. Cache is invalidated
on key revocation.

### 2. OAuth 2.0 Bearer Token

For partner systems and regulator integrations using machine-to-machine flows.

**Supported flow:** Client Credentials (RFC 6749 Section 4.4).

**Token endpoint:** `POST https://auth.npr.ng/oauth/token`

```
grant_type=client_credentials
client_id=YOUR_CLIENT_ID
client_secret=YOUR_CLIENT_SECRET
scope=registry_read
```

**Token format:** JWT (RS256) with claims:

```json
{
  "iss": "https://auth.npr.ng",
  "sub": "client:pcn-sync-service",
  "aud": "https://api.npr.ng",
  "scope": "registry_read registry_write",
  "tier": "registry_write",
  "exp": 1711036800,
  "iat": 1711033200,
  "jti": "unique-token-id"
}
```

**Validation:**
1. Verify JWT signature against the public key (JWKS endpoint).
2. Check `exp` (expiry), `iss` (issuer), `aud` (audience).
3. Extract `tier` and `scope` claims.
4. Check token not in revocation list (Redis set).

**Token lifetime:** 1 hour. Clients must re-request tokens before expiry.

### 3. Unauthenticated (No Credential)

Requests without a key or token are accepted at the `public` tier with:

- Contact data redacted.
- Aggressive rate limits (60 req/min).
- No access to validation history, change feed, or write endpoints.

---

## Access Tiers and Scopes

### Tiers

| Tier | Description |
|---|---|
| `public` | Unauthenticated or explicitly public-tier key |
| `registry_read` | Read access including contact data |
| `registry_write` | Write access for partner ingestion |
| `admin` | Full access (NPR team, regulator admins) |

### Scopes

Fine-grained scopes within each tier:

| Scope | Tier Required | Grants |
|---|---|---|
| `public` | public | Search, lookup, nearest (no contacts) |
| `registry_read` | registry_read | All public + contacts + validation history + change feed |
| `registry_write` | registry_write | All registry_read + data ingestion endpoints |
| `admin` | admin | All registry_write + audit log access + key management |

### Endpoint → Scope Matrix

| Endpoint | `public` | `registry_read` | `registry_write` | `admin` |
|---|---|---|---|---|
| `GET /pharmacies/search` | Yes (redacted) | Yes | Yes | Yes |
| `GET /pharmacies/{id}` | Yes (redacted) | Yes | Yes | Yes |
| `GET /pharmacies/nearest` | Yes (redacted) | Yes | Yes | Yes |
| `GET /pharmacies/{id}/validation-history` | No | Yes | Yes | Yes |
| `GET /changes` | No | Yes | Yes | Yes |
| `GET /fhir/Location` | Yes (redacted) | Yes | Yes | Yes |
| `GET /fhir/Location/{id}` | Yes (redacted) | Yes | Yes | Yes |
| `GET /health` | Yes | Yes | Yes | Yes |

---

## Contact Data Redaction

The response middleware applies redaction when the caller's tier is `public`:

| Field | Redaction |
|---|---|
| `contact_value` (phone) | `+234****1234` (last 4 digits preserved) |
| `contact_value` (email) | `u***@example.com` (first char + domain) |
| `contact_person` | Omitted entirely |
| `verified_by` | Omitted |

Redaction is applied in the serialisation layer, **not** by modifying the
database query. This ensures audit logs accurately record what data was
accessed.

---

## Key Lifecycle

### Provisioning

1. Admin creates key via internal dashboard or CLI.
2. System generates random key, computes bcrypt hash, stores hash.
3. Plaintext key is displayed **once** and never stored.
4. Key is associated with an owner (email, organisation), tier, and scopes.

### Rotation

1. Admin provisions a new key for the same owner.
2. Old key is marked `is_active = false` after a grace period (default 7 days).
3. Both keys work during the grace period.

### Revocation

1. Admin sets `is_active = false` on the key.
2. Redis cache entry is deleted immediately.
3. Key is rejected on the next request.

### Expiry

Keys may have an `expires_at` timestamp. The auth middleware checks this on
every request. Expired keys are treated as invalid.

---

## Security Controls

| Control | Implementation |
|---|---|
| Key stored as hash | bcrypt with work factor 12 |
| Key transmitted over TLS only | HTTPS enforced; HSTS header |
| Key never logged | Audit log records `api_key_id`, not the key value |
| Token signature verification | RS256 with key rotation via JWKS |
| Token revocation | Redis set checked on every request |
| Brute-force protection | Rate limit on auth failures (10/5min per IP) |
| Scope enforcement | Middleware checks scope before route handler executes |

---

## Error Responses

### Missing Credential (on protected endpoint)

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Authentication required. Provide an X-API-Key header or Bearer token."
  }
}
```

HTTP `401 Unauthorized` with `WWW-Authenticate: ApiKey, Bearer` header.

### Invalid Credential

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid or expired API key."
  }
}
```

HTTP `401 Unauthorized`. Logged as `auth_failure` event.

### Insufficient Scope

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Your API key does not have the 'registry_read' scope required for this endpoint."
  }
}
```

HTTP `403 Forbidden`.

---

## Request Context

After authentication, the middleware attaches an `auth` context to the request
object, available to all downstream handlers:

```typescript
interface AuthContext {
  tier: 'public' | 'registry_read' | 'registry_write' | 'admin';
  scopes: string[];
  actor_id: string;       // "apikey:ak-00142" or "client:pcn-sync" or "anonymous"
  actor_type: string;     // "api_user", "partner_api", "regulator_sync", "admin", "anonymous"
  key_id?: string;        // UUID of the api_keys row (for API key auth)
  client_id?: string;     // OAuth client ID (for token auth)
}
```

This context is used by:
- **Rate limiter** — to select the correct tier limits.
- **Audit logger** — to record the actor.
- **Response middleware** — to decide whether to redact contacts.

---

## Configuration

```yaml
auth:
  api_key:
    header: X-API-Key
    prefix_length: 16
    bcrypt_rounds: 12
    cache_ttl_seconds: 300
  oauth:
    issuer: https://auth.npr.ng
    audience: https://api.npr.ng
    jwks_uri: https://auth.npr.ng/.well-known/jwks.json
    token_endpoint: https://auth.npr.ng/oauth/token
    token_lifetime_seconds: 3600
  brute_force:
    max_failures_per_ip: 10
    window_seconds: 300
    block_duration_seconds: 1800
```
