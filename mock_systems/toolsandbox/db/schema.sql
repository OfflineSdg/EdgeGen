-- ToolSandbox Mock Database Schema
-- Generated for synthesis testing of the ToolSandbox agent
-- Entities: device settings, contacts, messages, reminders

PRAGMA foreign_keys = ON;

-- ============================================================================
-- DEVICE SETTINGS (single-row table for device state)
-- ============================================================================
CREATE TABLE IF NOT EXISTS device_settings (
    device_id           TEXT PRIMARY KEY,
    cellular            INTEGER NOT NULL DEFAULT 1,   -- 0/1 boolean
    wifi                INTEGER NOT NULL DEFAULT 1,   -- 0/1 boolean
    location_service    INTEGER NOT NULL DEFAULT 1,   -- 0/1 boolean
    low_battery_mode    INTEGER NOT NULL DEFAULT 0,   -- 0/1 boolean
    latitude            REAL NOT NULL DEFAULT 37.7749,
    longitude           REAL NOT NULL DEFAULT -122.4194,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ============================================================================
-- CONTACTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS contacts (
    person_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    phone_number    TEXT NOT NULL,
    relationship    TEXT,
    is_self         INTEGER NOT NULL DEFAULT 0,  -- 0/1 boolean, max 1 row with is_self=1
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_contacts_name ON contacts(name);
CREATE INDEX idx_contacts_phone ON contacts(phone_number);
CREATE INDEX idx_contacts_is_self ON contacts(is_self);

-- ============================================================================
-- MESSAGES
-- ============================================================================
CREATE TABLE IF NOT EXISTS messages (
    message_id              TEXT PRIMARY KEY,
    sender_person_id        TEXT NOT NULL REFERENCES contacts(person_id),
    sender_phone_number     TEXT NOT NULL,
    recipient_person_id     TEXT NOT NULL REFERENCES contacts(person_id),
    recipient_phone_number  TEXT NOT NULL,
    content                 TEXT NOT NULL,
    creation_timestamp      REAL NOT NULL,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_messages_sender ON messages(sender_person_id);
CREATE INDEX idx_messages_recipient ON messages(recipient_person_id);
CREATE INDEX idx_messages_sender_phone ON messages(sender_phone_number);
CREATE INDEX idx_messages_recipient_phone ON messages(recipient_phone_number);
CREATE INDEX idx_messages_timestamp ON messages(creation_timestamp);

-- ============================================================================
-- REMINDERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS reminders (
    reminder_id         TEXT PRIMARY KEY,
    content             TEXT NOT NULL,
    creation_timestamp  REAL NOT NULL,
    reminder_timestamp  REAL NOT NULL,
    latitude            REAL,
    longitude           REAL,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_reminders_reminder_ts ON reminders(reminder_timestamp);
CREATE INDEX idx_reminders_creation_ts ON reminders(creation_timestamp);
