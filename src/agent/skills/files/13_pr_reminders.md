---
name: pr-reminders
always: false
description: Use when the user asks to check whether pull requests are approved, to remind reviewers about PRs, or to schedule recurring PR review reminders.
---

You can check whether GitHub pull requests shared in Slack are approved and remind the channel about the ones still waiting for review. The reminder is posted by the tool itself, as a reply in the Slack thread where each PR link was shared, and it pings `@channel`.

## Tools

- `check_pr_approvals(scope, lookback_hours?, pr_urls?)` — one-off check, open to any user. It runs at any time of day.
- `schedule_pr_reminder(cron, scope, lookback_hours?, channel?)` — recurring check, admin-only (the same rule as the schedule tools). It re-checks on every cron tick, reminds in the thread of each PR still waiting for review, and stops by itself once no PR is waiting or after its lifetime. It checks once right away, at any time of day, and schedules nothing if nothing is waiting. Its scheduled ticks are only sent during fixed active hours; the tool description and the tool result state the hours, the minimum cadence and the lifetime, so read them there rather than assuming numbers.

## Rules

- `scope: "thread"` checks the PR links in the thread the request was made in, plus any link typed in the request. Use it when the user says "this PR" or asks inside a thread that holds PR links. Put links typed in the request into `pr_urls`.
- `scope: "channel"` checks PR links shared in the channel over the last `lookback_hours` ("3d" is 72, "1w" is 168; leave it out for the default). Use it for "PRs in this channel".
- A recurring reminder for a whole channel uses `scope: "channel"`. A thread-scope recurring reminder only works in the channel and thread of the request.
- People named in the request (`@alice @bob`) are not pinged individually. The reminder pings `@channel`. If the user named people, say so in one short sentence after the result; don't ask them to retry.
- Use `schedule_pr_reminder`, never `add_schedule`, for recurring PR reminders. Cron is UTC. Convert the user's cadence ("every 30m" is `*/30 * * * *`, "hourly" is `0 * * * *`) and pass it on; the tool rejects a cadence that is too frequent. When it does, relay its message, which names the minimum, and offer a cron at that minimum instead of retrying a faster one.
- When you schedule a reminder, repeat the active hours from the tool result so the admin knows when reminders are sent.
- The tools post the reminders; don't repeat the PR list in your reply. Confirm briefly using the tool's result (how many PRs, how many threads) and, for a schedule, give the schedule id.
- To stop a recurring reminder early, use `remove_schedule` with its id; `list_schedules` shows active reminders.
- If a tool says it can't read the channel or is missing a scope, relay that message as is.
