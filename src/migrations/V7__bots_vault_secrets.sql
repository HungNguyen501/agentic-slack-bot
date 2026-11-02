-- Move bots.bot_token / bots.signing_secret into Supabase Vault so they're encrypted at rest
-- (dumps and backups of public.bots no longer contain them in the clear). The key is managed
-- by Vault, not by us, so admins keep rotating secrets in plain SQL via vault.update_secret().
--
-- This migration is additive: the plaintext columns stay (now nullable) so code that predates
-- the Vault read path keeps working until it's redeployed. Drop them in a follow-up migration
-- once every service runs the new code.

create extension if not exists supabase_vault;

alter table public.bots
    add column if not exists bot_token_secret_id uuid,
    add column if not exists signing_secret_secret_id uuid,
    alter column bot_token drop not null,
    alter column signing_secret drop not null;

-- No FK: vault.secrets is owned by supabase_vault_admin, which public can't reference.
-- connectors/db/bots.py inner-joins vault.decrypted_secrets, so a dangling id fails closed
-- (the bot simply isn't found) rather than falling back to anything.
do $$
declare
    r record;
begin
    for r in
        select id, bot_token, signing_secret
        from public.bots
        where bot_token_secret_id is null or signing_secret_secret_id is null
    loop
        update public.bots
        set
            bot_token_secret_id = coalesce(
                bot_token_secret_id,
                vault.create_secret(r.bot_token, 'bot-' || r.id || '-bot-token', 'Slack bot token for bot ' || r.id)
            ),
            signing_secret_secret_id = coalesce(
                signing_secret_secret_id,
                vault.create_secret(r.signing_secret, 'bot-' || r.id || '-signing-secret', 'Slack signing secret for bot ' || r.id)
            )
        where id = r.id;
    end loop;
end
$$;

alter table public.bots
    alter column bot_token_secret_id set not null,
    alter column signing_secret_secret_id set not null;
