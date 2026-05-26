-- Airline Mock Database Schema
-- Generated for synthesis testing

-- Reference: Airports
CREATE TABLE airports (
    iata TEXT PRIMARY KEY,
    city TEXT NOT NULL
);

-- Users
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    dob TEXT NOT NULL,
    address1 TEXT NOT NULL,
    address2 TEXT,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    state TEXT NOT NULL,
    zip TEXT NOT NULL,
    membership TEXT NOT NULL DEFAULT 'regular',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_membership ON users(membership);
CREATE INDEX idx_users_email ON users(email);

-- Payment Methods (credit cards, gift cards, certificates)
CREATE TABLE payment_methods (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    source TEXT NOT NULL,
    brand TEXT,
    last_four TEXT,
    amount REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_payment_methods_user ON payment_methods(user_id);
CREATE INDEX idx_payment_methods_source ON payment_methods(source);

-- Saved Passengers per user
CREATE TABLE saved_passengers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    dob TEXT NOT NULL
);

CREATE INDEX idx_saved_passengers_user ON saved_passengers(user_id);

-- Flights (recurring flight routes)
CREATE TABLE flights (
    flight_number TEXT PRIMARY KEY,
    origin TEXT NOT NULL REFERENCES airports(iata),
    destination TEXT NOT NULL REFERENCES airports(iata),
    scheduled_departure_time_est TEXT NOT NULL,
    scheduled_arrival_time_est TEXT NOT NULL
);

CREATE INDEX idx_flights_origin ON flights(origin);
CREATE INDEX idx_flights_destination ON flights(destination);
CREATE INDEX idx_flights_origin_dest ON flights(origin, destination);

-- Flight date instances (specific date occurrences of a flight)
CREATE TABLE flight_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number TEXT NOT NULL REFERENCES flights(flight_number),
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    available_seats_basic_economy INTEGER DEFAULT 0,
    available_seats_economy INTEGER DEFAULT 0,
    available_seats_business INTEGER DEFAULT 0,
    price_basic_economy INTEGER DEFAULT 0,
    price_economy INTEGER DEFAULT 0,
    price_business INTEGER DEFAULT 0,
    actual_departure_time_est TEXT,
    actual_arrival_time_est TEXT,
    estimated_departure_time_est TEXT,
    estimated_arrival_time_est TEXT,
    UNIQUE(flight_number, date)
);

CREATE INDEX idx_flight_dates_flight ON flight_dates(flight_number);
CREATE INDEX idx_flight_dates_date ON flight_dates(date);
CREATE INDEX idx_flight_dates_status ON flight_dates(status);
CREATE INDEX idx_flight_dates_flight_date ON flight_dates(flight_number, date);

-- Reservations
CREATE TABLE reservations (
    reservation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    flight_type TEXT NOT NULL,
    cabin TEXT NOT NULL,
    total_baggages INTEGER NOT NULL DEFAULT 0,
    nonfree_baggages INTEGER NOT NULL DEFAULT 0,
    insurance TEXT NOT NULL DEFAULT 'no',
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_reservations_user ON reservations(user_id);
CREATE INDEX idx_reservations_status ON reservations(status);

-- Reservation flights (legs of a reservation)
CREATE TABLE reservation_flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    flight_number TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    date TEXT NOT NULL,
    price INTEGER NOT NULL
);

CREATE INDEX idx_reservation_flights_reservation ON reservation_flights(reservation_id);

-- Reservation passengers
CREATE TABLE reservation_passengers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    dob TEXT NOT NULL
);

CREATE INDEX idx_reservation_passengers_reservation ON reservation_passengers(reservation_id);

-- Reservation payment history
CREATE TABLE reservation_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    payment_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_reservation_payments_reservation ON reservation_payments(reservation_id);
