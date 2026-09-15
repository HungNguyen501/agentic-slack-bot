---
name: service-principal-secrets
always: false
description: Use when a reviewer asks to generate, rotate, or get a Databricks service-principal (service account) OAuth client secret.
---

You can generate a Databricks service-principal OAuth client secret on behalf of the current user. Unlike data access requests, this is restricted — only users configured as reviewers for the `service_principal_secret` request type, in an eligible channel, may do this.

## Scope

This tool is for GPT-user service principals only — the kind created by an approved `request_data_access` submission (principal type "Service principals"), named `svc-<email>` in Databricks. It only mints/reuses a secret for a service principal that already exists under that convention; it never creates one. It is not for pre-existing Databricks service accounts used by scheduled jobs/pipelines that were never created through this bot — those are out of scope, and the answer for those is to reach out to the data team directly, not to keep retrying this tool with variations of the address.

## Tools

- `generate_service_principal_secret(service_account)` — checks reviewer/channel eligibility, verifies the service principal exists, generates (or reuses a still-valid cached) secret, and returns a one-time view link. The link shows the full record (`id`, `secret`, `secret_hash`, `status`, `create_time`, `update_time`, `expire_time`, `service_account`, `client_id`, `email`) exactly as Databricks returns it. It works exactly once; after it's opened, everything is gone.

## Rules

- `service_account` is the bare email address identifying the service principal (e.g. `braiden.haas@wholesome.co`), not the `svc-`-prefixed display name.
- Call the tool as soon as the service account is clear — don't ask the user to confirm first.
- If the tool reports the requester or channel isn't eligible, that the service account isn't a valid email address, or that no matching service principal exists, relay that refusal verbatim (it already includes next-step guidance) and don't retry.
- Never repeat or guess at the secret value yourself — only ever relay the one-time link the tool returns. Tell the user the link only works once.
