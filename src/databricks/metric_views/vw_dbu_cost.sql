-- ============================================================
-- Step 1 — Base view: pre-joins billing.usage × list_prices
--   and flattens struct fields into plain columns.
--   record_type = 'ORIGINAL' is applied here so it is never
--   forgotten downstream.
-- ============================================================

CREATE OR REPLACE VIEW vireox_infra.semantic._base_dbu_cost AS
SELECT
  u.workspace_id,
  u.sku_name,
  u.billing_origin_product,
  u.usage_date,
  u.usage_start_time,
  u.usage_end_time,
  u.identity_metadata.run_as          AS run_as,
  u.usage_metadata.warehouse_id       AS warehouse_id,
  u.usage_metadata.job_id             AS job_id,
  u.usage_metadata.job_name           AS job_name,
  u.usage_metadata.cluster_id         AS cluster_id,
  u.usage_metadata.dlt_pipeline_id    AS pipeline_id,
  u.usage_metadata.app_id             AS app_id,
  u.usage_quantity,
  u.usage_quantity * p.pricing.default AS cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices p
  ON  u.sku_name          = p.sku_name
  AND u.cloud             = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.record_type = 'ORIGINAL';

-- ============================================================
-- Step 2 — Metric view: semantic layer on top of the base view.
--   source is a simple table reference — no SQL embedded in YAML.
--   Always filter usage_date / usage_start_time to the 180-day
--   window when querying.
-- ============================================================

CREATE OR REPLACE VIEW vireox_infra.semantic.vw_dbu_cost
WITH METRICS LANGUAGE YAML AS$$
version: 1.1
comment: "DBU consumption and estimated USD cost per workspace / user / product. list-price JOIN and record_type filter are pre-applied in the base view. Always filter usage_date or usage_start_time to the 180-day window."
source: vireox_infra.semantic._base_dbu_cost

fields:
  - name: workspace_id
    expr: workspace_id
    comment: "Databricks workspace identifier"

  - name: sku_name
    expr: sku_name
    comment: "Billing SKU (e.g. PREMIUM_ALL_PURPOSE_COMPUTE, JOBS_COMPUTE, SERVERLESS_COMPUTE)"
    synonyms: [sku, product_sku, tier]

  - name: billing_origin_product
    expr: billing_origin_product
    comment: "High-level product — JOBS, SQL, DLT, MODEL_SERVING, NOTEBOOKS, INTERACTIVE, etc."
    synonyms: [product, origin_product, workload_type]
    display_name: "Product"

  - name: run_as
    expr: run_as
    comment: "User email or service principal attributed to the workload (NULL for shared SQL warehouse usage)"
    synonyms: [user, owner, attributed_to, identity]
    display_name: "User"

  - name: warehouse_id
    expr: warehouse_id
    comment: "SQL warehouse ID for warehouse-originated usage"

  - name: job_id
    expr: job_id
    comment: "Lakeflow job ID for job-originated usage"

  - name: job_name
    expr: job_name
    comment: "User-assigned job name — no join needed"
    synonyms: [job]

  - name: cluster_id
    expr: cluster_id
    comment: "Non-serverless all-purpose cluster ID"

  - name: pipeline_id
    expr: pipeline_id
    comment: "Lakeflow (DLT) pipeline ID"

  - name: usage_date
    expr: usage_date
    comment: "Calendar date of usage — use for daily or monthly GROUP BY"
    synonyms: [date, day]

  - name: usage_start_time
    expr: usage_start_time
    comment: "Billing period start timestamp — use for sub-day filtering"

measures:
  - name: total_dbus
    expr: SUM(usage_quantity)
    comment: "Total DBU consumption"
    synonyms: [dbus, dbu_total, consumption, units]

  - name: estimated_cost_usd
    expr: SUM(cost_usd)
    comment: "Estimated USD cost at Databricks list prices (not contractual/discounted)"
    synonyms: [cost, spend, cost_usd, total_cost, total_spend]
    format:
      type: currency
      currency_code: USD
      decimal_places:
        type: exact
        places: 2

  - name: avg_daily_cost_usd
    expr: SUM(cost_usd) / NULLIF(COUNT(DISTINCT usage_date), 0)
    comment: "Average daily cost over the queried period"
    synonyms: [daily_cost, daily_spend]
    format:
      type: currency
      currency_code: USD
      decimal_places:
        type: exact
        places: 2
$$
