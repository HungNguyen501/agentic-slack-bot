-- Cached Databricks service-principal OAuth secrets, generated via the
-- generate_service_principal_secret agent tool. Databricks only returns a secret's
-- plaintext value once, at creation — this table lets us honor "return the existing
-- secret if it's still valid" without a second Databricks call, by caching it
-- (encrypted) ourselves. One row per bot+service_account; rotated in place.

create table if not exists public.service_principal_secrets (
    id uuid not null default gen_random_uuid(),
    bot_id text not null references public.bots(id),
    service_account text not null,
    client_id text not null,
    email text not null,
    dbx_secret_id text not null,
    secret_hash text not null,
    -- Fernet-encrypted secret value (see common/crypto.py) — never stored in plaintext.
    secret_encrypted bytea not null,
    status text not null,
    dbx_create_time timestamp with time zone not null,
    dbx_update_time timestamp with time zone not null,
    dbx_expire_time timestamp with time zone not null,
    requested_by text not null,
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now(),
    constraint service_principal_secrets_pkey primary key (id)
);

create trigger service_principal_secrets_updated_at BEFORE
update on service_principal_secrets for EACH row
execute FUNCTION _set_updated_at ();

-- `email` (the bare address, e.g. "braiden.haas@wholesome.co") is the lookup key — it's what
-- the tool's `service_account` argument and find_service_principal_by_email both key off of.
-- `service_account` itself stores Databricks' svc-prefixed display name (e.g. "svc-braiden.haas@wholesome.co").
alter table public.service_principal_secrets
    add constraint service_principal_secrets_bot_id_email_key unique (bot_id, email);
