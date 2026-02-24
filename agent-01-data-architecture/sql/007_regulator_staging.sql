-- 007_regulator_staging.sql
-- Regulator sync staging tables for PCN/NHIA/NAFDAC batch import pipeline.
-- Part of Agent-08: Regulatory Integration.

begin;

-- ---------------------------------------------------------------------------
-- Enum: regulator source type
-- ---------------------------------------------------------------------------

do $$ begin
    create type regulator_source_type as enum ('pcn', 'nhia', 'nafdac');
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------------
-- Enum: regulator match status
-- ---------------------------------------------------------------------------

do $$ begin
    create type regulator_match_status as enum (
        'pending',          -- awaiting matching
        'auto_matched',     -- match_score >= 0.90, eligible for auto-approve
        'probable_match',   -- 0.70 <= match_score < 0.90, needs manual review
        'no_match',         -- match_score < 0.70, potential new pharmacy
        'approved',         -- manually reviewed and confirmed
        'rejected',         -- manually reviewed and rejected
        'promoted'          -- L3 promotion executed
    );
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------------
-- Table: regulator_sync_batches
-- ---------------------------------------------------------------------------

create table if not exists regulator_sync_batches (
    id                  uuid primary key default gen_random_uuid(),
    regulator_source    regulator_source_type not null,
    file_name           text not null,
    file_hash           text,
    extract_date        date,
    record_count        integer not null default 0,
    auto_matched_count  integer not null default 0,
    probable_count      integer not null default 0,
    no_match_count      integer not null default 0,
    promoted_count      integer not null default 0,
    status              text not null default 'processing',
    error_message       text,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

create index if not exists idx_reg_batches_source
    on regulator_sync_batches (regulator_source);
create index if not exists idx_reg_batches_status
    on regulator_sync_batches (status);
create unique index if not exists idx_reg_batches_file_hash
    on regulator_sync_batches (file_hash) where file_hash is not null;

-- ---------------------------------------------------------------------------
-- Table: regulator_staging_records
-- ---------------------------------------------------------------------------

create table if not exists regulator_staging_records (
    id                  uuid primary key default gen_random_uuid(),
    batch_id            uuid not null references regulator_sync_batches(id) on delete cascade,
    regulator_source    regulator_source_type not null,

    -- raw imported fields
    raw_name            text not null,
    raw_registration_id text,
    raw_state           text,
    raw_lga             text,
    raw_address         text,
    raw_phone           text,
    raw_facility_category text,
    raw_data            jsonb not null,

    -- matching results
    match_status        regulator_match_status not null default 'pending',
    matched_pharmacy_id uuid,
    match_score         real,
    match_details       jsonb,

    -- promotion tracking
    promoted            boolean not null default false,
    promoted_at         timestamptz,
    history_id          uuid,

    -- review
    reviewed_by         text,
    reviewed_at         timestamptz,
    review_notes        text,

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

create index if not exists idx_reg_staging_batch
    on regulator_staging_records (batch_id);
create index if not exists idx_reg_staging_status
    on regulator_staging_records (match_status);
create index if not exists idx_reg_staging_pharmacy
    on regulator_staging_records (matched_pharmacy_id)
    where matched_pharmacy_id is not null;
create index if not exists idx_reg_staging_reg_id
    on regulator_staging_records (regulator_source, raw_registration_id);

commit;
