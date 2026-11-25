-- The Slack user whose access a scheduled question runs with. It is set when the schedule is created and again when
-- someone edits it, so it is always the last admin who decided what the schedule reads. A scheduled channel analysis
-- checks this person on every run (still an admin, still allowed to see the channel). A row without one (every schedule
-- created before this column) runs its question as before but may not read channels for analysis.
--
-- Deploy order: apply this migration BEFORE rolling out the image that reads the column. Every schedules query selects
-- it, so the new scheduler against an unmigrated database fails each tick. The old code ignores the column, so
-- migrating first is safe.

alter table public.schedules
add column if not exists run_as text;
