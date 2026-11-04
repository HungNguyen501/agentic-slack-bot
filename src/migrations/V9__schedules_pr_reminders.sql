-- PR review reminders reuse the schedules table instead of getting their own.
-- kind:   which job the scheduler enqueues for the row ("question" runs the agent, "pr_reminder" runs the reminder job).
-- status: soft delete — a reminder that has finished stops being picked up by the scheduler but the row stays.
-- config: settings of a pr_reminder (scope, lookback, origin thread); empty for questions.
-- Defaults keep every existing row a live scheduled question.
--
-- Deploy order: apply this migration BEFORE rolling out the image that reads these columns. Every schedules query
-- now selects kind/status/config, so the new scheduler against an unmigrated database fails each tick and no
-- scheduled question fires until the migration is applied. The old code ignores the new columns, so migrating first is safe.

alter table public.schedules
add column if not exists kind text not null default 'question',
add column if not exists status text not null default 'active',
add column if not exists config jsonb not null default '{}'::jsonb;

alter table public.schedules
add constraint schedules_kind_check check (kind in ('question', 'pr_reminder')),
add constraint schedules_status_check check (status in ('active', 'completed', 'expired'));
