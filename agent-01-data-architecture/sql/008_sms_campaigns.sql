-- 008_sms_campaigns.sql
-- SMS campaign tables for bulk L1 phone verification.
-- Part of the SMS-First Contact Confirmation strategy.

begin;

-- ---------------------------------------------------------------------------
-- Enum: sms_campaign_status
-- ---------------------------------------------------------------------------

do $$ begin
    create type sms_campaign_status as enum (
        'draft',        -- created but not yet launched
        'active',       -- messages generated, SMS blasting in progress
        'completed'     -- all messages resolved (replied/expired/failed)
    );
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------------
-- Enum: sms_message_status
-- ---------------------------------------------------------------------------

do $$ begin
    create type sms_message_status as enum (
        'pending',      -- message generated, awaiting send by gateway
        'sent',         -- dispatched to SMS gateway
        'delivered',    -- delivery confirmed by carrier
        'replied',      -- inbound reply received (unparseable)
        'confirmed',    -- valid reply parsed, L1 promotion executed
        'expired',      -- no reply after max_attempts
        'failed'        -- delivery failure from provider
    );
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------------
-- Table: sms_campaigns (batch metadata)
-- ---------------------------------------------------------------------------

create table if not exists sms_campaigns (
    id                      uuid primary key default gen_random_uuid(),
    campaign_name           text not null,
    description             text,
    status                  sms_campaign_status not null default 'draft',

    -- targeting criteria (stored for audit trail)
    target_filters          jsonb not null default '{}',

    -- message template with {pharmacy_name}, {address}, {msg_id_short} placeholders
    message_template        text not null,

    -- retry configuration
    max_attempts            integer not null default 3,
    retry_interval_hours    integer not null default 48,

    -- aggregate counters (updated as messages progress)
    total_messages          integer not null default 0,
    sent_count              integer not null default 0,
    delivered_count         integer not null default 0,
    replied_count           integer not null default 0,
    confirmed_count         integer not null default 0,
    expired_count           integer not null default 0,
    failed_count            integer not null default 0,

    -- lifecycle timestamps
    launched_at             timestamptz,
    completed_at            timestamptz,

    -- provenance
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),
    created_by              text not null,
    updated_by              text not null
);

create index if not exists idx_sms_campaigns_status
    on sms_campaigns (status, created_at);

-- ---------------------------------------------------------------------------
-- Table: sms_messages (per-pharmacy outbound/inbound tracking)
-- ---------------------------------------------------------------------------

create table if not exists sms_messages (
    id                      uuid primary key default gen_random_uuid(),
    campaign_id             uuid not null references sms_campaigns(id) on delete cascade,
    pharmacy_id             uuid not null references pharmacy_locations(id) on delete cascade,

    -- target contact (snapshot at send time)
    phone_number            text not null,
    pharmacy_name           text not null,
    pharmacy_address        text,

    -- rendered message content
    outbound_message        text not null,

    -- status tracking
    status                  sms_message_status not null default 'pending',
    attempt_number          integer not null default 1,

    -- SMS provider interaction
    provider_message_id     text,
    sent_at                 timestamptz,
    delivered_at            timestamptz,

    -- inbound reply
    reply_text              text,
    reply_received_at       timestamptz,
    parsed_status           text,           -- 'operating', 'closed', 'relocated', or null

    -- L1 promotion tracking
    promoted                boolean not null default false,
    promoted_at             timestamptz,
    history_id              uuid,           -- references validation_status_history

    -- failure tracking
    failure_reason          text,

    -- provenance
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),
    created_by              text not null,
    updated_by              text not null
);

create index if not exists idx_sms_msg_campaign
    on sms_messages (campaign_id, status);
create index if not exists idx_sms_msg_pharmacy
    on sms_messages (pharmacy_id);
create index if not exists idx_sms_msg_status
    on sms_messages (status, created_at);
create index if not exists idx_sms_msg_phone
    on sms_messages (phone_number);
create index if not exists idx_sms_msg_provider_id
    on sms_messages (provider_message_id)
    where provider_message_id is not null;

commit;
