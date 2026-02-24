-- =============================================================================
-- 005_api_keys.sql
-- API key storage for authentication and access control
-- Keys are stored as bcrypt hashes; plaintext is shown once at creation.
-- =============================================================================

create table if not exists api_keys (
    id                  uuid primary key default gen_random_uuid(),
    key_prefix          varchar(16) not null,           -- first 16 chars for fast lookup
    key_hash            varchar(72) not null,            -- bcrypt hash of full key
    name                varchar(200) not null,           -- human-readable label
    tier                varchar(50) not null,            -- public, registry_read, registry_write, admin
    scopes              text[] not null default '{}',    -- fine-grained scopes
    owner_email         varchar(200) not null,
    owner_org           varchar(200),
    is_active           boolean not null default true,
    rate_limit_override jsonb,                           -- custom limits (null = tier default)
    expires_at          timestamptz,
    last_used_at        timestamptz,

    -- provenance
    created_at          timestamptz not null default now(),
    created_by          varchar(200) not null
);

-- partial index: only active keys need fast lookup
create index if not exists idx_api_keys_prefix
    on api_keys (key_prefix) where is_active = true;

create index if not exists idx_api_keys_tier
    on api_keys (tier) where is_active = true;

comment on table api_keys is
    'API key credentials for authenticated access. Keys are bcrypt-hashed; plaintext shown once at provisioning.';
