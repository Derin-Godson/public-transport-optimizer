import os
from flask import Flask, request, jsonify
from flask_cors import CORS

from db import get_conn
from optimizer import search_journeys, find_stops

app = Flask(__name__)
CORS(app)


# ---------------------------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
@app.route("/api/stops")
def stops():
    q = request.args.get("q", "")
    conn = get_conn()
    try:
        if q:
            rows = find_stops(conn, q)
        else:
            rows = conn.execute(
                "SELECT stop_id, stop_name, city, district FROM stop ORDER BY stop_name LIMIT 50"
            ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
@app.route("/api/operators")
def operators():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM transport_operator").fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
@app.route("/api/routes")
def routes():
    q = request.args.get("q", "")
    operator_id = request.args.get("operator_id")
    conn = get_conn()
    try:
        sql = "SELECT * FROM vw_route_details WHERE 1=1"
        params = []
        if q:
            sql += " AND (route_number LIKE ? OR route_name LIKE ? OR source_stop LIKE ? OR destination_stop LIKE ?)"
            params += [f"%{q}%"] * 4
        if operator_id:
            sql += " AND route_id IN (SELECT route_id FROM route WHERE operator_id=?)"
            params.append(operator_id)
        sql += " LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/routes/<int:route_id>")
def route_detail(route_id):
    conn = get_conn()
    try:
        route = conn.execute("SELECT * FROM vw_route_details WHERE route_id=?", (route_id,)).fetchone()
        if not route:
            return jsonify({"error": "NOT_FOUND"}), 404
        stops_ = conn.execute("SELECT * FROM vw_route_stops WHERE route_id=?", (route_id,)).fetchall()
        schedules = conn.execute(
            "SELECT * FROM schedule WHERE route_id=? ORDER BY departure_time", (route_id,)
        ).fetchall()
        fares = conn.execute("SELECT * FROM fare WHERE route_id=?", (route_id,)).fetchall()
        return jsonify({
            "route": dict(route),
            "stops": [dict(s) for s in stops_],
            "schedules": [dict(s) for s in schedules],
            "fares": [dict(f) for f in fares],
        })
    finally:
        conn.close()


# ---------------------------------------------------------------------------
@app.route("/api/schedules")
def schedules():
    route_id = request.args.get("route_id")
    conn = get_conn()
    try:
        if not route_id:
            return jsonify({"error": "route_id is required"}), 400
        rows = conn.execute(
            "SELECT * FROM vw_available_journeys WHERE route_id=? ORDER BY departure_time", (route_id,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
@app.route("/api/journeys")
def journeys():
    """Alias of /api/optimize with no preference weighting applied beyond DEFAULT."""
    return optimize()


@app.route("/api/optimize")
def optimize():
    source = request.args.get("from", "").strip()
    dest = request.args.get("to", "").strip()
    dep_time = request.args.get("departure_time") or None
    preference = request.args.get("preference", "DEFAULT").upper()

    if not source:
        return jsonify({"error": "SOURCE_MISSING", "message": "Please select a valid starting location."}), 400
    if not dest:
        return jsonify({"error": "DEST_MISSING", "message": "Please select a valid destination."}), 400

    result = search_journeys(source, dest, dep_time, preference)
    status = 200
    if result.get("error") in ("SOURCE_NOT_FOUND",):
        status = 404
    elif result.get("error") in ("DEST_NOT_FOUND",):
        status = 404
    elif result.get("error") == "SAME_STOP":
        status = 400
    elif result.get("error") == "NO_ROUTE_FOUND":
        status = 200  # valid response with a fallback payload, not a server error
    return jsonify(result), status


# ---------------------------------------------------------------------------
@app.route("/api/delays")
def delays():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM vw_route_delay_statistics WHERE delay_records > 0").fetchall()
        return jsonify({
            "records": [dict(r) for r in rows],
            "note": "No historical delay data was supplied in any of the source datasets, so this "
                    "table is empty. The 'Most Reliable' optimization preference automatically falls "
                    "back to fewest-transfers + fastest-journey scoring when no delay data exists.",
        })
    finally:
        conn.close()


# ---------------------------------------------------------------------------
@app.route("/api/dashboard")
def dashboard():
    conn = get_conn()
    try:
        def count(sql):
            return conn.execute(sql).fetchone()[0]

        stats = {
            "total_operators": count("SELECT COUNT(*) FROM transport_operator"),
            "total_routes": count("SELECT COUNT(*) FROM route"),
            "total_stops": count("SELECT COUNT(*) FROM stop"),
            "total_schedules": count("SELECT COUNT(*) FROM schedule"),
            "total_route_stops": count("SELECT COUNT(*) FROM route_stop"),
            "total_transfers_defined": count("SELECT COUNT(*) FROM transfer"),
            "total_delay_records": count("SELECT COUNT(*) FROM delay_history"),
        }

        routes_by_operator = conn.execute(
            "SELECT o.operator_name, COUNT(*) n FROM route r JOIN transport_operator o "
            "ON o.operator_id=r.operator_id GROUP BY o.operator_name ORDER BY n DESC"
        ).fetchall()

        avg_fare_by_operator = conn.execute(
            "SELECT o.operator_name, ROUND(AVG(r.base_fare),2) avg_fare, COUNT(r.base_fare) n "
            "FROM route r JOIN transport_operator o ON o.operator_id=r.operator_id "
            "WHERE r.base_fare IS NOT NULL GROUP BY o.operator_name"
        ).fetchall()

        avg_journey_time = conn.execute(
            "SELECT o.operator_name, ROUND(AVG(r.journey_duration_min),1) avg_min, COUNT(r.journey_duration_min) n "
            "FROM route r JOIN transport_operator o ON o.operator_id=r.operator_id "
            "WHERE r.journey_duration_min IS NOT NULL GROUP BY o.operator_name"
        ).fetchall()

        most_connected_stops = conn.execute(
            "SELECT s.stop_name, COUNT(DISTINCT rs.route_id) n FROM route_stop rs "
            "JOIN stop s ON s.stop_id=rs.stop_id GROUP BY s.stop_name ORDER BY n DESC LIMIT 10"
        ).fetchall()

        routes_by_district = conn.execute(
            "SELECT COALESCE(s.district,'Not Available') district, COUNT(*) n FROM route r "
            "JOIN stop s ON s.stop_id=r.source_stop_id GROUP BY district ORDER BY n DESC LIMIT 12"
        ).fetchall()

        data_sources = conn.execute("SELECT * FROM data_source").fetchall()

        return jsonify({
            "stats": stats,
            "routes_by_operator": [dict(r) for r in routes_by_operator],
            "avg_fare_by_operator": [dict(r) for r in avg_fare_by_operator],
            "avg_journey_time_by_operator": [dict(r) for r in avg_journey_time],
            "most_connected_stops": [dict(r) for r in most_connected_stops],
            "routes_by_district": [dict(r) for r in routes_by_district],
            "data_sources": [dict(r) for r in data_sources],
        })
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, port=port)
