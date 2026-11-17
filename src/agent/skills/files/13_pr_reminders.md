---
name: pr-reminders
always: false
description: Use when the user asks to check whether pull requests are approved, to remind reviewers about PRs, or to schedule recurring PR review reminders.
---

You can check whether GitHub pull requests shared in Slack are approved and remind people about the ones still waiting for review. The reminder is posted by the tool itself, as a reply in the Slack thread where each PR link was shared, and it @mentions the people you pass in `mentions`.

## Tools

- `check_pr_approvals(scope, mentions, lookback_hours?, pr_urls?)` — one-off check, open to any user. It runs at any time of day.
- Both tools also react on the Slack message of each finished PR, once: :white_check_mark: when its PRs are approved or merged, :x: when they were closed without merging (a message that already has either emoji is skipped). You don't call anything for this; if the result says it marked messages, say so in a few words.
- `schedule_pr_reminder(cron, scope, mentions, lookback_hours?, channel?, expires_in_hours?, timezone?)` — recurring check, admin-only (the same rule as the schedule tools). It re-checks on every cron tick and reminds in the thread of each PR still waiting for review. If the current time is inside the hours and days of the cron it checks once right away; otherwise the first check waits for the first tick. It is kept even if nothing is waiting yet, since a PR can be shared later. It runs until an admin removes it, and never stops just because nothing is waiting. Pass `expires_in_hours` only when the user explicitly asks it to stop after a time ("for 72h", "for 3 days"); a lookback window ("PRs in the last 3d") is not an expiry. The cron is the only thing that decides when reminders go out: there is no separate sending-hours rule, so the hours and days you put in the cron are the ones that apply.

## Rules

- `scope: "thread"` checks the PR links in the thread the request was made in, plus any link typed in the request. Use it when the user says "this PR" or asks inside a thread that holds PR links. Put links typed in the request into `pr_urls`.
- `scope: "channel"` checks PR links shared in the channel over the last `lookback_hours` ("3d" is 72, "1w" is 168; leave it out for the default). Use it for "PRs in this channel".
- A recurring reminder for a whole channel uses `scope: "channel"`. A thread-scope recurring reminder only works in the channel and thread of the request.
- **Who to remind.** Pass the people the user @mentioned in the request as `mentions`: their Slack user IDs, from the `<@U123>` tokens in the message. Never include the bot's own mention, and never invent IDs. If the user named nobody, ask who should be reminded and wait for the answer: don't call the tool without `mentions`. If the tool says a mention isn't valid (a channel, `@here` or a user group), relay that and ask for individual people.
- Use `schedule_pr_reminder`, never `add_schedule`, for recurring PR reminders.
- **Writing the cron.** The cron is written in the reminder's timezone, which is Vietnam time (`Asia/Ho_Chi_Minh`) unless the user names another one: then pass `timezone` (an IANA name such as `Asia/Singapore` or `UTC`) and write the cron in that zone's local time. Never convert to UTC yourself; the scheduler reads the cron in the timezone you pass.
- **Hours and days are part of the cron.** Unless the user says otherwise, keep reminders to working time: weekdays (Monday to Friday, `1-5` in the last field) and 08:00-17:59 local time, so nobody is pinged at night or at the weekend. "every 30m" is `*/30 8-17 * * 1-5`, "hourly" is `0 8-17 * * 1-5`, "every 2 hours" is `0 8-17/2 * * 1-5`. Always put `1-5` in the day-of-week field unless the user explicitly asks for weekends or every day ("also at weekends", "every day", "7 days a week", "around the clock"); a cadence with no days named ("every 30m", "hourly") is not such a request. If the user names the hours or days ("9am to 6pm", "Saturdays too"), write exactly those. The minimum cadence is 30 minutes; if the tool rejects the cron as too frequent, relay its message and offer a cron at the minimum instead of retrying a faster one.
- When you schedule a reminder, say in plain words when it runs (for example "every 30 minutes, 08:00-17:59 Vietnam time, Monday to Friday") so the admin can catch a wrong cron, and give the cron and the timezone from the tool result.
- If a result says the check was incomplete (PRs unchecked, unreadable, or the channel scan cut short), relay that and don't tell the user nothing is waiting.
- Don't write `<@…>` mentions or user IDs in your own reply (it would ping the same people again): say how many people were reminded.
- The tools post the reminders; don't repeat the PR list in your reply. Confirm briefly using the tool's result (how many PRs, how many threads) and, for a schedule, give the schedule id.
- If the tool says the reminder already exists, relay the existing schedule id and don't try to create it again: repeating a request never makes a second one. Offer to remove it and recreate it if the user wants a different schedule.
- To stop a recurring reminder early, use `remove_schedule` with its id; `list_schedules` shows active reminders.
- If a tool says it can't read the channel or is missing a scope, relay that message as is.
