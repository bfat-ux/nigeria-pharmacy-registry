-- =============================================================================
-- 001_core_schema.sql
-- Core registry tables for the Nigeria Pharmacy Registry
-- =============================================================================

-- facility_type enum: pharmacy, ppmv (Patent & Proprietary Medicine Vendor),
-- hospital_pharmacy
create type facility_type as enum (
    'pharmacy',
    'ppmv',
    'hospital_pharmacy'
);

-- operational_status: whether the facility is currently operating
create type operational_status as enum (
    'operational',
    'temporarily_closed',
    'permanently_closed',
    'unknown'
);

-- validation_level: the validation ladder (L0–L4)
create type validation_level as enum (
    'L0_mapped',
    'L1_contact_confirmed',
    'L2_evidence_documented',
    'L3_regulator_verified',
    'L4_high_assurance'
);

-- =============================================================================
-- pharmacy_locations: canonical registry of dispensing endpoints
-- =============================================================================
create table pharmacy_locations (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null,
    facility_type       facility_type not null,
    operational_status  operational_status not null default 'unknown',

    -- address fields
    address_line_1      text,
    address_line_2      text,
    ward                text,
    lga                 text not null,
    state               text not null,
    country             text not null default 'NG',
    postal_code         text,

    -- current validation level (derived from latest status_history entry,
    -- but stored here for query convenience; updated via trigger/application)
    current_validation_level validation_level not null default 'L0_mapped',

    -- geolocation: PostGIS POINT, SRID 4326 (WGS84)
    geolocation         geography(point, 4326),

    -- source tracking
    primary_source      text,               -- e.g. 'grid3', 'osm', 'pcn_csv'
    primary_source_id   text,               -- ID in the original source dataset

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,       -- actor reference
    updated_by          text not null        -- actor reference
);

-- indexes for common query patterns
create index idx_pharmacy_locations_state on pharmacy_locations (state);
create index idx_pharmacy_locations_lga on pharmacy_locations (state, lga);
create index idx_pharmacy_locations_type on pharmacy_locations (facility_type);
create index idx_pharmacy_locations_validation on pharmacy_locations (current_validation_level);
create index idx_pharmacy_locations_name_trgm on pharmacy_locations using gin (name gin_trgm_ops);

comment on table pharmacy_locations is
    'Canonical registry of pharmacy and dispensing locations across Nigeria.';

-- =============================================================================
-- external_identifiers: links to PCN premises IDs, NHIA facility IDs, etc.
-- =============================================================================
create table external_identifiers (
    id                  uuid primary key default gen_random_uuid(),
    pharmacy_id         uuid not null references pharmacy_locations(id) on delete cascade,
    identifier_type     text not null,       -- e.g. 'pcn_premises_id', 'nhia_facility_id', 'osm_node_id'
    identifier_value    text not null,
    issuing_authority   text,                -- e.g. 'PCN', 'NHIA', 'OpenStreetMap'
    valid_from          date,
    valid_to            date,
    is_current          boolean not null default true,

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null,

    -- prevent duplicate active identifiers of the same type for a pharmacy
    constraint uq_external_id_active unique (pharmacy_id, identifier_type, identifier_value)
);

create index idx_external_identifiers_pharmacy on external_identifiers (pharmacy_id);
create index idx_external_identifiers_type_value on external_identifiers (identifier_type, identifier_value);

comment on table external_identifiers is
    'Links pharmacy_locations to identifiers in external systems (PCN, NHIA, OSM, etc.).';

-- =============================================================================
-- contacts: phone, email, contact person for a pharmacy
-- =============================================================================
create table contacts (
    id                  uuid primary key default gen_random_uuid(),
    pharmacy_id         uuid not null references pharmacy_locations(id) on delete cascade,
    contact_type        text not null,       -- 'phone', 'email', 'whatsapp'
    contact_value       text not null,       -- the actual phone number or email
    contact_person      text,                -- name of the contact person
    is_primary          boolean not null default false,
    is_verified         boolean not null default false,
    verified_at         timestamptz,
    verified_by         text,

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

create index idx_contacts_pharmacy on contacts (pharmacy_id);
create index idx_contacts_type on contacts (contact_type);

comment on table contacts is
    'Contact details for pharmacy locations. Contact data is sensitive and rate-limited at the API layer.';

-- =============================================================================
-- raw_ingested_records: staging table for raw source data before canonical merge
-- =============================================================================
create table raw_ingested_records (
    id                  uuid primary key default gen_random_uuid(),
    source_name         text not null,
    source_dataset      text,
    source_record_id    text,
    raw_data            jsonb not null,
    ingestion_batch_id  uuid,
    canonical_pharmacy_id uuid references pharmacy_locations(id),
    processing_status   text not null default 'pending',  -- 'pending', 'processed', 'rejected', 'duplicate'
    processing_notes    text,

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

create index idx_raw_ingested_source on raw_ingested_records (source_name, source_dataset);
create index idx_raw_ingested_batch on raw_ingested_records (ingestion_batch_id);
create index idx_raw_ingested_canonical on raw_ingested_records (canonical_pharmacy_id);
create index idx_raw_ingested_status on raw_ingested_records (processing_status);

comment on table raw_ingested_records is
    'Staging table for raw data from ingestion sources. Kept separate from canonical registry per coding standards.';
