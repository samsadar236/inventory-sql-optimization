

-- ---- Clean slate (idempotent re-runs) -------------------------------------
DROP TABLE IF EXISTS fact_inventory_daily CASCADE;
DROP TABLE IF EXISTS dim_date            CASCADE;
DROP TABLE IF EXISTS dim_product         CASCADE;
DROP TABLE IF EXISTS dim_store           CASCADE;
DROP TABLE IF EXISTS dim_region          CASCADE;
DROP TABLE IF EXISTS staging_inventory   CASCADE;


-- ---------------------------------------------------------------------------
-- STAGING TABLE
-- One-to-one mapping with the raw CSV. Used to bulk-load and then split.
-- ---------------------------------------------------------------------------
CREATE TABLE staging_inventory (
    txn_date            DATE          NOT NULL,
    store_id            VARCHAR(10)   NOT NULL,
    product_id          VARCHAR(10)   NOT NULL,
    category            VARCHAR(50)   NOT NULL,
    region              VARCHAR(20)   NOT NULL,
    inventory_level     INTEGER       NOT NULL,
    units_sold          INTEGER       NOT NULL,
    units_ordered       INTEGER       NOT NULL,
    demand_forecast     NUMERIC(10,2) NOT NULL,
    price               NUMERIC(10,2) NOT NULL,
    discount            INTEGER       NOT NULL,
    weather_condition   VARCHAR(20)   NOT NULL,
    holiday_promotion   SMALLINT      NOT NULL,
    competitor_pricing  NUMERIC(10,2) NOT NULL,
    seasonality         VARCHAR(20)   NOT NULL
);


-- ---------------------------------------------------------------------------
-- DIMENSION : dim_date
-- One row per calendar date. Holds derived time attributes used for slicing.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_date (
    date_key       DATE        PRIMARY KEY,
    day            SMALLINT    NOT NULL,
    month          SMALLINT    NOT NULL,
    month_name     VARCHAR(10) NOT NULL,
    quarter        SMALLINT    NOT NULL,
    year           SMALLINT    NOT NULL,
    day_of_week    SMALLINT    NOT NULL,
    day_name       VARCHAR(10) NOT NULL,
    is_weekend     BOOLEAN     NOT NULL,
    season         VARCHAR(20) NOT NULL,
    week_of_year   SMALLINT    NOT NULL
);

CREATE INDEX idx_dim_date_year_month ON dim_date (year, month);
CREATE INDEX idx_dim_date_season     ON dim_date (season);


-- ---------------------------------------------------------------------------
-- DIMENSION : dim_product
-- One row per SKU. Category is functionally dependent on product_id.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_product (
    product_id   VARCHAR(10)  PRIMARY KEY,
    category     VARCHAR(50)  NOT NULL
);

CREATE INDEX idx_dim_product_category ON dim_product (category);


-- ---------------------------------------------------------------------------
-- DIMENSION : dim_store
-- One row per store. Kept thin (the CSV has no further store attributes).
-- ---------------------------------------------------------------------------
CREATE TABLE dim_store (
    store_id   VARCHAR(10) PRIMARY KEY
);


-- ---------------------------------------------------------------------------
-- DIMENSION : dim_region
-- One row per region. Surrogate integer key for join efficiency.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_region (
    region_id    SERIAL      PRIMARY KEY,
    region_name  VARCHAR(20) UNIQUE NOT NULL
);


-- ---------------------------------------------------------------------------
-- FACT : fact_inventory_daily
-- Grain : one row per (date, store, product). Holds all measures + the
--         "transactional" attributes that vary per fact record (weather,
--         promotion flag, region of fulfillment, price, discount).
-- ---------------------------------------------------------------------------
CREATE TABLE fact_inventory_daily (
    txn_date             DATE          NOT NULL,
    store_id             VARCHAR(10)   NOT NULL,
    product_id           VARCHAR(10)   NOT NULL,
    region_id            INTEGER       NOT NULL,

    -- Measures
    inventory_level      INTEGER       NOT NULL,
    units_sold           INTEGER       NOT NULL,
    units_ordered        INTEGER       NOT NULL,
    demand_forecast      NUMERIC(10,2) NOT NULL,
    price                NUMERIC(10,2) NOT NULL,
    discount_pct         INTEGER       NOT NULL,
    competitor_price     NUMERIC(10,2) NOT NULL,

    -- Row-level attributes
    weather_condition    VARCHAR(20)   NOT NULL,
    holiday_promotion    SMALLINT      NOT NULL,

    CONSTRAINT pk_fact_inventory   PRIMARY KEY (txn_date, store_id, product_id),
    CONSTRAINT fk_fact_date        FOREIGN KEY (txn_date)   REFERENCES dim_date    (date_key),
    CONSTRAINT fk_fact_store       FOREIGN KEY (store_id)   REFERENCES dim_store   (store_id),
    CONSTRAINT fk_fact_product     FOREIGN KEY (product_id) REFERENCES dim_product (product_id),
    CONSTRAINT fk_fact_region      FOREIGN KEY (region_id)  REFERENCES dim_region  (region_id),
    CONSTRAINT chk_inventory_nneg  CHECK (inventory_level >= 0),
    CONSTRAINT chk_units_sold_nneg CHECK (units_sold      >= 0),
    CONSTRAINT chk_discount_range  CHECK (discount_pct BETWEEN 0 AND 100)
);

-- Indexes tuned for the analytical queries in 03_queries.sql -----------------
CREATE INDEX idx_fact_store_product_date ON fact_inventory_daily (store_id, product_id, txn_date DESC);
CREATE INDEX idx_fact_product_date       ON fact_inventory_daily (product_id, txn_date);
CREATE INDEX idx_fact_date               ON fact_inventory_daily (txn_date);
CREATE INDEX idx_fact_region             ON fact_inventory_daily (region_id);


-- ---------------------------------------------------------------------------
-- Comments (self-documenting schema)
-- ---------------------------------------------------------------------------
COMMENT ON TABLE  fact_inventory_daily IS 'Daily snapshot of inventory + sales per (store, product).';
COMMENT ON COLUMN fact_inventory_daily.inventory_level IS 'End-of-day on-hand units.';
COMMENT ON COLUMN fact_inventory_daily.units_ordered   IS 'Replenishment units ordered from supplier that day.';
COMMENT ON COLUMN fact_inventory_daily.demand_forecast IS 'Internal predicted demand for the day (units).';
