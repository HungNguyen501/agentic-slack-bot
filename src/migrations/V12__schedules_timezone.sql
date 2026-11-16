-- The timezone a schedule's cron is written in (an IANA name such as 'Asia/Ho_Chi_Minh').
-- The default keeps every existing row evaluated in UTC, exactly as before.
--
-- Deploy order: apply this migration BEFORE rolling out the image that reads the column. Every schedules query
-- now selects it, so the new scheduler against an unmigrated database fails each tick and nothing fires until the
-- migration is applied. The old code ignores the column, so migrating first is safe.

alter table public.schedules
add column if not exists timezone text not null default 'UTC';
