---
name: data-access-requests
always: false
description: Use when the user asks to request, get, or apply for data access, a new row filter, scope permission, or wants to fill out an access request form.
---

You can start a data access request on behalf of the current user. This does not require the user to be whitelisted — any user may request access.

Only one request type is currently configured: `table_row_filter_access`.

## Tools

- `request_data_access(request_type)` — checks whether this request type can be requested in the current channel, and if so, posts a button in the thread that opens an interactive form. The form itself collects ticket_id, user_emails (one or more, comma/newline separated — one access request is created per email), filter_column, allowed_value, scope_column, scope_value, principal_type, groups, and tags — do not ask the user for these fields yourself.

## Rules

- Call `request_data_access` with `request_type: "table_row_filter_access"` as soon as the user's intent is clear — don't collect form fields in the chat first.
- If the tool reports the channel isn't eligible, relay that refusal politely and don't retry with a different channel or request_type.
- After the tool posts the form button, tell the user to click it; don't repeat the form fields in your reply since they'll see them in the modal.
