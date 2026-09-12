-- Seed the initial access request category for the data-nerd bot.

insert into public.access_request_categories (bot_id, request_type, channel_ids, reviewers)
values ('data-nerd', 'table_row_filter_access', array['C09HDPPSR40'], array['U08UQ1FG39S']);
