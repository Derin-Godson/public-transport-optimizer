-- MySQL 8.0-compatible version, auto-derived from schema.sql for production deployment.
-- Run: mysql -u root -p < schema_mysql.sql (after CREATE DATABASE + USE)

-- =====================================================================
-- PUBLIC TRANSPORT JOURNEY OPTIMIZER — DATABASE SCHEMA
-- Engine: SQLite (bundled, zero-setup). A MySQL-compatible version of
-- this same schema is in database/schema_mysql.sql for production use.
-- =====================================================================

-- ---------------------------------------------------------------------
-- A. TRANSPORT_OPERATOR
-- ---------------------------------------------------------------------
CREATE TABLE transport_operator (
    operator_id     INT AUTO_INCREMENT PRIMARY KEY,
    operator_name   VARCHAR(255) NOT NULL UNIQUE,
    operator_type   VARCHAR(255) NOT NULL,           -- e.g. State Corp, City Transport
    region          VARCHAR(255),
    source_reference VARCHAR(255)
);

-- ---------------------------------------------------------------------
-- C. DEPOT
-- ---------------------------------------------------------------------
CREATE TABLE depot (
    depot_id        INT AUTO_INCREMENT PRIMARY KEY,
    depot_name      VARCHAR(255) NOT NULL,
    district        VARCHAR(255),
    region          VARCHAR(255),
    operator_id     INT NOT NULL REFERENCES transport_operator(operator_id),
    UNIQUE(depot_name, operator_id)
);

-- ---------------------------------------------------------------------
-- B. BUS  (kept for schema completeness — populated only where the
-- source data names individual vehicles; none of the supplied datasets
-- list bus numbers, so this table stays empty rather than being faked)
-- ---------------------------------------------------------------------
CREATE TABLE bus (
    bus_id          INT AUTO_INCREMENT PRIMARY KEY,
    operator_id     INT NOT NULL REFERENCES transport_operator(operator_id),
    bus_number      VARCHAR(255),
    bus_type        VARCHAR(255),
    service_type    VARCHAR(255),
    depot_id        INT REFERENCES depot(depot_id),
    status          VARCHAR(255)
);

-- ---------------------------------------------------------------------
-- D. STOP
-- ---------------------------------------------------------------------
CREATE TABLE stop (
    stop_id         INT AUTO_INCREMENT PRIMARY KEY,
    stop_name       VARCHAR(255) NOT NULL,
    district        VARCHAR(255),
    city            VARCHAR(255),
    latitude        DECIMAL(10,2),      -- NULL when not present in source data
    longitude       DECIMAL(10,2),      -- NULL when not present in source data
    stop_type       VARCHAR(255),      -- Bus Stand / Depot / Town / GTFS Stop
    UNIQUE(stop_name, city)
);
CREATE INDEX idx_stop_name ON stop(stop_name);

-- ---------------------------------------------------------------------
-- E. ROUTE
-- ---------------------------------------------------------------------
CREATE TABLE route (
    route_id            INT AUTO_INCREMENT PRIMARY KEY,
    operator_id         INT NOT NULL REFERENCES transport_operator(operator_id),
    route_number        VARCHAR(255) NOT NULL,
    route_name          VARCHAR(255) NOT NULL,
    source_stop_id       INT NOT NULL REFERENCES stop(stop_id),
    destination_stop_id  INT NOT NULL REFERENCES stop(stop_id),
    via                 VARCHAR(255),
    service_type        VARCHAR(255),               -- Ordinary / Express / Ultra Deluxe / End-to-End ...
    journey_duration_min INT,            -- NULL if not available
    total_distance_km   DECIMAL(10,2),                -- NULL if not available
    base_fare           DECIMAL(10,2),                -- NULL if not available
    total_services      INT,             -- daily service count, if known
    status              VARCHAR(255) DEFAULT 'ACTIVE',
    source_reference    VARCHAR(255) NOT NULL,
    CHECK (source_stop_id <> destination_stop_id)
);
CREATE INDEX idx_route_number ON route(route_number);
CREATE INDEX idx_route_source ON route(source_stop_id);
CREATE INDEX idx_route_dest   ON route(destination_stop_id);
CREATE INDEX idx_route_operator ON route(operator_id);

-- ---------------------------------------------------------------------
-- F. ROUTE_STOP  (ordered stop sequence per route — normalized, no
-- comma-joined stop lists)
-- ---------------------------------------------------------------------
CREATE TABLE route_stop (
    route_stop_id           INT AUTO_INCREMENT PRIMARY KEY,
    route_id                INT NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    stop_id                 INT NOT NULL REFERENCES stop(stop_id),
    stop_sequence            INT NOT NULL,
    distance_from_origin_km DECIMAL(10,2),             -- NULL if not available
    boarding_allowed        INT DEFAULT 1,
    dropoff_allowed         INT DEFAULT 1,
    UNIQUE(route_id, stop_sequence),
    UNIQUE(route_id, stop_id)
);
CREATE INDEX idx_route_stop_route ON route_stop(route_id);
CREATE INDEX idx_route_stop_stop  ON route_stop(stop_id);

-- ---------------------------------------------------------------------
-- G. SCHEDULE
-- ---------------------------------------------------------------------
CREATE TABLE schedule (
    schedule_id       INT AUTO_INCREMENT PRIMARY KEY,
    route_id          INT NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    departure_stop_id INT NOT NULL REFERENCES stop(stop_id),
    arrival_stop_id   INT NOT NULL REFERENCES stop(stop_id),
    departure_time    VARCHAR(255) NOT NULL,   -- 'HH:MM'
    arrival_time      VARCHAR(255),            -- NULL if not available
    operating_day     VARCHAR(255) DEFAULT 'DAILY',
    service_date      VARCHAR(255),            -- specific date if the source names one
    schedule_source   VARCHAR(255) NOT NULL
);
CREATE INDEX idx_schedule_route ON schedule(route_id);
CREATE INDEX idx_schedule_dep_time ON schedule(departure_time);

-- ---------------------------------------------------------------------
-- H. FARE
-- ---------------------------------------------------------------------
CREATE TABLE fare (
    fare_id          INT AUTO_INCREMENT PRIMARY KEY,
    route_id         INT NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    from_stop_id     INT NOT NULL REFERENCES stop(stop_id),
    to_stop_id       INT NOT NULL REFERENCES stop(stop_id),
    fare_amount      DECIMAL(10,2),             -- NULL if not available
    fare_type        VARCHAR(255),
    effective_date   VARCHAR(255),
    source_reference VARCHAR(255) NOT NULL
);
CREATE INDEX idx_fare_route ON fare(route_id);

-- Reference table: the official TNSTC Tirunelveli per-km fare slab
-- (fare policy, not tied to a specific route/stop pair — kept separate
-- from FARE on purpose, see README §Data Provenance)
CREATE TABLE fare_policy_slab (
    slab_id          INT AUTO_INCREMENT PRIMARY KEY,
    service_type     VARCHAR(255) NOT NULL,     -- Ordinary / Express / Semi Luxury / Deluxe / LSS ...
    category         VARCHAR(255) NOT NULL,     -- RURAL / CITY-URBAN
    min_km           INT,
    fare_per_km_paise DECIMAL(10,2),
    min_fare_rs      DECIMAL(10,2),
    min_fare_km      INT,
    effective_from   VARCHAR(255),
    source_reference VARCHAR(255) NOT NULL
);

-- ---------------------------------------------------------------------
-- I. TRANSFER  (precomputed known interchange points; the live
-- optimizer also discovers transfers dynamically via the stop graph)
-- ---------------------------------------------------------------------
CREATE TABLE transfer (
    transfer_id              INT AUTO_INCREMENT PRIMARY KEY,
    from_route_id            INT NOT NULL REFERENCES route(route_id),
    to_route_id              INT NOT NULL REFERENCES route(route_id),
    transfer_stop_id         INT NOT NULL REFERENCES stop(stop_id),
    minimum_transfer_minutes INT DEFAULT 10,
    transfer_type             VARCHAR(255) DEFAULT 'SAME_STOP'
);

-- ---------------------------------------------------------------------
-- J. DELAY_HISTORY — intentionally left EMPTY.
-- None of the supplied datasets contain historical delay records, so
-- no rows are generated here. The "Most Reliable" optimizer preference
-- degrades gracefully (see backend/optimizer.py) when this is empty.
-- ---------------------------------------------------------------------
CREATE TABLE delay_history (
    delay_id         INT AUTO_INCREMENT PRIMARY KEY,
    route_id         INT NOT NULL REFERENCES route(route_id),
    stop_id          INT REFERENCES stop(stop_id),
    delay_date       VARCHAR(255),
    scheduled_time   VARCHAR(255),
    actual_time      VARCHAR(255),
    delay_minutes    INT,
    delay_reason     VARCHAR(255),
    source_reference VARCHAR(255)
);

-- ---------------------------------------------------------------------
-- K. DATA_SOURCE
-- ---------------------------------------------------------------------
CREATE TABLE data_source (
    source_id      INT AUTO_INCREMENT PRIMARY KEY,
    source_name    VARCHAR(255) NOT NULL,
    source_url     VARCHAR(255),
    source_type    VARCHAR(255) NOT NULL,   -- PDF / CSV / GTFS
    date_accessed  VARCHAR(255) NOT NULL,
    data_date      VARCHAR(255),
    description    VARCHAR(255)
);

-- =====================================================================
-- VIEWS
-- =====================================================================

CREATE VIEW vw_route_details AS
SELECT r.route_id, r.route_number, r.route_name,
       o.operator_name, o.operator_type,
       src.stop_name AS source_stop, dst.stop_name AS destination_stop,
       r.via, r.service_type, r.journey_duration_min, r.total_distance_km,
       r.base_fare, r.total_services, r.status, r.source_reference
FROM route r
JOIN transport_operator o ON o.operator_id = r.operator_id
JOIN stop src ON src.stop_id = r.source_stop_id
JOIN stop dst ON dst.stop_id = r.destination_stop_id;

CREATE VIEW vw_route_stops AS
SELECT rs.route_id, r.route_number, rs.stop_sequence, s.stop_name,
       rs.distance_from_origin_km, rs.boarding_allowed, rs.dropoff_allowed
FROM route_stop rs
JOIN route r ON r.route_id = rs.route_id
JOIN stop s ON s.stop_id = rs.stop_id
ORDER BY rs.route_id, rs.stop_sequence;

CREATE VIEW vw_available_journeys AS
SELECT sch.schedule_id, r.route_id, r.route_number, r.route_name,
       ds.stop_name AS departure_stop, arr.stop_name AS arrival_stop,
       sch.departure_time, sch.arrival_time, sch.operating_day,
       r.base_fare, r.journey_duration_min, r.total_distance_km
FROM schedule sch
JOIN route r ON r.route_id = sch.route_id
JOIN stop ds ON ds.stop_id = sch.departure_stop_id
JOIN stop arr ON arr.stop_id = sch.arrival_stop_id;

CREATE VIEW vw_route_delay_statistics AS
SELECT r.route_id, r.route_number,
       COUNT(d.delay_id) AS delay_records,
       AVG(d.delay_minutes) AS avg_delay_minutes,
       MAX(d.delay_minutes) AS max_delay_minutes
FROM route r
LEFT JOIN delay_history d ON d.route_id = r.route_id
GROUP BY r.route_id, r.route_number;
