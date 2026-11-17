-- Agent skills (src/agent/skills/files/*.md) copied into Supabase so a skill edit reaches running services without a
-- new image: the loader reads only this table, on every request (an empty table fails the request, so sync before relying on it).
-- Git stays the source of truth; `make skills-sync` (run by CI on pushes to master) replaces the whole table from the files.
-- Skills are workspace-wide, not per bot; a bot's enabled_skills allowlist still gates which of them it can use.
-- name: unique skill name from the frontmatter, the key used by bots.enabled_skills.
-- filename: the source file's basename (NN_name.md); the number sets the load order.

create table if not exists public.agent_skills (
    name text not null, -- noqa: RF04
    filename text not null,
    always boolean not null default false, -- noqa: RF04
    description text not null default '',
    body text not null,
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now(),
    constraint agent_skills_pkey primary key (name)
);

create trigger agent_skills_updated_at before
update on public.agent_skills for each row
execute function _set_updated_at();
