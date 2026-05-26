-- ============================================================================
-- Urban Retail Co. — Inventory Analytics
-- 02_load_data.sql : Bulk-load CSV into staging, then populate the star schema
-- ============================================================================
-- Pre-requisite : 01_schema.sql has been run.
-- Usage         : Replace the path in the \COPY command with the actual path
--                 to your CSV file on disk. \COPY is a psql client-side
--                 directive that does NOT require server-side superuser
--                 privileges (unlike SQL COPY).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Bulk load the raw CSV into staging
-- ---------------------------------------------------------------------------
TRUNCATE TABLE staging_inventory;

\COPY staging_inventory(
    txn_date, store_id, product_id, category, region,
    inventory_level, units_sold, units_ordered, demand_forecast,
    price, discount, weather_condition, holiday_promotion,
    competitor_pricing, seasonality
) FROM 'inventory_forecasting.csv' WITH (FORMAT csv, HEADER true);

-- Sanity check
SELECT COUNT(*) AS staging_rows FROM staging_inventory;


-- ---------------------------------------------------------------------------
-- 2. Populate dim_date (one row per unique date with derived attributes)
-- ---------------------------------------------------------------------------
INSERT INTO dim_date (
    date_key, day, month, month_name, quarter, year,
    day_of_week, day_name, is_weekend, season, week_of_year
)
SELECT DISTINCT
    s.txn_date                                            AS date_key,
    EXTRACT(DAY     FROM s.txn_date)::SMALLINT            AS day,
    EXTRACT(MONTH   FROM s.txn_date)::SMALLINT            AS month,
    TO_CHAR(s.txn_date, 'Month')                          AS month_name,
    EXTRACT(QUARTER FROM s.txn_date)::SMALLINT            AS quarter,
    EXTRACT(YEAR    FROM s.txn_date)::SMALLINT            AS year,
    EXTRACT(ISODOW  FROM s.txn_date)::SMALLINT            AS day_of_week,
    TO_CHAR(s.txn_date, 'Day')                            AS day_name,
    EXTRACT(ISODOW FROM s.txn_date) IN (6, 7)             AS is_weekend,
    s.seasonality                                         AS season,
    EXTRACT(WEEK FROM s.txn_date)::SMALLINT               AS week_of_year
FROM staging_inventory s;


-- ---------------------------------------------------------------------------
-- 3. Populate dim_product
-- ---------------------------------------------------------------------------
INSERT INTO dim_product (product_id, category)
SELECT DISTINCT product_id, category
FROM staging_inventory;


-- ---------------------------------------------------------------------------
-- 4. Populate dim_store
-- ---------------------------------------------------------------------------
INSERT INTO dim_store (store_id)
SELECT DISTINCT store_id FROM staging_inventory;


-- ---------------------------------------------------------------------------
-- 5. Populate dim_region (SERIAL key auto-increments)
-- ---------------------------------------------------------------------------
INSERT INTO dim_region (region_name)
SELECT DISTINCT region FROM staging_inventory;


-- ---------------------------------------------------------------------------
-- 6. Populate fact_inventory_daily (resolves region_name to region_id)
-- ---------------------------------------------------------------------------
INSERT INTO fact_inventory_daily (
    txn_date, store_id, product_id, region_id,
    inventory_level, units_sold, units_ordered, demand_forecast,
    price, discount_pct, competitor_price,
    weather_condition, holiday_promotion
)
SELECT
    s.txn_date,
    s.store_id,
    s.product_id,
    r.region_id,
    s.inventory_level,
    s.units_sold,
    s.units_ordered,
    s.demand_forecast,
    s.price,
    s.discount,
    s.competitor_pricing,
    s.weather_condition,
    s.holiday_promotion
FROM staging_inventory s
JOIN dim_region r ON r.region_name = s.region;


-- ---------------------------------------------------------------------------
-- 7. Refresh planner stats (important after a bulk load)
-- ---------------------------------------------------------------------------
ANALYZE dim_date;
ANALYZE dim_product;
ANALYZE dim_store;
ANALYZE dim_region;
ANALYZE fact_inventory_daily;


-- ---------------------------------------------------------------------------
-- 8. Validation
-- ---------------------------------------------------------------------------
SELECT 'fact_inventory_daily' AS table_name, COUNT(*) AS rows FROM fact_inventory_daily
UNION ALL SELECT 'dim_date',    COUNT(*) FROM dim_date
UNION ALL SELECT 'dim_product', COUNT(*) FROM dim_product
UNION ALL SELECT 'dim_store',   COUNT(*) FROM dim_store
UNION ALL SELECT 'dim_region',  COUNT(*) FROM dim_region;

-- Optional cleanup: drop staging once load is verified.
-- DROP TABLE staging_inventory;
