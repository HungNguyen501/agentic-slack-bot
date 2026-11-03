---
name: schedules
always: false
description: Managing scheduled Slack reports — list, add, update or remove cron-based automated questions
---

You can manage automated scheduled questions that the bot posts to Slack channels on a cron schedule.

Each schedule has:
- `id` — UUID; use this to update or remove a specific schedule
- `cron` — cron expression in UTC (e.g. `0 9 * * 1-5` = every weekday at 9 am UTC)
- `channel` — Slack channel ID (e.g. `C1234567890`)
- `question` — the question text the bot will ask automatically on each trigger

## Tools

- `list_schedules` — show all configured schedules
- `add_schedule(cron, channel, question)` — create a new schedule
- `update_schedule(id, cron?, channel?, question?)` — modify one or more fields of an existing schedule; pass only the fields to change
- `remove_schedule(id)` — permanently delete a schedule

## Rules

- Only whitelisted users may use any of these tools. If a non-whitelisted user asks, reply: "You are not authorized to manage schedules."
- Always confirm the action back to the user: show the id and a short summary of what changed.
- When listing, format as a table with columns: ID, Cron, Channel, Question.
- When the user asks to "pause" or "disable" a schedule, interpret that as remove and tell them to re-add it when ready.
- Validate cron expressions have exactly 5 fields before calling add_schedule or update_schedule.
