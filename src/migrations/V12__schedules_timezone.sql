-- The timezone a schedule's cron is written in (an IANA name such as 'Asia/Ho_Chi_Minh').
-- The default keeps every existing row evaluated in UTC, exactly as before.
--
-- PR reminders also stop checking the clock in code: when one may post is now part of its cron. Until now every
-- reminder was held to 07:00-21:59 Vietnam time (UTC+7) by that check, whatever its cron said, so an existing
-- reminder such as '*/30 * * * *' only ever posted by day. Left alone, it would start pinging at night as soon as the
-- code is deployed. The second half of this migration therefore writes the old restriction into each active
-- reminder's own cron: its hour field is cut down to UTC hours 0-14 (07:00-21:59 in Vietnam), which keeps what it
-- could fire on before and drops only what the old check silently skipped. A reminder that could never fire inside
-- that window is marked 'completed', since it never posted anything anyway. An hour field that cannot be read falls
-- back to the whole window rather than being left unrestricted.
--
-- Deploy order: apply this migration BEFORE rolling out the image that reads the column. Every schedules query
-- now selects it, so the new scheduler against an unmigrated database fails each tick and nothing fires until the
-- migration is applied. The old code ignores the column, so migrating first is safe. Running the migration before
-- the new code also keeps the old check in force until the crons carry the restriction themselves.

alter table public.schedules
add column if not exists timezone text not null default 'UTC';

do $$
declare
    reminder record;
    fields text[];
    item text;
    base text;
    has_step boolean;
    step int;
    first_hour int;
    last_hour int;
    span int;
    hour int;
    allowed int[];
    kept int[];
    new_hours text;
begin
    for reminder in select id, cron from public.schedules where kind = 'pr_reminder' and status = 'active' loop
        fields := regexp_split_to_array(btrim(reminder.cron), '\s+');
        allowed := '{}';
        kept := '{}';

        begin
            -- Expand the hour field (lists, ranges, steps, '*') into the hours of the day it fires on. A range that
            -- wraps past midnight (22-2) is allowed, as in the scheduler's cron library.
            foreach item in array string_to_array(fields[2], ',') loop
                has_step := position('/' in item) > 0;
                step := case when has_step then split_part(item, '/', 2)::int else 1 end;
                base := split_part(item, '/', 1);
                if base = '*' then
                    first_hour := 0;
                    last_hour := 23;
                elsif position('-' in base) > 0 then
                    first_hour := split_part(base, '-', 1)::int;
                    last_hour := split_part(base, '-', 2)::int;
                else
                    first_hour := base::int;
                    last_hour := case when has_step then 23 else first_hour end;
                end if;
                if step < 1 or first_hour not between 0 and 23 or last_hour not between 0 and 23 then
                    raise exception 'hour out of range';
                end if;
                span := (last_hour - first_hour + 24) % 24;
                for hour in 0..23 loop
                    if (hour - first_hour + 24) % 24 <= span and (hour - first_hour + 24) % 24 % step = 0 then
                        allowed := allowed || hour;
                    end if;
                end loop;
            end loop;
        exception when others then
            raise notice 'PR reminder % has an hour field that could not be read (%); restricting it to the old window', reminder.id, reminder.cron;
            allowed := array(select generate_series(0, 23));
        end;

        select coalesce(array_agg(distinct h order by h), '{}') into allowed from unnest(allowed) as h;
        select coalesce(array_agg(h order by h), '{}') into kept from unnest(allowed) as h where h <= 14;

        if cardinality(kept) = 0 then
            update public.schedules set status = 'completed' where id = reminder.id;
        elsif cardinality(kept) < cardinality(allowed) then
            new_hours := case when cardinality(kept) = 15 then '0-14' else array_to_string(kept, ',') end;
            fields[2] := new_hours;
            update public.schedules set cron = array_to_string(fields, ' ') where id = reminder.id;
        end if;
    end loop;
end
$$;
