-- Reference data copied from Databricks into Supabase on a schedule (or on demand), so consumers such as the data access
-- request form read it from our own database instead of hard-coding it or querying Databricks live.
-- One row per dataset: the registry entry (dataset name, query, status) and its latest snapshot live together, and a sync
-- replaces the snapshot with a single UPDATE, so a reader never sees a half-written dataset.
-- source_sql: a read-only Databricks SQL statement; the sync refuses anything that isn't a SELECT/WITH.
-- column_names / synced_rows: the columns and rows of the last successful sync (values are strings, NULL stays NULL).
-- last_status: 'ok' or 'error'; a failed sync leaves the previous snapshot in place and only updates last_status / last_error.
-- Datasets are workspace-wide, not per bot (like service_principal_secrets).

create table if not exists public.synced_datasets (
    dataset text not null,
    description text not null default '',
    source_sql text not null,
    enabled boolean not null default true,
    column_names text[] not null default '{}',
    synced_rows jsonb not null default '[]'::jsonb,
    row_count integer not null default 0,
    last_synced_at timestamp with time zone,
    last_status text,
    last_error text,
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now(),
    constraint synced_datasets_pkey primary key (dataset),
    constraint synced_datasets_last_status_check check (last_status in ('ok', 'error'))
);

create trigger synced_datasets_updated_at before
update on public.synced_datasets for each row
execute function _set_updated_at();

-- Scheduled syncs reuse the schedules table: kind 'dataset_sync' holds {"dataset": <name>} in config, and channel is
-- where a failed run is reported.
alter table public.schedules
drop constraint if exists schedules_kind_check,
add constraint schedules_kind_check check (kind in ('question', 'pr_reminder', 'dataset_sync'));
