-- =============================================================================
-- 003_provenance_audit.sql
-- Provenance tracking and audit logging
-- Every insert, update, and status change must have a logged source,
-- timestamp, and actor. (CLAUDE.md non-negotiable)
-- =============================================================================

-- =============================================================================
-- provenance_records: tracks the origin and lineage of every data change
-- =============================================================================
create table provenance_records (
    id                  uuid primary key default gen_random_uuid(),
    entity_type         text not null,       -- 'pharmacy_location', 'contact', 'external_identifier', etc.
    entity_id           uuid not null,       -- FK to the affected record (not enforced to allow polymorphism)
    action              text not null,       -- 'create', 'update', 'merge', 'delete', 'import', 'verify'
    actor               text not null,       -- who performed the action (user ID, system process name)
    actor_type          text not null,       -- 'system', 'field_agent', 'partner_api', 'regulator_sync'
    source_system       text,                -- originating system: 'grid3', 'osm', 'pcn_portal', 'field_app'
    source_dataset      text,                -- specific dataset or batch identifier
    source_record_id    text,                -- ID in the source system
    happened_at         timestamptz not null default now(),
    detail              jsonb,               -- structured payload: old values, new values, context

    -- provenance (meta-provenance: who wrote this provenance record)
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

-- indexes for common query patterns
create index idx_provenance_entity on provenance_records (entity_type, entity_id);
create index idx_provenance_actor on provenance_records (actor);
create index idx_provenance_action on provenance_records (action);
create index idx_provenance_source on provenance_records (source_system, source_dataset);
create index idx_provenance_time on provenance_records (happened_at desc);
create index idx_provenance_detail on provenance_records using gin (detail);

comment on table provenance_records is
    'Immutable log of data lineage. Every record mutation must generate a provenance entry with source, actor, and timestamp.';

-- =============================================================================
-- audit_log: captures all API and system operations (broader than provenance)
-- =============================================================================
create table audit_log (
    id                  uuid primary key default gen_random_uuid(),
    event_type          text not null,       -- 'api_request', 'login', 'export', 'bulk_import', 'config_change'
    event_action        text not null,       -- 'GET', 'POST', 'PUT', 'DELETE', 'SEARCH', 'EXPORT'
    actor               text not null,       -- user ID, API key reference, or system process
    actor_type          text not null,       -- 'api_user', 'system', 'admin', 'partner_api'
    resource_type       text,                -- 'pharmacy_location', 'contact', 'report', etc.
    resource_id         uuid,                -- specific resource affected (if applicable)
    request_path        text,                -- API endpoint path
    request_method      text,                -- HTTP method
    request_ip          inet,                -- client IP address
    request_user_agent  text,                -- client user agent string
    response_status     integer,             -- HTTP response status code
    duration_ms         integer,             -- request duration in milliseconds
    detail              jsonb,               -- additional context: query params, error messages, etc.
    happened_at         timestamptz not null default now(),

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

-- indexes for audit queries
create index idx_audit_log_actor on audit_log (actor);
create index idx_audit_log_event on audit_log (event_type, event_action);
create index idx_audit_log_resource on audit_log (resource_type, resource_id);
create index idx_audit_log_time on audit_log (happened_at desc);
create index idx_audit_log_ip on audit_log (request_ip);

comment on table audit_log is
    'Comprehensive audit trail for all API requests and system operations. Retained for compliance and forensic analysis.';

-- =============================================================================
-- Helper function: log a provenance record (convenience wrapper)
-- =============================================================================
create or replace function log_provenance(
    p_entity_type text,
    p_entity_id uuid,
    p_action text,
    p_actor text,
    p_actor_type text,
    p_source_system text default null,
    p_source_dataset text default null,
    p_source_record_id text default null,
    p_detail jsonb default null
) returns uuid as $$
declare
    v_id uuid;
begin
    insert into provenance_records (
        entity_type, entity_id, action, actor, actor_type,
        source_system, source_dataset, source_record_id, detail,
        created_by, updated_by
    ) values (
        p_entity_type, p_entity_id, p_action, p_actor, p_actor_type,
        p_source_system, p_source_dataset, p_source_record_id, p_detail,
        p_actor, p_actor
    ) returning id into v_id;

    return v_id;
end;
$$ language plpgsql;

comment on function log_provenance is
    'Convenience function to insert a provenance record. Should be called for every data mutation.';

-- =============================================================================
-- Helper function: log an audit entry (convenience wrapper)
-- =============================================================================
create or replace function log_audit(
    p_event_type text,
    p_event_action text,
    p_actor text,
    p_actor_type text,
    p_resource_type text default null,
    p_resource_id uuid default null,
    p_request_path text default null,
    p_request_method text default null,
    p_request_ip inet default null,
    p_response_status integer default null,
    p_duration_ms integer default null,
    p_detail jsonb default null
) returns uuid as $$
declare
    v_id uuid;
begin
    insert into audit_log (
        event_type, event_action, actor, actor_type,
        resource_type, resource_id,
        request_path, request_method, request_ip,
        response_status, duration_ms, detail,
        created_by, updated_by
    ) values (
        p_event_type, p_event_action, p_actor, p_actor_type,
        p_resource_type, p_resource_id,
        p_request_path, p_request_method, p_request_ip,
        p_response_status, p_duration_ms, p_detail,
        p_actor, p_actor
    ) returning id into v_id;

    return v_id;
end;
$$ language plpgsql;

comment on function log_audit is
    'Convenience function to insert an audit log entry. Called by API middleware on every request.';
