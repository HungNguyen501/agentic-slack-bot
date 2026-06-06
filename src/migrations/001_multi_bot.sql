-- Multi-bot support: bot registry + bot_id column on schedules
-- Run once against your Supabase database before deploying.

create table if not exists public.bots (
    id         text        primary key,
    bot_token      text        not null,
    signing_secret text        not null,
    -- empty array means "all skills enabled" (matches single-bot default)
    enabled_skills text[]      not null default '{}',
    -- slack user ids allowed to manage schedules for this bot
    admin_users    text[]      not null default '{}',
    -- slack app id used by the receiver to resolve the bot
    app_id   text,
    active         boolean     not null default true,
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now()
);

create trigger bots_updated_at BEFORE
update on bots for EACH row
execute FUNCTION _set_updated_at ();

create table if not exists public.schedules (
  id uuid not null default gen_random_uuid (),
  bot_id text null,
  cron text not null,
  channel text not null,
  question text not null,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint schedules_pkey primary key (id)
) TABLESPACE pg_default;

create trigger schedules_updated_at BEFORE
update on schedules for EACH row
execute FUNCTION _set_updated_at ();