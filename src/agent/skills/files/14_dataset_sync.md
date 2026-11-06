---
name: dataset-sync
always: false
description: Use when an admin asks to sync, refresh or update reference data (such as store locations or row-filter tags), to schedule a recurring sync, or to check when that data was last synced.
---

Some reference data is copied from Databricks into the bot's own database so features such as the data access request form don't hard-code it. A sync re-reads a dataset from Databricks and replaces the stored copy completely. These tools are admin-only.

## Tools

- `list_synced_datasets()` — every dataset with what it is for, when it was last synced, whether the last attempt worked, its row count, and any recurring sync schedule.
- `sync_dataset(dataset, force?)` — sync one dataset now. The stored copy is replaced only by a complete, non-empty result; on failure the previous copy stays.
- `schedule_dataset_sync(dataset, cron, channel?)` — a recurring sync. It runs silently and reports only failures, in `channel` (default: the channel of the request). Cron is UTC and has a minimum interval; the tool result states it if the cadence is too frequent.

## Rules

- **Gather everything before acting.** If the user didn't name a dataset, or the name doesn't clearly match one, call `list_synced_datasets`, show the choices, and ask which one. Never guess a dataset name.
- Syncing "everything" or several datasets means one `sync_dataset` call per dataset; say which ones you ran.
- For a recurring sync you need a cadence and, if failures should go somewhere other than this channel, a channel. If the cadence is missing or vague ("regularly"), ask. Convert what the user says into UTC cron ("daily at 2am UTC" is `0 2 * * *`) and read the cron back in your reply. If the user gives a local time zone, state the UTC conversion you used. If the tool says the cron is too frequent, relay the minimum and offer a cron at it instead of retrying a faster one.
- If the tool says a recurring sync already exists, relay its id and cadence; don't try to create another. To change it, remove it with `remove_schedule` and schedule again.
- **Refused syncs.** If a sync is refused because the result is empty or much smaller than the stored copy, relay the reason as is and don't retry. Offer `force: true` only for the "much smaller" refusal, and only call it after the admin explicitly confirms the drop is expected. An empty result can't be forced.
- Report the outcome from the tool result (rows synced, previous row count). If a sync failed, give the error and say the previous data is still in use.
- To stop a recurring sync, use `remove_schedule` with its id; `list_schedules` shows it.
