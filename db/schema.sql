-- Full schema for Restaurant Ops Copilot
-- All statements use IF NOT EXISTS so this file is safe to re-run against an existing DB.

CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Core entities ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS restaurants (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    locality    VARCHAR(255) NOT NULL,
    cuisine     VARCHAR(100) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS menu_items (
    id            SERIAL PRIMARY KEY,
    restaurant_id INTEGER      NOT NULL REFERENCES restaurants(id),
    name          VARCHAR(255) NOT NULL,
    price         NUMERIC(10,2) NOT NULL,
    category      VARCHAR(100) NOT NULL,
    active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─── Order history ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    id            BIGSERIAL PRIMARY KEY,
    restaurant_id INTEGER      NOT NULL REFERENCES restaurants(id),
    item_id       INTEGER      NOT NULL REFERENCES menu_items(id),
    qty           SMALLINT     NOT NULL CHECK (qty > 0),
    ordered_at    TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_restaurant_date ON orders (restaurant_id, ordered_at);
CREATE INDEX IF NOT EXISTS idx_orders_item_date       ON orders (item_id, ordered_at);

-- ─── Recipes / bill of materials ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bill_of_materials (
    id            SERIAL PRIMARY KEY,
    dish_id       INTEGER       NOT NULL REFERENCES menu_items(id),
    raw_material  VARCHAR(255)  NOT NULL,   -- slug matching inventory + catalog
    qty_per_unit  NUMERIC(10,4) NOT NULL,   -- per one serving
    unit          VARCHAR(50)   NOT NULL    -- g | ml | piece
);

CREATE INDEX IF NOT EXISTS idx_bom_dish ON bill_of_materials (dish_id);

-- ─── Inventory ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS inventory (
    id            SERIAL PRIMARY KEY,
    restaurant_id INTEGER       NOT NULL REFERENCES restaurants(id),
    raw_material  VARCHAR(255)  NOT NULL,
    current_qty   NUMERIC(12,4) NOT NULL,
    unit          VARCHAR(50)   NOT NULL,
    reorder_point NUMERIC(12,4) NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (restaurant_id, raw_material)
);

-- ─── Instamart product catalog ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS raw_material_catalog (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(255)  NOT NULL,          -- raw_material slug
    instamart_product_id VARCHAR(50)   NOT NULL UNIQUE,
    product_name         VARCHAR(255)  NOT NULL,          -- display name on Instamart
    pack_size            NUMERIC(10,2) NOT NULL,
    unit                 VARCHAR(50)   NOT NULL,
    price                NUMERIC(10,2) NOT NULL,
    category             VARCHAR(100)  NOT NULL,
    in_stock             BOOLEAN       NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_catalog_name ON raw_material_catalog (name);

-- ─── Forecasts ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS forecasts (
    id             BIGSERIAL PRIMARY KEY,
    restaurant_id  INTEGER       NOT NULL REFERENCES restaurants(id),
    item_id        INTEGER       NOT NULL REFERENCES menu_items(id),
    forecast_date  DATE          NOT NULL,
    predicted_qty  NUMERIC(10,2) NOT NULL,
    model_version  VARCHAR(50)   NOT NULL,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (restaurant_id, item_id, forecast_date, model_version)
);

CREATE INDEX IF NOT EXISTS idx_forecasts_date ON forecasts (restaurant_id, forecast_date);

-- ─── Menu embeddings (pgvector, Phase 4+) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS menu_embeds (
    item_id   INTEGER PRIMARY KEY REFERENCES menu_items(id),
    embedding vector(1536)
);
