-- One ecommerce purchase, as a pgbench transaction.
--
-- Shaped to touch the things the dashboard watches: a read, three writes
-- and an index insert inside a single transaction, so it generates commit
-- volume, WAL, buffer churn and lock activity rather than just SELECT load.
--
-- Bounds come from -D on the command line rather than pgbench's :scale,
-- which is only set for its own built-in schema.

\set customer_id random(1, :ncustomers)
\set product_id  random(1, :nproducts)
\set quantity    random(1, 5)

BEGIN;

-- Price is read inside the transaction on purpose: a real checkout reads
-- then writes, and that read is what makes the cache-hit panel move.
SELECT price_cents AS unit_price
  FROM pg4all_loadtest.products
 WHERE id = :product_id \gset

UPDATE pg4all_loadtest.products
   SET stock = stock - :quantity
 WHERE id = :product_id;

INSERT INTO pg4all_loadtest.orders (customer_id, total_cents)
VALUES (:customer_id, :unit_price * :quantity)
RETURNING id AS order_id \gset

INSERT INTO pg4all_loadtest.order_items
       (order_id, product_id, quantity, unit_price_cents)
VALUES (:order_id, :product_id, :quantity, :unit_price);

UPDATE pg4all_loadtest.customers
   SET order_count = order_count + 1,
       last_order_at = now()
 WHERE id = :customer_id;

END;
