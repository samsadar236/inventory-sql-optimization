-- ============================================================================
-- Urban Retail Co. — Inventory Analytics
-- 03_queries.sql : Analytical query suite (PostgreSQL 14+)
-- ============================================================================
-- Each query is self-contained and ends with a semicolon. Run individually.
-- Numbering follows the project brief: Stock Levels → Low Inventory →
-- Reorder Points → Turnover → KPI Summary → Fast/Slow Movers → Adjustments
-- → Supplier Performance → Demand Forecast → Stockout Rate.
-- ============================================================================


-- ============================================================================
-- Q1. CURRENT STOCK LEVELS
-- Latest end-of-day inventory per (store, product), with a rolling 30-day
-- demand snapshot to give context for the on-hand number.
-- Uses DISTINCT ON, a PostgreSQL idiom for "top-1-per-group".
-- ============================================================================
WITH latest AS (
    SELECT DISTINCT ON (store_id, product_id)
           store_id, product_id, txn_date, inventory_level
    FROM   fact_inventory_daily
    ORDER  BY store_id, product_id, txn_date DESC
),
rolling_30 AS (
    SELECT  store_id, product_id,
            AVG(units_sold)::NUMERIC(10,2) AS avg_daily_demand_30d
    FROM    fact_inventory_daily
    WHERE   txn_date >= (SELECT MAX(txn_date) - INTERVAL '30 days'
                         FROM fact_inventory_daily)
    GROUP BY store_id, product_id
)
SELECT  l.store_id,
        l.product_id,
        p.category,
        l.txn_date           AS as_of_date,
        l.inventory_level    AS on_hand,
        r.avg_daily_demand_30d,
        ROUND(l.inventory_level / NULLIF(r.avg_daily_demand_30d, 0), 1)
                             AS days_of_cover
FROM    latest      l
JOIN    rolling_30  r USING (store_id, product_id)
JOIN    dim_product p USING (product_id)
ORDER BY days_of_cover NULLS LAST;


-- ============================================================================
-- Q2. LOW INVENTORY DETECTION
-- Flags (store, product) pairs where current stock is below the dynamic
-- reorder point. Reorder point = (avg daily demand × lead time)
--                              + safety stock (z × σ × √lead time).
-- Assumptions: lead_time = 7 days, service level z = 1.65 (≈ 95%).
-- ============================================================================
WITH params AS (
    SELECT 7::NUMERIC AS lead_time_days,
           1.65::NUMERIC AS z_score
),
latest AS (
    SELECT DISTINCT ON (store_id, product_id)
           store_id, product_id, txn_date, inventory_level
    FROM   fact_inventory_daily
    ORDER  BY store_id, product_id, txn_date DESC
),
demand_stats AS (
    SELECT  store_id, product_id,
            AVG(units_sold)::NUMERIC AS mean_demand,
            STDDEV_SAMP(units_sold)::NUMERIC AS sd_demand
    FROM    fact_inventory_daily
    WHERE   txn_date >= (SELECT MAX(txn_date) - INTERVAL '90 days'
                         FROM fact_inventory_daily)
    GROUP BY store_id, product_id
)
SELECT  l.store_id,
        l.product_id,
        p.category,
        l.inventory_level                                                  AS on_hand,
        ROUND(d.mean_demand * pa.lead_time_days, 0)                        AS cycle_stock,
        ROUND(pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)       AS safety_stock,
        ROUND(d.mean_demand * pa.lead_time_days
              + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)     AS reorder_point,
        CASE
            WHEN l.inventory_level = 0                                 THEN 'STOCKOUT'
            WHEN l.inventory_level <= d.mean_demand * pa.lead_time_days
                                 + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days)
                                                                       THEN 'REORDER NOW'
            ELSE                                                            'OK'
        END                                                                AS status
FROM    latest       l
JOIN    demand_stats d  USING (store_id, product_id)
JOIN    dim_product  p  USING (product_id)
CROSS JOIN params pa
WHERE   l.inventory_level <= d.mean_demand * pa.lead_time_days
                          + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days)
ORDER BY (d.mean_demand * pa.lead_time_days
          + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days))
       - l.inventory_level DESC;


-- ============================================================================
-- Q3. REORDER POINT ESTIMATION (full catalog)
-- Same formula as Q2 but returned for every (store, product) regardless of
-- current status. This is the master reorder-point lookup table.
-- ============================================================================
WITH params AS (
    SELECT 7::NUMERIC AS lead_time_days, 1.65::NUMERIC AS z_score
),
demand_stats AS (
    SELECT  store_id, product_id,
            AVG(units_sold)::NUMERIC      AS mean_demand,
            STDDEV_SAMP(units_sold)::NUMERIC AS sd_demand,
            MAX(units_sold)               AS peak_demand
    FROM    fact_inventory_daily
    GROUP BY store_id, product_id
)
SELECT  d.store_id,
        d.product_id,
        p.category,
        ROUND(d.mean_demand, 1)                                           AS avg_daily_demand,
        ROUND(d.sd_demand,   1)                                           AS demand_std_dev,
        d.peak_demand,
        ROUND(d.mean_demand * pa.lead_time_days, 0)                       AS cycle_stock,
        ROUND(pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)      AS safety_stock,
        ROUND(d.mean_demand * pa.lead_time_days
              + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)    AS reorder_point,
        ROUND(d.mean_demand * pa.lead_time_days * 1.5
              + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)    AS max_stock_level
FROM    demand_stats d
JOIN    dim_product  p USING (product_id)
CROSS JOIN params pa
ORDER BY d.store_id, reorder_point DESC;


-- ============================================================================
-- Q4. INVENTORY TURNOVER ANALYSIS
-- Turnover ratio = total units sold / average on-hand inventory (per period).
-- High turnover ⇒ stock moves fast; low turnover ⇒ capital tied up.
-- ============================================================================
SELECT  p.category,
        f.product_id,
        SUM(f.units_sold)                                  AS total_units_sold,
        ROUND(AVG(f.inventory_level)::NUMERIC, 1)          AS avg_inventory,
        ROUND(SUM(f.units_sold) / NULLIF(AVG(f.inventory_level), 0)::NUMERIC, 2)
                                                           AS turnover_ratio,
        ROUND(365.0 / NULLIF(
              SUM(f.units_sold) / NULLIF(AVG(f.inventory_level), 0)::NUMERIC, 0), 1)
                                                           AS days_inventory_outstanding
FROM    fact_inventory_daily f
JOIN    dim_product          p USING (product_id)
WHERE   f.txn_date >= (SELECT MAX(txn_date) - INTERVAL '365 days'
                       FROM fact_inventory_daily)
GROUP BY p.category, f.product_id
ORDER BY turnover_ratio DESC;


-- ============================================================================
-- Q5. KPI SUMMARY REPORT (Dashboard headline metrics)
-- Single-row scoreboard for the most recent 90 days.
-- ============================================================================
WITH window_data AS (
    SELECT *
    FROM   fact_inventory_daily
    WHERE  txn_date >= (SELECT MAX(txn_date) - INTERVAL '90 days'
                        FROM fact_inventory_daily)
)
SELECT
    COUNT(DISTINCT txn_date)                                AS days_in_window,
    COUNT(*)                                                AS fact_rows,
    SUM(units_sold)                                         AS total_units_sold,
    ROUND(SUM(units_sold * price)::NUMERIC, 0)              AS gross_revenue,
    ROUND(AVG(inventory_level)::NUMERIC, 1)                 AS avg_stock_level,
    ROUND(100.0 * SUM(CASE WHEN inventory_level = 0 THEN 1 ELSE 0 END)
                / COUNT(*)::NUMERIC, 2)                     AS stockout_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN units_sold = 0 THEN 1 ELSE 0 END)
                / COUNT(*)::NUMERIC, 2)                     AS zero_sale_rate_pct,
    ROUND(AVG(CASE WHEN units_sold > 0
                   THEN inventory_level::NUMERIC / units_sold END), 1)
                                                            AS avg_days_of_cover,
    ROUND(SUM(units_sold)::NUMERIC
        / NULLIF(AVG(inventory_level), 0)::NUMERIC, 2)      AS turnover_90d,
    ROUND(100.0 * SUM(CASE WHEN units_sold > demand_forecast THEN 1 ELSE 0 END)
                / COUNT(*)::NUMERIC, 2)                     AS forecast_under_pct
FROM window_data;


-- ============================================================================
-- Q6. FAST vs SLOW MOVING PRODUCTS (ABC ANALYSIS)
-- Classifies SKUs into A / B / C using NTILE on revenue contribution.
-- A = top 20% by revenue (the critical few),
-- B = next 30%, C = bottom 50%.
-- ============================================================================
WITH product_revenue AS (
    SELECT  f.product_id,
            p.category,
            SUM(f.units_sold)                       AS units_sold,
            ROUND(SUM(f.units_sold * f.price)::NUMERIC, 2) AS revenue
    FROM    fact_inventory_daily f
    JOIN    dim_product          p USING (product_id)
    GROUP BY f.product_id, p.category
),
ranked AS (
    SELECT  product_id, category, units_sold, revenue,
            RANK()  OVER (ORDER BY revenue DESC) AS revenue_rank,
            100.0 * revenue
                  / SUM(revenue) OVER ()         AS pct_of_revenue,
            100.0 * SUM(revenue) OVER (ORDER BY revenue DESC
                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                                 / SUM(revenue) OVER ()
                                                  AS cumulative_pct
    FROM product_revenue
)
SELECT  product_id, category, units_sold, revenue,
        ROUND(pct_of_revenue, 2)  AS pct_of_revenue,
        ROUND(cumulative_pct, 2)  AS cumulative_pct,
        CASE
            WHEN cumulative_pct <= 70 THEN 'A — Fast Mover'
            WHEN cumulative_pct <= 90 THEN 'B — Medium Mover'
            ELSE                            'C — Slow Mover'
        END AS abc_class
FROM    ranked
ORDER BY revenue_rank;


-- ============================================================================
-- Q7. STOCK ADJUSTMENT RECOMMENDATIONS
-- For each (store, product) compare on-hand against the reorder point
-- and the max stock level. Recommends INCREASE / DECREASE / HOLD with a
-- concrete unit adjustment.
-- ============================================================================
WITH params AS (
    SELECT 7::NUMERIC AS lead_time, 1.65::NUMERIC AS z
),
demand_stats AS (
    SELECT  store_id, product_id,
            AVG(units_sold)::NUMERIC          AS mean_d,
            STDDEV_SAMP(units_sold)::NUMERIC  AS sd_d
    FROM    fact_inventory_daily
    GROUP BY store_id, product_id
),
latest AS (
    SELECT DISTINCT ON (store_id, product_id)
           store_id, product_id, inventory_level, txn_date
    FROM   fact_inventory_daily
    ORDER BY store_id, product_id, txn_date DESC
),
levels AS (
    SELECT  l.store_id, l.product_id, l.inventory_level,
            ROUND(d.mean_d * pa.lead_time
                  + pa.z * d.sd_d * SQRT(pa.lead_time), 0) AS rop,
            ROUND(d.mean_d * pa.lead_time * 1.5
                  + pa.z * d.sd_d * SQRT(pa.lead_time), 0) AS max_level
    FROM    latest l
    JOIN    demand_stats d USING (store_id, product_id)
    CROSS JOIN params pa
)
SELECT  store_id, product_id,
        inventory_level                              AS on_hand,
        rop                                          AS reorder_point,
        max_level                                    AS target_max,
        CASE
            WHEN inventory_level <  rop       THEN 'INCREASE'
            WHEN inventory_level >  max_level THEN 'DECREASE'
            ELSE                                   'HOLD'
        END                                          AS action,
        CASE
            WHEN inventory_level <  rop       THEN max_level - inventory_level
            WHEN inventory_level >  max_level THEN inventory_level - max_level
            ELSE 0
        END                                          AS units_to_adjust
FROM    levels
ORDER BY action, units_to_adjust DESC;


-- ============================================================================
-- Q8. SUPPLIER PERFORMANCE (proxy via units_ordered vs demand_forecast)
-- Since the dataset has no supplier dimension, we proxy reliability by
-- comparing units ORDERED (procurement) against the realized demand.
-- A fill-rate proxy: units_sold / units_ordered. Sub-1.0 ⇒ over-ordering;
-- > 1.0 ⇒ under-supplied. Variance highlights inconsistency.
-- ============================================================================
SELECT  f.product_id,
        p.category,
        COUNT(*)                                                  AS observation_days,
        ROUND(AVG(f.units_ordered)::NUMERIC, 1)                   AS avg_units_ordered,
        ROUND(AVG(f.units_sold)::NUMERIC, 1)                      AS avg_units_sold,
        ROUND(AVG(f.demand_forecast)::NUMERIC, 1)                 AS avg_forecast,
        ROUND(AVG(f.units_sold::NUMERIC
                / NULLIF(f.units_ordered, 0)), 3)                 AS fill_rate_proxy,
        ROUND(STDDEV_SAMP(f.units_ordered)::NUMERIC, 1)           AS order_variability,
        ROUND(AVG(ABS(f.units_ordered - f.demand_forecast))::NUMERIC, 1)
                                                                  AS order_vs_forecast_mae
FROM    fact_inventory_daily f
JOIN    dim_product          p USING (product_id)
GROUP BY f.product_id, p.category
ORDER BY fill_rate_proxy DESC;


-- ============================================================================
-- Q9. SEASONAL DEMAND FORECAST (window functions + moving averages)
-- 7-day and 30-day moving averages per product to surface trend and
-- seasonality. Last 60 days returned for charting.
-- ============================================================================
WITH daily AS (
    SELECT  f.product_id,
            f.txn_date,
            SUM(f.units_sold)                AS units_sold,
            SUM(f.demand_forecast)::NUMERIC  AS forecast
    FROM    fact_inventory_daily f
    GROUP BY f.product_id, f.txn_date
)
SELECT  product_id,
        txn_date,
        units_sold,
        forecast,
        ROUND(AVG(units_sold) OVER (PARTITION BY product_id
                                    ORDER BY txn_date
                                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 1)
                                                              AS ma_7d,
        ROUND(AVG(units_sold) OVER (PARTITION BY product_id
                                    ORDER BY txn_date
                                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW), 1)
                                                              AS ma_30d,
        ROUND(100.0 * (units_sold
                - LAG(units_sold, 7) OVER (PARTITION BY product_id ORDER BY txn_date))
              / NULLIF(LAG(units_sold, 7) OVER (PARTITION BY product_id ORDER BY txn_date), 0), 1)
                                                              AS wow_growth_pct
FROM    daily
WHERE   txn_date >= (SELECT MAX(txn_date) - INTERVAL '60 days'
                     FROM fact_inventory_daily)
ORDER BY product_id, txn_date;


-- ============================================================================
-- Q10. STOCKOUT RATE BY CATEGORY / REGION  (visibility slice)
-- Where are the gaps? Highlights weak (category, region) cells.
-- ============================================================================
SELECT  p.category,
        r.region_name,
        COUNT(*)                                                  AS fact_rows,
        SUM(CASE WHEN f.inventory_level = 0 THEN 1 ELSE 0 END)    AS stockout_days,
        ROUND(100.0 * SUM(CASE WHEN f.inventory_level = 0 THEN 1 ELSE 0 END)
                    / COUNT(*)::NUMERIC, 3)                       AS stockout_rate_pct,
        ROUND(AVG(f.inventory_level)::NUMERIC, 1)                 AS avg_inventory,
        ROUND(AVG(f.units_sold)::NUMERIC, 1)                      AS avg_daily_sales
FROM    fact_inventory_daily f
JOIN    dim_product          p USING (product_id)
JOIN    dim_region           r USING (region_id)
GROUP BY p.category, r.region_name
ORDER BY p.category, stockout_rate_pct DESC;
