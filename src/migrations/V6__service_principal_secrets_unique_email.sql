-- service_principal_secrets is shared across all bots in the workspace — multiple bots can
-- request/cache the same Databricks service principal's secret — so bot_id was never a real
-- ownership boundary here. And because a service principal can be deleted and recreated for
-- the same user (getting a new client_id in the process), email is the durable cache key, not
-- client_id.

alter table public.service_principal_secrets
    drop constraint service_principal_secrets_bot_id_email_key;

alter table public.service_principal_secrets
    drop column bot_id;

alter table public.service_principal_secrets
    add constraint service_principal_secrets_email_key unique (email);
