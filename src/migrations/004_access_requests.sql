-- Submitted data access requests + their review/approval state.
-- Run once against your Supabase database before deploying.

create type access_request_status as enum ('pending', 'approved', 'rejected');

create table if not exists public.access_requests (
    id uuid not null default gen_random_uuid(),
    bot_id text not null references public.bots(id),
    request_type text not null,
    channel text not null,
    thread_ts text not null,
    requester_id text,
    -- slack user ids allowed to approve/reject this request
    reviewers text[] not null default '{}',
    status access_request_status not null default 'pending',
    ticket_id text not null default '',
    user_email text not null,
    principal_type text not null,
    display_name text,
    principal text,
    filter_column text,
    allowed_value text,
    scope_column text,
    scope_value text,
    groups text[] not null default '{}',
    tags text[] not null default '{}',
    service_principal_id text,
    pr_url text,
    -- pending requests older than this can no longer be approved/rejected by anyone
    expires_at timestamp with time zone not null default (now() + interval '24 hours'),
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now(),
    constraint access_requests_pkey primary key (id)
);

create trigger access_requests_updated_at BEFORE
update on access_requests for EACH row
execute FUNCTION _set_updated_at ();
