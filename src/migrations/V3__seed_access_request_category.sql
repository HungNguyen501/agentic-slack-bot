-- Seed the initial access request category for the data-nerd bot. Skipped when the bot isn't registered yet
-- (fresh database): the foreign key to public.bots would otherwise fail the migration.

insert into public.access_request_categories (bot_id, request_type, channel_ids, reviewers)
select
    'data-nerd' as bot_id,
    'table_row_filter_access' as request_type,
    array['C09HDPPSR40'] as channel_ids,
    array['U08UQ1FG39S'] as reviewers
where exists (select 1 from public.bots where id = 'data-nerd');
