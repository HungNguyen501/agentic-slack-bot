# /new-bot

Register a new Slack bot in Supabase and wire it into the stack.

## Checklist

### 1. Create Slack App
- Go to api.slack.com/apps → Create New App → From Manifest
- Enable **Event Subscriptions** → Subscribe to `app_mention`
- Enable **OAuth & Permissions** → Add `chat:write`, `app_mentions:read` scopes
- Install the app to the workspace
- Copy **Bot User OAuth Token** (`xoxb-...`), **Signing Secret**, and **App ID**

### 2. Insert Bot into Supabase

The token and signing secret go into Supabase Vault (encrypted at rest); the `bots` row stores only their ids. Vault secret names must be unique, hence the `<bot-id>` in them.

```sql
WITH token AS (SELECT vault.create_secret('xoxb-...', 'bot-<bot-id>-bot-token') AS id),       -- Bot User OAuth Token
     signing AS (SELECT vault.create_secret('<signing-secret>', 'bot-<bot-id>-signing-secret') AS id)  -- from Slack App → Basic Information
INSERT INTO bots (id, bot_token_secret_id, signing_secret_secret_id, enabled_skills, admin_users, app_id, active)
SELECT
  '<bot-id>',                          -- short human-readable slug, e.g. 'data-team'
  token.id,
  signing.id,
  '{}',                                -- empty = all skills enabled; or ARRAY['metadata','jobs']
  ARRAY['<slack-user-id>'],            -- admin Slack user IDs (e.g. 'U08UQ1FG39S')
  '<app-id>',                          -- from Slack App → Basic Information (e.g. 'A08XXXXXX')
  true
FROM token, signing;
```

To rotate later: `SELECT vault.update_secret(bot_token_secret_id, '<new>') FROM bots WHERE id = '<bot-id>';` (same for `signing_secret_secret_id`). No redeploy needed.

### 3. Set Slack Event Subscription URL

After `make compose-up`, get the ngrok URL:
```bash
curl http://localhost:4040/api/tunnels | jq -r '.tunnels[0].public_url'
```

Paste `https://<ngrok-url>/slack/events` into:
**Slack App → Event Subscriptions → Request URL**

Slack will send a challenge request; the receiver will respond automatically.

### 4. Verify

Mention the bot in a Slack channel — it should reply. Check logs:
```bash
docker compose logs -f receiver
docker compose logs -f worker
```

## Notes

- No env vars needed per bot — all config is in Supabase
- `enabled_skills = '{}'` enables all skills; pass a subset to restrict
- Multiple bots can run against the same receiver/worker/scheduler deployment
- Admin users listed in `admin_users` can manage schedules via the bot
- To let this bot handle data access requests (the `request_data_access` tool / `11_data_access_requests.md` skill), also insert a row per request type into `access_request_categories` (`bot_id`, `request_type`, `channel_ids`, `reviewers`) — see `src/migrations/V2__access_request_categories.sql`. Without a matching row, the tool always refuses.
