CREATE ROLE reporting_reader NOLOGIN;

CREATE TABLE customers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL UNIQUE,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE order_number_seq START WITH 1000 OWNED BY NONE;

CREATE TABLE orders (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_number bigint NOT NULL DEFAULT nextval('order_number_seq'),
    customer_id bigint NOT NULL,
    amount numeric(12, 2) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT orders_amount_positive CHECK (amount > 0),
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id),
    CONSTRAINT orders_order_number_unique UNIQUE (order_number)
);

CREATE INDEX orders_customer_created_idx ON orders (customer_id, created_at DESC);

INSERT INTO customers (email) VALUES ('alice@example.test'), ('bob@example.test');
INSERT INTO orders (customer_id, amount) VALUES (1, 19.95), (1, 42.00), (2, 7.50);

CREATE VIEW active_customers AS
SELECT id, email FROM customers WHERE active;

CREATE FUNCTION customer_order_total(requested_customer_id bigint)
RETURNS numeric
LANGUAGE sql
STABLE
AS $function$
    SELECT COALESCE(SUM(amount), 0) FROM orders WHERE customer_id = requested_customer_id
$function$;

CREATE PROCEDURE deactivate_customer(requested_customer_id bigint)
LANGUAGE sql
AS $procedure$
    UPDATE customers SET active = false WHERE id = requested_customer_id
$procedure$;

GRANT USAGE ON SCHEMA public TO reporting_reader;
GRANT SELECT ON customers, orders, active_customers TO reporting_reader;
GRANT SELECT ON SEQUENCE order_number_seq TO reporting_reader;

