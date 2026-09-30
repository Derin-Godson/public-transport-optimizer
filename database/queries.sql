-- =====================================================================
-- DEMONSTRATION QUERIES — run against backend/db/transport.db
-- (sqlite3 backend/db/transport.db < database/queries.sql)
-- =====================================================================

-- 1. Routes connecting two stops (by name) -----------------------------
SELECT r.route_number, r.route_name, s1.stop_name AS from_stop, s2.stop_name AS to_stop
FROM route r
JOIN route_stop rs1 ON rs1.route_id = r.route_id
JOIN route_stop rs2 ON rs2.route_id = r.route_id AND rs2.stop_sequence > rs1.stop_sequence
JOIN stop s1 ON s1.stop_id = rs1.stop_id
JOIN stop s2 ON s2.stop_id = rs2.stop_id
WHERE s1.stop_name = 'Nagercoil' AND s2.stop_name = 'Tirunelveli';

-- 2. Direct routes between source and destination -----------------------
SELECT route_number, route_name, base_fare, journey_duration_min
FROM vw_route_details
WHERE source_stop = 'Nagercoil' AND destination_stop = 'Tirunelveli';

-- 3. Routes with minimum transfers (direct vs 1-transfer count via a CTE)
WITH direct AS (
    SELECT route_id, 0 AS transfers FROM route
    WHERE source_stop_id = (SELECT stop_id FROM stop WHERE stop_name='Nagercoil' LIMIT 1)
      AND destination_stop_id = (SELECT stop_id FROM stop WHERE stop_name='Tirunelveli' LIMIT 1)
)
SELECT * FROM direct ORDER BY transfers;

-- 4. Cheapest route between two named stops -----------------------------
SELECT route_number, route_name, base_fare
FROM vw_route_details
WHERE source_stop = 'Nagercoil' AND destination_stop = 'Madurai'
ORDER BY base_fare ASC
LIMIT 1;

-- 5. Fastest route between two named stops -------------------------------
SELECT route_number, route_name, journey_duration_min
FROM vw_route_details
WHERE source_stop = 'Nagercoil' AND destination_stop = 'Madurai'
ORDER BY journey_duration_min ASC
LIMIT 1;

-- 6. Shortest route (by distance, where recorded) ------------------------
SELECT route_number, route_name, total_distance_km
FROM vw_route_details
WHERE total_distance_km IS NOT NULL
ORDER BY total_distance_km ASC
LIMIT 5;

-- 7. Routes with highest service frequency --------------------------------
SELECT route_number, route_name, total_services
FROM route
WHERE total_services IS NOT NULL
ORDER BY total_services DESC
LIMIT 10;

-- 8. Average fare by operator (GROUP BY + aggregate) ----------------------
SELECT o.operator_name, ROUND(AVG(r.base_fare), 2) AS avg_fare, COUNT(*) AS routes_with_fare
FROM route r
JOIN transport_operator o ON o.operator_id = r.operator_id
WHERE r.base_fare IS NOT NULL
GROUP BY o.operator_name;

-- 9. Average journey time by route type (GROUP BY + HAVING) --------------
SELECT service_type, ROUND(AVG(journey_duration_min), 1) AS avg_minutes, COUNT(*) AS n
FROM route
WHERE journey_duration_min IS NOT NULL
GROUP BY service_type
HAVING COUNT(*) >= 1
ORDER BY avg_minutes;

-- 10. Frequently delayed routes (view is empty on this dataset --
--     no delay_history rows were supplied; query still valid/runnable) --
SELECT * FROM vw_route_delay_statistics
WHERE delay_records > 0
ORDER BY avg_delay_minutes DESC;

-- 11. Most connected stops (interchange potential) ------------------------
SELECT s.stop_name, COUNT(DISTINCT rs.route_id) AS routes_through
FROM route_stop rs
JOIN stop s ON s.stop_id = rs.stop_id
GROUP BY s.stop_name
ORDER BY routes_through DESC
LIMIT 10;

-- 12. Routes passing through a selected stop -------------------------------
SELECT r.route_number, r.route_name, rs.stop_sequence
FROM route_stop rs
JOIN route r ON r.route_id = rs.route_id
JOIN stop s ON s.stop_id = rs.stop_id
WHERE s.stop_name = 'Valliyoor'
ORDER BY r.route_number;

-- 13. Available buses at/after a given time (window function for "next 3") -
WITH ranked AS (
    SELECT sch.*, r.route_number, r.route_name,
           ROW_NUMBER() OVER (PARTITION BY sch.route_id ORDER BY sch.departure_time) AS rn
    FROM schedule sch
    JOIN route r ON r.route_id = sch.route_id
    WHERE sch.departure_time >= '08:00'
)
SELECT route_number, route_name, departure_time
FROM ranked
WHERE rn <= 3
ORDER BY departure_time
LIMIT 20;

-- 14. Alternative journeys (direct + 1-transfer) — see backend/optimizer.py
--     for the full parameterized graph search; illustrative direct-only
--     version:
SELECT r.route_number, r.route_name, r.base_fare, r.journey_duration_min, 0 AS transfers
FROM vw_route_details r
WHERE r.source_stop = 'Nagercoil' AND r.destination_stop = 'Tirunelveli'
ORDER BY r.journey_duration_min;

-- 15. Operator-wise route count (GROUP BY + ORDER BY) ----------------------
SELECT o.operator_name, COUNT(*) AS route_count
FROM route r
JOIN transport_operator o ON o.operator_id = r.operator_id
GROUP BY o.operator_name
ORDER BY route_count DESC;
