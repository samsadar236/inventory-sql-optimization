"""
build_and_run.py
----------------
Loads inventory_forecasting.csv into an in-memory DuckDB instance using the
star-schema layout from sql/01_schema.sql, then executes every analytical
query from sql/03_queries.sql and dumps each result to CSV under data/.

DuckDB is PostgreSQL-compatible for the dialect we use here (window funcs,
DISTINCT ON, INTERVAL, EXTRACT, CTEs, generate_series). This serves as both
a smoke-test of the SQL and the data source for the dashboard.
"""

import duckdb
import pandas as pd
from pathlib import Path

ROOT      = Path("/home/claude/urban_retail_sql")
CSV_PATH  = "/mnt/user-data/uploads/inventory_forecasting.csv"
DATA_DIR  = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

con = duckdb.connect(database=":memory:")

# ---------------------------------------------------------------------------
# 1.  Build the schema in DuckDB.
#     DuckDB doesn't support SERIAL, FK enforcement, or COMMENT ON COLUMN
#     identically to Postgres — we translate where needed but the analytical
#     semantics match.
# ---------------------------------------------------------------------------
con.execute("""
    CREATE TABLE staging_inventory AS
    SELECT
        "Date"               ::DATE         AS txn_date,
        "Store ID"           ::VARCHAR      AS store_id,
        "Product ID"         ::VARCHAR      AS product_id,
        "Category"           ::VARCHAR      AS category,
        "Region"             ::VARCHAR      AS region,
        "Inventory Level"    ::INTEGER      AS inventory_level,
        "Units Sold"         ::INTEGER      AS units_sold,
        "Units Ordered"      ::INTEGER      AS units_ordered,
        "Demand Forecast"    ::DOUBLE       AS demand_forecast,
        "Price"              ::DOUBLE       AS price,
        "Discount"           ::INTEGER      AS discount,
        "Weather Condition"  ::VARCHAR      AS weather_condition,
        "Holiday/Promotion"  ::SMALLINT     AS holiday_promotion,
        "Competitor Pricing" ::DOUBLE       AS competitor_pricing,
        "Seasonality"        ::VARCHAR      AS seasonality
    FROM read_csv_auto(?, header=True);
""", [CSV_PATH])

print("staging rows:", con.execute("SELECT COUNT(*) FROM staging_inventory").fetchone()[0])

# Dimensions
con.execute("""
CREATE TABLE dim_date AS
SELECT DISTINCT
    txn_date                                    AS date_key,
    EXTRACT(DAY     FROM txn_date)::SMALLINT    AS day,
    EXTRACT(MONTH   FROM txn_date)::SMALLINT    AS month,
    strftime(txn_date, '%B')                    AS month_name,
    EXTRACT(QUARTER FROM txn_date)::SMALLINT    AS quarter,
    EXTRACT(YEAR    FROM txn_date)::SMALLINT    AS year,
    EXTRACT(ISODOW  FROM txn_date)::SMALLINT    AS day_of_week,
    strftime(txn_date, '%A')                    AS day_name,
    EXTRACT(ISODOW FROM txn_date) IN (6,7)      AS is_weekend,
    seasonality                                 AS season,
    EXTRACT(WEEK FROM txn_date)::SMALLINT       AS week_of_year
FROM staging_inventory;
""")

con.execute("CREATE TABLE dim_product AS SELECT DISTINCT product_id, category FROM staging_inventory;")
con.execute("CREATE TABLE dim_store   AS SELECT DISTINCT store_id FROM staging_inventory;")
con.execute("""
CREATE TABLE dim_region AS
SELECT ROW_NUMBER() OVER (ORDER BY region) AS region_id,
       region AS region_name
FROM (SELECT DISTINCT region FROM staging_inventory);
""")

con.execute("""
CREATE TABLE fact_inventory_daily AS
SELECT  s.txn_date,
        s.store_id,
        s.product_id,
        r.region_id,
        s.inventory_level,
        s.units_sold,
        s.units_ordered,
        s.demand_forecast,
        s.price,
        s.discount             AS discount_pct,
        s.competitor_pricing   AS competitor_price,
        s.weather_condition,
        s.holiday_promotion
FROM    staging_inventory s
JOIN    dim_region r ON r.region_name = s.region;
""")

# Validation
for t in ["dim_date", "dim_product", "dim_store", "dim_region", "fact_inventory_daily"]:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t:25s} {n:>8} rows")

# ---------------------------------------------------------------------------
# 2.  Run each analytical query.  We define each block as Python-side strings
#     so we can capture the result and save it.  Each query here is the same
#     SQL that lives in sql/03_queries.sql — kept in sync intentionally.
# ---------------------------------------------------------------------------

QUERIES = {}

QUERIES["Q1_current_stock_levels"] = """
WITH latest AS (
    SELECT store_id, product_id, txn_date, inventory_level FROM (
        SELECT store_id, product_id, txn_date, inventory_level,
               ROW_NUMBER() OVER (PARTITION BY store_id, product_id
                                  ORDER BY txn_date DESC) AS rn
        FROM fact_inventory_daily
    ) t WHERE rn = 1
),
rolling_30 AS (
    SELECT  store_id, product_id,
            AVG(units_sold) AS avg_daily_demand_30d
    FROM    fact_inventory_daily
    WHERE   txn_date >= (SELECT MAX(txn_date) - INTERVAL 30 DAY FROM fact_inventory_daily)
    GROUP BY store_id, product_id
)
SELECT  l.store_id, l.product_id, p.category, l.txn_date AS as_of_date,
        l.inventory_level AS on_hand,
        ROUND(r.avg_daily_demand_30d, 2) AS avg_daily_demand_30d,
        ROUND(l.inventory_level / NULLIF(r.avg_daily_demand_30d, 0), 1) AS days_of_cover
FROM    latest l
JOIN    rolling_30 r USING (store_id, product_id)
JOIN    dim_product p USING (product_id)
ORDER BY days_of_cover NULLS LAST;
"""

QUERIES["Q2_low_inventory_alerts"] = """
WITH params AS (SELECT 7.0 AS lead_time_days, 1.65 AS z_score),
latest AS (
    SELECT store_id, product_id, txn_date, inventory_level FROM (
        SELECT store_id, product_id, txn_date, inventory_level,
               ROW_NUMBER() OVER (PARTITION BY store_id, product_id
                                  ORDER BY txn_date DESC) AS rn
        FROM fact_inventory_daily
    ) t WHERE rn = 1
),
demand_stats AS (
    SELECT  store_id, product_id,
            AVG(units_sold) AS mean_demand,
            STDDEV_SAMP(units_sold) AS sd_demand
    FROM    fact_inventory_daily
    WHERE   txn_date >= (SELECT MAX(txn_date) - INTERVAL 90 DAY FROM fact_inventory_daily)
    GROUP BY store_id, product_id
)
SELECT  l.store_id, l.product_id, p.category, l.inventory_level AS on_hand,
        ROUND(d.mean_demand * pa.lead_time_days, 0)                       AS cycle_stock,
        ROUND(pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)      AS safety_stock,
        ROUND(d.mean_demand * pa.lead_time_days
              + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)    AS reorder_point,
        CASE
            WHEN l.inventory_level = 0 THEN 'STOCKOUT'
            WHEN l.inventory_level <= d.mean_demand * pa.lead_time_days
                          + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days)
                                     THEN 'REORDER NOW'
            ELSE 'OK'
        END AS status
FROM    latest l
JOIN    demand_stats d USING (store_id, product_id)
JOIN    dim_product  p USING (product_id)
CROSS JOIN params pa
WHERE   l.inventory_level <= d.mean_demand * pa.lead_time_days
                + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days)
ORDER BY (d.mean_demand * pa.lead_time_days
          + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days)) - l.inventory_level DESC;
"""

QUERIES["Q3_reorder_points"] = """
WITH params AS (SELECT 7.0 AS lead_time_days, 1.65 AS z_score),
demand_stats AS (
    SELECT  store_id, product_id,
            AVG(units_sold)         AS mean_demand,
            STDDEV_SAMP(units_sold) AS sd_demand,
            MAX(units_sold)         AS peak_demand
    FROM    fact_inventory_daily
    GROUP BY store_id, product_id
)
SELECT  d.store_id, d.product_id, p.category,
        ROUND(d.mean_demand, 1) AS avg_daily_demand,
        ROUND(d.sd_demand, 1)   AS demand_std_dev,
        d.peak_demand,
        ROUND(d.mean_demand * pa.lead_time_days, 0)                       AS cycle_stock,
        ROUND(pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)      AS safety_stock,
        ROUND(d.mean_demand * pa.lead_time_days
              + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)    AS reorder_point,
        ROUND(d.mean_demand * pa.lead_time_days * 1.5
              + pa.z_score * d.sd_demand * SQRT(pa.lead_time_days), 0)    AS max_stock_level
FROM    demand_stats d
JOIN    dim_product p USING (product_id)
CROSS JOIN params pa
ORDER BY d.store_id, reorder_point DESC;
"""

QUERIES["Q4_turnover_analysis"] = """
SELECT  p.category, f.product_id,
        SUM(f.units_sold) AS total_units_sold,
        ROUND(AVG(f.inventory_level), 1) AS avg_inventory,
        ROUND(SUM(f.units_sold) / NULLIF(AVG(f.inventory_level), 0), 2) AS turnover_ratio,
        ROUND(365.0 / NULLIF(SUM(f.units_sold) / NULLIF(AVG(f.inventory_level), 0), 0), 1)
              AS days_inventory_outstanding
FROM    fact_inventory_daily f
JOIN    dim_product p USING (product_id)
WHERE   f.txn_date >= (SELECT MAX(txn_date) - INTERVAL 365 DAY FROM fact_inventory_daily)
GROUP BY p.category, f.product_id
ORDER BY turnover_ratio DESC;
"""

QUERIES["Q5_kpi_summary"] = """
WITH window_data AS (
    SELECT * FROM fact_inventory_daily
    WHERE txn_date >= (SELECT MAX(txn_date) - INTERVAL 90 DAY FROM fact_inventory_daily)
)
SELECT
    COUNT(DISTINCT txn_date)                                AS days_in_window,
    COUNT(*)                                                AS fact_rows,
    SUM(units_sold)                                         AS total_units_sold,
    ROUND(SUM(units_sold * price), 0)                       AS gross_revenue,
    ROUND(AVG(inventory_level), 1)                          AS avg_stock_level,
    ROUND(100.0 * SUM(CASE WHEN inventory_level = 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                            AS stockout_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN units_sold = 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                            AS zero_sale_rate_pct,
    ROUND(AVG(CASE WHEN units_sold > 0 THEN inventory_level::DOUBLE / units_sold END), 1)
                                                            AS avg_days_of_cover,
    ROUND(SUM(units_sold) / NULLIF(AVG(inventory_level), 0), 2)
                                                            AS turnover_90d,
    ROUND(100.0 * SUM(CASE WHEN units_sold > demand_forecast THEN 1 ELSE 0 END)
                / COUNT(*), 2)                              AS forecast_under_pct
FROM window_data;
"""

QUERIES["Q6_abc_analysis"] = """
WITH product_revenue AS (
    SELECT  f.product_id, p.category,
            SUM(f.units_sold) AS units_sold,
            ROUND(SUM(f.units_sold * f.price), 2) AS revenue
    FROM    fact_inventory_daily f
    JOIN    dim_product p USING (product_id)
    GROUP BY f.product_id, p.category
),
ranked AS (
    SELECT product_id, category, units_sold, revenue,
           RANK() OVER (ORDER BY revenue DESC) AS revenue_rank,
           100.0 * revenue / SUM(revenue) OVER () AS pct_of_revenue,
           100.0 * SUM(revenue) OVER (ORDER BY revenue DESC
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                 / SUM(revenue) OVER () AS cumulative_pct
    FROM product_revenue
)
SELECT  product_id, category, units_sold, revenue,
        ROUND(pct_of_revenue, 2) AS pct_of_revenue,
        ROUND(cumulative_pct, 2) AS cumulative_pct,
        CASE WHEN cumulative_pct <= 70 THEN 'A - Fast Mover'
             WHEN cumulative_pct <= 90 THEN 'B - Medium Mover'
             ELSE 'C - Slow Mover' END AS abc_class
FROM ranked ORDER BY revenue_rank;
"""

QUERIES["Q7_stock_adjustments"] = """
WITH params AS (SELECT 7.0 AS lead_time, 1.65 AS z),
demand_stats AS (
    SELECT store_id, product_id,
           AVG(units_sold) AS mean_d, STDDEV_SAMP(units_sold) AS sd_d
    FROM fact_inventory_daily
    GROUP BY store_id, product_id
),
latest AS (
    SELECT store_id, product_id, inventory_level, txn_date FROM (
        SELECT store_id, product_id, inventory_level, txn_date,
               ROW_NUMBER() OVER (PARTITION BY store_id, product_id
                                  ORDER BY txn_date DESC) AS rn
        FROM fact_inventory_daily
    ) t WHERE rn = 1
),
levels AS (
    SELECT  l.store_id, l.product_id, l.inventory_level,
            ROUND(d.mean_d * pa.lead_time + pa.z * d.sd_d * SQRT(pa.lead_time), 0) AS rop,
            ROUND(d.mean_d * pa.lead_time * 1.5 + pa.z * d.sd_d * SQRT(pa.lead_time), 0) AS max_level
    FROM latest l
    JOIN demand_stats d USING (store_id, product_id)
    CROSS JOIN params pa
)
SELECT  store_id, product_id, inventory_level AS on_hand,
        rop AS reorder_point, max_level AS target_max,
        CASE WHEN inventory_level <  rop       THEN 'INCREASE'
             WHEN inventory_level >  max_level THEN 'DECREASE'
             ELSE 'HOLD' END AS action,
        CASE WHEN inventory_level <  rop       THEN max_level - inventory_level
             WHEN inventory_level >  max_level THEN inventory_level - max_level
             ELSE 0 END AS units_to_adjust
FROM levels
ORDER BY action, units_to_adjust DESC;
"""

QUERIES["Q8_supplier_performance"] = """
SELECT  f.product_id, p.category,
        COUNT(*) AS observation_days,
        ROUND(AVG(f.units_ordered), 1) AS avg_units_ordered,
        ROUND(AVG(f.units_sold), 1)    AS avg_units_sold,
        ROUND(AVG(f.demand_forecast), 1) AS avg_forecast,
        ROUND(AVG(f.units_sold::DOUBLE / NULLIF(f.units_ordered, 0)), 3) AS fill_rate_proxy,
        ROUND(STDDEV_SAMP(f.units_ordered), 1) AS order_variability,
        ROUND(AVG(ABS(f.units_ordered - f.demand_forecast)), 1) AS order_vs_forecast_mae
FROM    fact_inventory_daily f
JOIN    dim_product p USING (product_id)
GROUP BY f.product_id, p.category
ORDER BY fill_rate_proxy DESC;
"""

QUERIES["Q9_demand_forecast"] = """
WITH daily AS (
    SELECT product_id, txn_date,
           SUM(units_sold) AS units_sold,
           SUM(demand_forecast) AS forecast
    FROM fact_inventory_daily
    GROUP BY product_id, txn_date
)
SELECT  product_id, txn_date, units_sold, forecast,
        ROUND(AVG(units_sold) OVER (PARTITION BY product_id ORDER BY txn_date
                                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 1) AS ma_7d,
        ROUND(AVG(units_sold) OVER (PARTITION BY product_id ORDER BY txn_date
                                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW), 1) AS ma_30d,
        ROUND(100.0 * (units_sold - LAG(units_sold, 7) OVER
                       (PARTITION BY product_id ORDER BY txn_date))
              / NULLIF(LAG(units_sold, 7) OVER (PARTITION BY product_id ORDER BY txn_date), 0), 1)
              AS wow_growth_pct
FROM daily
WHERE txn_date >= (SELECT MAX(txn_date) - INTERVAL 60 DAY FROM fact_inventory_daily)
ORDER BY product_id, txn_date;
"""

QUERIES["Q10_stockout_by_category_region"] = """
SELECT  p.category, r.region_name,
        COUNT(*) AS fact_rows,
        SUM(CASE WHEN f.inventory_level = 0 THEN 1 ELSE 0 END) AS stockout_days,
        ROUND(100.0 * SUM(CASE WHEN f.inventory_level = 0 THEN 1 ELSE 0 END) / COUNT(*), 3) AS stockout_rate_pct,
        ROUND(AVG(f.inventory_level), 1) AS avg_inventory,
        ROUND(AVG(f.units_sold), 1) AS avg_daily_sales
FROM    fact_inventory_daily f
JOIN    dim_product p USING (product_id)
JOIN    dim_region  r USING (region_id)
GROUP BY p.category, r.region_name
ORDER BY p.category, stockout_rate_pct DESC;
"""

# ---------------------------------------------------------------------------
# Execute everything
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("Running analytical queries")
print("=" * 60)

results = {}
for name, sql in QUERIES.items():
    try:
        df = con.execute(sql).df()
        results[name] = df
        outpath = DATA_DIR / f"{name}.csv"
        df.to_csv(outpath, index=False)
        print(f"OK  {name:40s} -> {len(df):6d} rows  ({outpath.name})")
    except Exception as e:
        print(f"ERR {name:40s} -> {e}")

# Quick previews of the key ones
print("\n--- Q5 KPI Summary ---")
print(results["Q5_kpi_summary"].to_string(index=False))

print("\n--- Q6 ABC Analysis (top 5) ---")
print(results["Q6_abc_analysis"].head(5).to_string(index=False))

print("\n--- Q2 Low Inventory Alerts (top 5) ---")
print(results["Q2_low_inventory_alerts"].head(5).to_string(index=False))

print("\n--- Q10 Stockout by Category/Region ---")
print(results["Q10_stockout_by_category_region"].to_string(index=False))

# Save the DuckDB DB to disk so dashboard.py can re-attach without re-loading
con.execute(f"ATTACH '{ROOT}/data/inventory.duckdb' AS persist (TYPE DUCKDB);")
for t in ["dim_date", "dim_product", "dim_store", "dim_region", "fact_inventory_daily"]:
    con.execute(f"CREATE TABLE persist.{t} AS SELECT * FROM {t};")
con.execute("DETACH persist;")
print("\nDuckDB persisted to data/inventory.duckdb")
