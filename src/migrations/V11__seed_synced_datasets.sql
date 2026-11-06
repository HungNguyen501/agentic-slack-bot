-- The two datasets behind the data access request form's dropdowns. Run each once (ask the bot to sync it) after this
-- migration, since the form refuses to open until both have a snapshot; schedule them to keep them fresh.

insert into public.synced_datasets (dataset, description, source_sql)
values
(
    'store_locations',
    'Companies and their store locations (and location market codes); feeds the data access request form''s allowed value and scope value dropdowns.',
    $sql$
select company_key, company, location_market, location, id as location_id
from vireox.ai_semantic.unified_dim_store_locations
$sql$
),
(
    'row_filter_tags',
    'Tag names used by the row filters in Unity Catalog; feeds the data access request form''s tags dropdown.',
    $sql$
select explode(collect_set(regexp_replace(try_element_at(split(target_columns, ','), 4), "[ '\" ]", ""))) as tag_name
from system.information_schema.row_filters a
$sql$
)
on conflict (dataset) do nothing;
