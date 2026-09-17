CREATE DATABASE IF NOT EXISTS backup_source;
USE backup_source;

CREATE TABLE audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    item_name VARCHAR(100) NOT NULL
);

CREATE TABLE items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    quantity INT NOT NULL,
    CONSTRAINT items_name_unique UNIQUE (name),
    CONSTRAINT items_quantity_positive CHECK (quantity >= 0)
);

CREATE INDEX items_quantity_idx ON items (quantity);

DELIMITER //
CREATE TRIGGER items_after_insert AFTER INSERT ON items
FOR EACH ROW BEGIN
    INSERT INTO audit_log(item_name) VALUES (NEW.name);
END//

CREATE FUNCTION item_count() RETURNS INT
READS SQL DATA
BEGIN
    RETURN (SELECT COUNT(*) FROM items);
END//

CREATE PROCEDURE add_item(IN requested_name VARCHAR(100), IN requested_quantity INT)
BEGIN
    INSERT INTO items(name, quantity) VALUES (requested_name, requested_quantity);
END//

CREATE EVENT future_inventory_event
ON SCHEDULE AT CURRENT_TIMESTAMP + INTERVAL 1 DAY
DO INSERT INTO audit_log(item_name) VALUES ('scheduled')//
DELIMITER ;

INSERT INTO items(name, quantity) VALUES ('alpha', 3), ('beta', 7);
CREATE VIEW available_items AS SELECT id, name FROM items WHERE quantity > 0;
