-- Drop the plaintext Slack credentials from public.bots. They now live only in Supabase Vault
-- (see V7), referenced by bot_token_secret_id / signing_secret_secret_id.
--
-- Apply only once every service runs code that reads secrets from Vault; anything still
-- selecting these columns will fail after this migration.

alter table public.bots
    drop column bot_token,
    drop column signing_secret;
