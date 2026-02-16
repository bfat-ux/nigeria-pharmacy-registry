-- =============================================================================
-- 002_status_history.sql
-- Append-only validation status history
-- Status changes are NEVER in-place updates — always appended here.
-- =============================================================================

-- =============================================================================
-- validation_status_history: immutable record of every validation level change
-- =============================================================================
create table validation_status_history (
    id                  uuid primary key default gen_random_uuid(),
    pharmacy_id         uuid not null references pharmacy_locations(id) on delete cascade,
    old_level           validation_level,    -- null for initial assignment
    new_level           validation_level not null,
    changed_at          timestamptz not null default now(),
    changed_by          text not null,       -- actor: 'system', user ID, 'pcn_sync', etc.
    actor_type          text not null,       -- 'system', 'field_agent', 'partner_api', 'regulator_sync'

    -- evidence and rationale
    evidence_reference  text,                -- URI or ID pointing to evidence record
    source_description  text,                -- human-readable explanation
    evidence_detail     jsonb,               -- structured evidence metadata

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

-- index for efficient latest-status lookups
create index idx_status_history_pharmacy on validation_status_history (pharmacy_id, changed_at desc);
create index idx_status_history_level on validation_status_history (new_level);
create index idx_status_history_actor_type on validation_status_history (actor_type);

comment on table validation_status_history is
    'Append-only history of validation level transitions. The current level for a pharmacy is the new_level of its most recent entry.';

-- =============================================================================
-- View: current validation status per pharmacy (convenience view)
-- =============================================================================
create view current_validation_status as
select distinct on (pharmacy_id)
    pharmacy_id,
    new_level as current_level,
    changed_at as level_since,
    changed_by,
    actor_type,
    evidence_reference,
    source_description
from validation_status_history
order by pharmacy_id, changed_at desc;

comment on view current_validation_status is
    'Derived view showing the latest validation level for each pharmacy. Use this instead of querying status_history directly.';

-- =============================================================================
-- Function: record a validation level change
-- Validates transition rules and updates the pharmacy_locations convenience column.
-- =============================================================================
create or replace function record_validation_change(
    p_pharmacy_id uuid,
    p_new_level validation_level,
    p_changed_by text,
    p_actor_type text,
    p_evidence_reference text default null,
    p_source_description text default null,
    p_evidence_detail jsonb default null
) returns uuid as $$
declare
    v_current_level validation_level;
    v_history_id uuid;
begin
    -- get current level
    select current_validation_level into v_current_level
    from pharmacy_locations
    where id = p_pharmacy_id;

    if not found then
        raise exception 'Pharmacy % not found', p_pharmacy_id;
    end if;

    -- insert history record
    insert into validation_status_history (
        pharmacy_id, old_level, new_level, changed_by, actor_type,
        evidence_reference, source_description, evidence_detail,
        created_by, updated_by
    ) values (
        p_pharmacy_id, v_current_level, p_new_level, p_changed_by, p_actor_type,
        p_evidence_reference, p_source_description, p_evidence_detail,
        p_changed_by, p_changed_by
    ) returning id into v_history_id;

    -- update convenience column on pharmacy_locations
    update pharmacy_locations
    set current_validation_level = p_new_level,
        updated_at = now(),
        updated_by = p_changed_by
    where id = p_pharmacy_id;

    return v_history_id;
end;
$$ language plpgsql;

comment on function record_validation_change is
    'Records a validation level transition. Updates both the history table and the convenience column on pharmacy_locations.';

-- =============================================================================
-- operational_status_history: track changes to operational status
-- =============================================================================
create table operational_status_history (
    id                  uuid primary key default gen_random_uuid(),
    pharmacy_id         uuid not null references pharmacy_locations(id) on delete cascade,
    old_status          operational_status,
    new_status          operational_status not null,
    changed_at          timestamptz not null default now(),
    changed_by          text not null,
    reason              text,
    source_description  text,

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

create index idx_op_status_history_pharmacy on operational_status_history (pharmacy_id, changed_at desc);

comment on table operational_status_history is
    'Append-only history of operational status changes (open, closed, etc.).';
