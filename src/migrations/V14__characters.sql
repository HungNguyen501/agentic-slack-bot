-- Characters: voices the bot speaks in, bound to the features and skills they speak for. Workspace-wide, not per bot
-- (like agent_skills and synced_datasets).
-- The seed files in src/characters/files/ fill this table once (the admin asks the bot to sync characters); after that
-- the table is the source of truth and admins add, change, disable and remove rows here. Every use re-validates a row
-- (characters/core/validation.py), so a bad edit is skipped by the bot and flagged by list_characters.
-- name: slug, also what is stored per thread in Redis so a thread keeps one character.
-- display_name / icon_emoji / icon_url: the sender name and icon a message is posted with (needs Slack's chat:write.customize).
-- bindings: feature:<name> targets (messages code posts from templates, see voices) and skill:<name> targets (agent
-- replies, see persona). skill:core is the default voice of every agent reply.
-- persona: text appended to the system prompt of an agent reply.
-- voices: one block per feature, e.g. {"pr_reminder": {"headers": ["{who} ..."]}}.

create table if not exists public.characters (
    name text not null, -- noqa: RF04
    display_name text not null,
    song text not null default '',
    artist text not null default '',
    enabled boolean not null default true,
    bindings text[] not null default '{}',
    icon_emoji text,
    icon_url text,
    persona text not null default '',
    voices jsonb not null default '{}'::jsonb,
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now(),
    constraint characters_pkey primary key (name)
);

create trigger characters_updated_at before
update on public.characters for each row
execute function _set_updated_at();
