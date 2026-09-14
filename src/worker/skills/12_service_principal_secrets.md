---
name: service-principal-secrets
always: false
description: Use when a reviewer asks to generate, rotate, or get a Databricks service-principal (service account) OAuth client secret.
---

You can generate a Databricks service-principal OAuth client secret on behalf of the current user. Unlike data access requests, this is restricted — only users configured as reviewers for the `service_principal_secret` request type, in an eligible channel, may do this.

## Tools

- `generate_service_principal_secret(service_account)` — checks reviewer/channel eligibility, verifies the service principal exists, generates (or reuses a still-valid cached) secret, and returns a one-time view link. The link shows the full record (`id`, `secret`, `secret_hash`, `status`, `create_time`, `update_time`, `expire_time`, `service_account`, `client_id`, `email`) exactly as Databricks returns it. It works exactly once; after it's opened, everything is gone.

## Rules

- `service_account` is the bare email address identifying the service principal (e.g. `braiden.haas@wholesome.co`), not the `svc-`-prefixed display name.
- Call the tool as soon as the service account is clear — don't ask the user to confirm first.
- If the tool reports the requester or channel isn't eligible, that the service account isn't a valid email address, or that no matching service principal exists, relay that refusal verbatim and don't retry.
- Never repeat or guess at the secret value yourself — only ever relay the one-time link the tool returns. Tell the user the link only works once.
