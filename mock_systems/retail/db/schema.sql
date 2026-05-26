-- Retail Mock Database Schema
-- Generated for synthesis testing of the tau2bench retail domain

-- Products table
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Product variants (items) table
CREATE TABLE variants (
    item_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    options TEXT NOT NULL DEFAULT '{}',  -- JSON dict of option_name -> value
    available INTEGER NOT NULL DEFAULT 1,  -- boolean 0/1
    price REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_variants_product_id ON variants(product_id);

-- Users table
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    address1 TEXT NOT NULL DEFAULT '',
    address2 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT 'USA',
    zip TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_name_zip ON users(first_name, last_name, zip);

-- Payment methods table (polymorphic: credit_card, gift_card, paypal)
CREATE TABLE payment_methods (
    payment_method_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    source TEXT NOT NULL CHECK(source IN ('credit_card', 'gift_card', 'paypal')),
    -- credit_card fields
    brand TEXT,          -- e.g. 'visa', 'mastercard'
    last_four TEXT,      -- last 4 digits
    -- gift_card fields
    balance REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_payment_methods_user_id ON payment_methods(user_id);

-- Orders table
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'pending (item modified)', 'processed', 'delivered', 'cancelled',
        'exchange requested', 'return requested'
    )),
    -- Shipping address (snapshot at order time)
    address1 TEXT NOT NULL DEFAULT '',
    address2 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT 'USA',
    zip TEXT NOT NULL DEFAULT '',
    -- Cancellation
    cancel_reason TEXT CHECK(cancel_reason IN ('no longer needed', 'ordered by mistake')),
    -- Exchange fields
    exchange_items TEXT,                -- JSON list of item_ids
    exchange_new_items TEXT,            -- JSON list of new item_ids
    exchange_payment_method_id TEXT,
    exchange_price_difference REAL,
    -- Return fields
    return_items TEXT,                  -- JSON list of item_ids
    return_payment_method_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);

-- Order items table
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    item_id TEXT NOT NULL,   -- references variants.item_id but not FK (item may change)
    name TEXT NOT NULL,
    price REAL NOT NULL,
    options TEXT NOT NULL DEFAULT '{}'  -- JSON dict
);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);

-- Order fulfillments table
CREATE TABLE order_fulfillments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    tracking_ids TEXT NOT NULL DEFAULT '[]',  -- JSON list of tracking IDs
    item_ids TEXT NOT NULL DEFAULT '[]'       -- JSON list of item IDs
);
CREATE INDEX idx_order_fulfillments_order_id ON order_fulfillments(order_id);

-- Order payment history table
CREATE TABLE order_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    transaction_type TEXT NOT NULL CHECK(transaction_type IN ('payment', 'refund')),
    amount REAL NOT NULL,
    payment_method_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_order_payments_order_id ON order_payments(order_id);
