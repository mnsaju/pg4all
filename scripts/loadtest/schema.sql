-- Ecommerce-shaped tables for the load test.
--
-- Everything lives in one schema so removal is a single DROP SCHEMA that
-- cannot miss a table. Against the throwaway test stack that's academic —
-- `docker compose down -v` takes the volume with it — but it matters the
-- moment this is pointed at a build someone is keeping.

CREATE SCHEMA IF NOT EXISTS pg4all_loadtest;

DROP TABLE IF EXISTS pg4all_loadtest.order_items;
DROP TABLE IF EXISTS pg4all_loadtest.orders;
DROP TABLE IF EXISTS pg4all_loadtest.products;
DROP TABLE IF EXISTS pg4all_loadtest.customers;

CREATE TABLE pg4all_loadtest.customers (
    id          integer PRIMARY KEY,
    name        text        NOT NULL,
    order_count integer     NOT NULL DEFAULT 0,
    last_order_at timestamptz
);

CREATE TABLE pg4all_loadtest.products (
    id          integer PRIMARY KEY,
    name        text    NOT NULL,
    price_cents integer NOT NULL,
    -- Deliberately huge: a depleting stock column would make later
    -- transactions no-ops and quietly flatten the write load.
    stock       integer NOT NULL DEFAULT 1000000000
);

CREATE TABLE pg4all_loadtest.orders (
    id          bigserial PRIMARY KEY,
    customer_id integer     NOT NULL REFERENCES pg4all_loadtest.customers(id),
    total_cents integer     NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pg4all_loadtest.order_items (
    id               bigserial PRIMARY KEY,
    order_id         bigint  NOT NULL REFERENCES pg4all_loadtest.orders(id),
    product_id       integer NOT NULL REFERENCES pg4all_loadtest.products(id),
    quantity         integer NOT NULL,
    unit_price_cents integer NOT NULL
);

CREATE INDEX ON pg4all_loadtest.orders (customer_id);
CREATE INDEX ON pg4all_loadtest.orders (created_at);
CREATE INDEX ON pg4all_loadtest.order_items (order_id);

INSERT INTO pg4all_loadtest.customers (id, name)
SELECT g, 'customer ' || g FROM generate_series(1, 5000) AS g;

INSERT INTO pg4all_loadtest.products (id, name, price_cents)
SELECT g, 'product ' || g, 500 + (g % 20000) FROM generate_series(1, 1000) AS g;

ANALYZE pg4all_loadtest.customers;
ANALYZE pg4all_loadtest.products;
