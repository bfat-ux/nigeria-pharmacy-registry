-- =============================================================================
-- 006_verification_tasks.sql
-- Verification queue: task assignment, lifecycle tracking, and batch generation
-- for progressing pharmacies through the validation ladder.
-- =============================================================================

-- task_status enum: lifecycle states for a verification task
create type task_status as enum (
    'pending',
    'assigned',
    'in_progress',
    'completed',
    'failed',
    'skipped'
);

-- task_type enum: the kind of verification work to be performed
create type task_type as enum (
    'verify_L1',
    'verify_L2',
    'verify_L3',
    'verify_L4',
    'reverify_L1',
    'reverify_L2',
    'reverify_L3'
);

-- =============================================================================
-- verification_tasks: the queue of verification work items
-- =============================================================================
create table verification_tasks (
    id                  uuid primary key default gen_random_uuid(),
    pharmacy_id         uuid not null references pharmacy_locations(id) on delete cascade,
    task_type           task_type not null,
    target_level        validation_level not null,
    status              task_status not null default 'pending',
    priority            integer not null default 3
                        constraint chk_priority check (priority between 1 and 5),

    -- assignment
    assigned_to         text,                -- actor ID of the assigned agent
    assigned_at         timestamptz,

    -- completion
    completed_at        timestamptz,
    result_detail       jsonb,               -- structured outcome/evidence metadata

    -- scheduling
    due_date            date,
    attempt_count       integer not null default 1,
    max_attempts        integer not null default 3,

    -- notes
    notes               text,

    -- provenance
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          text not null,
    updated_by          text not null
);

-- Prevent duplicate active tasks for the same pharmacy + target level.
-- "Active" means status is not in a terminal state.
create unique index uq_active_task_pharmacy_target
    on verification_tasks (pharmacy_id, target_level)
    where status not in ('completed', 'failed', 'skipped');

-- Queue query: fetch pending/assigned tasks ordered by priority
create index idx_vt_queue
    on verification_tasks (status, priority, created_at)
    where status in ('pending', 'assigned', 'in_progress');

-- Assignee lookup: "my tasks"
create index idx_vt_assignee
    on verification_tasks (assigned_to, status)
    where assigned_to is not null;

-- Pharmacy lookup: "tasks for this pharmacy"
create index idx_vt_pharmacy
    on verification_tasks (pharmacy_id, status);

-- Target level + status: for stats aggregation
create index idx_vt_level_status
    on verification_tasks (target_level, status);

-- Due date: for overdue task queries
create index idx_vt_due_date
    on verification_tasks (due_date)
    where status in ('pending', 'assigned', 'in_progress') and due_date is not null;

comment on table verification_tasks is
    'Queue of verification tasks for progressing pharmacies through the validation ladder. Each task represents one verification attempt targeting a specific level.';

comment on column verification_tasks.priority is
    'Task priority: 1 = highest (urgent), 5 = lowest (routine). Used for queue ordering.';

comment on column verification_tasks.attempt_count is
    'Which attempt this task represents (1 = first, incremented on reschedule after skip/fail).';

comment on column verification_tasks.result_detail is
    'Structured JSON capturing the outcome: evidence details, failure reasons, skip reasons.';
