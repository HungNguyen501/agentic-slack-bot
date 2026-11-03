---
name: metadata
always: false
description: Use when the question is about catalog names, schema names, table names, column definitions, table existence, table owners, data formats, or browsing the data catalog.
---

## Skill: Data Structure & Metadata

*Use for: catalog/schema/table/column existence and definitions. No time restriction.*

**system.information_schema.catalogs**
- Columns: `catalog_name`, `catalog_owner`, `comment`, `created`, `last_altered`
- Use for: listing available catalogs, checking catalog existence

**system.information_schema.tables**
- Columns: `table_catalog`, `table_schema`, `table_name`, `table_type`, `table_owner`, `created`, `last_altered`, `data_source_format`
- Use for: listing tables, checking existence, finding owner or format

**system.information_schema.columns**
- Columns: `table_catalog`, `table_schema`, `table_name`, `column_name`, `ordinal_position`, `data_type`, `is_nullable`, `column_default`, `comment`
- Use for: describing a table's schema, finding columns by name or type
