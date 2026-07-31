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

```sql
INSERT INTO bots (id, bot_token, signing_secret, enabled_skills, admin_users, app_id, active)
VALUES (
  '<bot-id>',                          -- short human-readable slug, e.g. 'data-team'
  'xoxb-...',                          -- Bot User OAuth Token
  '<signing-secret>',                  -- from Slack App → Basic Information
  '{}',                                -- empty = all skills enabled; or ARRAY['metadata','jobs']
  ARRAY['<slack-user-id>'],            -- admin Slack user IDs (e.g. 'U08UQ1FG39S')
  '<app-id>',                          -- from Slack App → Basic Information (e.g. 'A08XXXXXX')
  true
);
```

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
