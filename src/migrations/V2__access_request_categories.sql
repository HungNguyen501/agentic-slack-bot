-- Data access request categories: gates the request_data_access agent tool.

create table if not exists public.access_request_categories (
    id uuid not null default gen_random_uuid(),
    bot_id text not null references public.bots(id),
    request_type text not null,
    -- slack channel ids where this request type may be requested
    channel_ids text[] not null default '{}',
    -- slack user ids who should review submissions of this type
    reviewers text[] not null default '{}',
    created_at timestamp with time zone not null default now(),
    updated_at timestamp with time zone not null default now(),
    constraint access_request_categories_pkey primary key (id)
);

create trigger access_request_categories_updated_at BEFORE
update on access_request_categories for EACH row
execute FUNCTION _set_updated_at ();

alter table public.access_request_categories
    add constraint access_request_categories_bot_id_request_type_key unique (bot_id, request_type);
    