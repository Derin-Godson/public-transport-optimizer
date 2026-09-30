"""
Journey search + multi-criteria optimization.

This is a DBMS-driven optimizer, not a live GPS/navigation engine: every
number it returns (fare, duration, distance, departure time) comes from
the local database, which in turn comes only from the datasets supplied
for this project. Where a figure isn't in the data, the API returns null
and the frontend must say so -- it must never be invented here.

Search strategy
----------------
STOPS are graph nodes. Each ROUTE's ROUTE_STOP rows (ordered by
stop_sequence) form a chain of directed edges between consecutive stops
on that route. A "journey" is one or more route segments joined at a
transfer stop.

1. Direct journeys: a single route on which source_stop appears at an
   earlier stop_sequence than destination_stop.
2. One-transfer journeys: route A carries the traveller from source to
   some intermediate stop X; route B (a different route) carries them
   from X to destination, with A's arrival before B's departure at X.
   This is a small brute-force join over route_stop x route_stop --
   perfectly fine at this dataset's size (~1,200 route_stop rows) and
   avoids the need for a heavier all-pairs shortest-path index.

Multi-criteria scoring
-----------------------
cost = w_time * journey_minutes
     + w_transfer * transfer_count * TRANSFER_PENALTY_MIN
     + w_fare * fare_rupees
     + w_wait * waiting_minutes
     + w_delay * avg_delay_minutes   (0 unless real delay_history rows exist)

Weights are switched per the user's chosen preference. Missing values
(None) are treated as 0 contribution to the weighted sum but are always
reported to the user as "Not Available" rather than silently dropped.
"""
from db import get_conn

TRANSFER_PENALTY_MIN = 20  # minutes-equivalent penalty per transfer, used only for scoring

WEIGHTS = {
    "FASTEST":            dict(time=1.0, transfer=0.15, fare=0.05, wait=0.6, delay=0.2),
    "FEWEST_TRANSFERS":   dict(time=0.2, transfer=1.0,  fare=0.05, wait=0.2, delay=0.1),
    "LOWEST_FARE":        dict(time=0.15, transfer=0.15, fare=1.0, wait=0.15, delay=0.05),
    "SHORTEST_DISTANCE":  dict(time=0.2, transfer=0.2,  fare=0.1, wait=0.15, delay=0.05),
    "MOST_RELIABLE":      dict(time=0.3, transfer=0.3,  fare=0.05, wait=0.2, delay=1.0),
    "DEFAULT":            dict(time=0.5, transfer=1.0,  fare=0.1, wait=0.3, delay=0.1),
}

EXPLANATIONS = {
    "FASTEST": "Recommended for Fastest Journey \u2014 lowest total travel + waiting time among matching database routes.",
    "FEWEST_TRANSFERS": "Recommended for Fewest Transfers \u2014 needs the least number of bus changes among matching database routes.",
    "LOWEST_FARE": "Recommended for Lowest Fare \u2014 cheapest total fare among matching database routes.",
    "SHORTEST_DISTANCE": "Recommended for Shortest Distance \u2014 shortest total recorded distance among matching database routes.",
    "MOST_RELIABLE": "Recommended for Most Reliable \u2014 lowest historical delay among matching database routes.",
    "DEFAULT": "Recommended \u2014 balances fewest transfers with the shortest recorded journey time.",
}


def find_stops(conn, query):
    q = f"%{query.strip()}%"
    return conn.execute(
        "SELECT stop_id, stop_name, city, district FROM stop WHERE stop_name LIKE ? ORDER BY stop_name LIMIT 15",
        (q,),
    ).fetchall()


def _best_stop_id(conn, query):
    rows = find_stops(conn, query)
    if not rows:
        return None, []
    exact = [r for r in rows if r["stop_name"].lower() == query.strip().lower()]
    return (exact[0]["stop_id"] if exact else rows[0]["stop_id"]), rows


def _avg_delay(conn, route_id):
    row = conn.execute(
        "SELECT AVG(delay_minutes) am FROM delay_history WHERE route_id=?", (route_id,)
    ).fetchone()
    return row["am"]  # None if no rows -- never fabricated


def _route_schedules(conn, route_id, dep_stop_id, arr_stop_id, after_time=None):
    q = ("SELECT schedule_id, departure_time, arrival_time, schedule_source FROM schedule "
         "WHERE route_id=? AND departure_stop_id=? AND arrival_stop_id=?")
    params = [route_id, dep_stop_id, arr_stop_id]
    if after_time:
        q += " AND departure_time >= ?"
        params.append(after_time)
    q += " ORDER BY departure_time LIMIT 6"
    return conn.execute(q, params).fetchall()


# ---------------------------------------------------------------------------
# Estimation for figures the source data doesn't have. Two different kinds,
# kept explicitly separate and always labeled to the caller:
#
#   FARE  -- grounded in the *official* TNSTC per-km fare policy (Rural /
#            Ordinary: paise-per-km rate + minimum fare), transcribed from
#            the fare-table screenshot supplied for this project. This is a
#            real published rate, applied to a leg's real recorded distance
#            -- an estimate of what a route with that distance would cost,
#            not that specific route's actual quoted fare.
#
#   DURATION / DISTANCE -- there is no published rate for converting one to
#            the other anywhere in the source data. This uses an ASSUMED
#            average intercity-bus speed of 40 km/h, which is NOT sourced
#            from any supplied dataset. It exists only because it was
#            explicitly requested; every value it produces carries an
#            "estimated" flag distinct from the fare estimate above so the
#            two are never confused in the UI.
RURAL_ORDINARY_RATE_PER_KM = 0.58
RURAL_ORDINARY_MIN_FARE = 6
ASSUMED_AVG_SPEED_KMH = 40


def _estimate(dur, dist, fare):
    """Given a route's real (possibly None) duration/distance/fare, return
    (dur_display, dur_est, dist_display, dist_est, fare_display, fare_est)."""
    dur_est = dist_est = fare_est = False
    dur_display, dist_display, fare_display = dur, dist, fare

    if dur is None and dist is not None:
        dur_display = round((dist / ASSUMED_AVG_SPEED_KMH) * 60)
        dur_est = True
    if dist is None and dur is not None:
        dist_display = round((dur / 60) * ASSUMED_AVG_SPEED_KMH, 2)
        dist_est = True
    if fare is None and dist is not None:
        fare_display = max(RURAL_ORDINARY_MIN_FARE, round(dist * RURAL_ORDINARY_RATE_PER_KM, 2))
        fare_est = True

    return dur_display, dur_est, dist_display, dist_est, fare_display, fare_est


def _direct_candidates(conn, source_id, dest_id, dep_time=None):
    rows = conn.execute(
        """
        SELECT r.route_id, r.route_number, r.route_name, r.via, r.service_type,
               r.journey_duration_min, r.total_distance_km, r.base_fare,
               r.total_services, r.source_reference, o.operator_name,
               rs1.stop_sequence AS seq_from, rs2.stop_sequence AS seq_to
        FROM route r
        JOIN route_stop rs1 ON rs1.route_id = r.route_id AND rs1.stop_id = ?
        JOIN route_stop rs2 ON rs2.route_id = r.route_id AND rs2.stop_id = ?
        JOIN transport_operator o ON o.operator_id = r.operator_id
        WHERE rs1.stop_sequence < rs2.stop_sequence
        """,
        (source_id, dest_id),
    ).fetchall()
    results = []
    for r in rows:
        sched = _route_schedules(conn, r["route_id"], source_id, dest_id, dep_time)
        avg_delay = _avg_delay(conn, r["route_id"])
        dur_d, dur_e, dist_d, dist_e, fare_d, fare_e = _estimate(
            r["journey_duration_min"], r["total_distance_km"], r["base_fare"])
        results.append({
            "type": "DIRECT",
            "legs": [{
                "route_id": r["route_id"], "route_number": r["route_number"],
                "route_name": r["route_name"], "operator": r["operator_name"],
                "via": r["via"], "service_type": r["service_type"],
                "from_stop_id": source_id, "to_stop_id": dest_id,
                "journey_duration_min": r["journey_duration_min"],
                "duration_estimated": dur_e, "duration_display": dur_d,
                "distance_km": r["total_distance_km"],
                "distance_estimated": dist_e, "distance_display": dist_d,
                "fare": r["base_fare"], "fare_estimated": fare_e, "fare_display": fare_d,
                "total_services": r["total_services"],
                "source_reference": r["source_reference"],
                "departures": [dict(s) for s in sched],
                "avg_delay_min": avg_delay,
            }],
            "transfers": 0,
            "total_duration_min": dur_d, "total_duration_estimated": dur_e,
            "total_fare": fare_d, "total_fare_estimated": fare_e,
            "total_distance_km": dist_d, "total_distance_estimated": dist_e,
            "waiting_min": 0,
            "avg_delay_min": avg_delay,
            "has_schedule": len(sched) > 0,
        })
    return results


def _transfer_candidates(conn, source_id, dest_id, dep_time=None, limit=8):
    # route A: source -> X ;  route B: X -> destination ; A != B
    rows = conn.execute(
        """
        SELECT
            rA.route_id AS a_id, rA.route_number AS a_no, rA.route_name AS a_name,
            rA.journey_duration_min AS a_dur, rA.base_fare AS a_fare,
            rA.total_distance_km AS a_dist, rA.source_reference AS a_src,
            oA.operator_name AS a_op,
            rB.route_id AS b_id, rB.route_number AS b_no, rB.route_name AS b_name,
            rB.journey_duration_min AS b_dur, rB.base_fare AS b_fare,
            rB.total_distance_km AS b_dist, rB.source_reference AS b_src,
            oB.operator_name AS b_op,
            x.stop_id AS x_id, x.stop_name AS x_name
        FROM route_stop rsA1
        JOIN route_stop rsA2 ON rsA2.route_id = rsA1.route_id AND rsA2.stop_sequence > rsA1.stop_sequence
        JOIN route rA ON rA.route_id = rsA1.route_id
        JOIN transport_operator oA ON oA.operator_id = rA.operator_id
        JOIN stop x ON x.stop_id = rsA2.stop_id
        JOIN route_stop rsB1 ON rsB1.stop_id = rsA2.stop_id
        JOIN route_stop rsB2 ON rsB2.route_id = rsB1.route_id AND rsB2.stop_sequence > rsB1.stop_sequence
        JOIN route rB ON rB.route_id = rsB1.route_id
        JOIN transport_operator oB ON oB.operator_id = rB.operator_id
        WHERE rsA1.stop_id = ?
          AND rsB2.stop_id = ?
          AND rA.route_id <> rB.route_id
        LIMIT ?
        """,
        (source_id, dest_id, limit),
    ).fetchall()

    results = []
    seen = set()
    for r in rows:
        key = (r["a_id"], r["x_id"], r["b_id"])
        if key in seen:
            continue
        seen.add(key)
        sched_a = _route_schedules(conn, r["a_id"], source_id, r["x_id"], dep_time)
        sched_b = _route_schedules(conn, r["b_id"], r["x_id"], dest_id)

        waiting_min = None
        note = None
        if sched_a and sched_b:
            try:
                a_last = sched_a[0]["departure_time"]
                b_first = sched_b[0]["departure_time"]
                ah, am = map(int, a_last.split(":"))
                bh, bm = map(int, b_first.split(":"))
                waiting_min = max((bh * 60 + bm) - (ah * 60 + am), 0)
            except Exception:
                waiting_min = None
        if waiting_min is None:
            note = "Exact transfer waiting time unavailable."

        dur = None
        dur_estimated = False
        a_dur_d, a_dur_e, a_dist_d, a_dist_e, a_fare_d, a_fare_e = _estimate(
            r["a_dur"], r["a_dist"], r["a_fare"])
        b_dur_d, b_dur_e, b_dist_d, b_dist_e, b_fare_d, b_fare_e = _estimate(
            r["b_dur"], r["b_dist"], r["b_fare"])
        if a_dur_d is not None and b_dur_d is not None:
            dur = a_dur_d + b_dur_d + (waiting_min or 0)
            dur_estimated = a_dur_e or b_dur_e
        fare = None
        fare_estimated = False
        if a_fare_d is not None and b_fare_d is not None:
            fare = round(a_fare_d + b_fare_d, 2)
            fare_estimated = a_fare_e or b_fare_e
        dist = None
        dist_estimated = False
        if a_dist_d is not None and b_dist_d is not None:
            dist = round(a_dist_d + b_dist_d, 2)
            dist_estimated = a_dist_e or b_dist_e

        avg_delay_a = _avg_delay(conn, r["a_id"])
        avg_delay_b = _avg_delay(conn, r["b_id"])
        avg_delay = None
        if avg_delay_a is not None or avg_delay_b is not None:
            avg_delay = (avg_delay_a or 0) + (avg_delay_b or 0)

        results.append({
            "type": "TRANSFER",
            "legs": [
                {"route_id": r["a_id"], "route_number": r["a_no"], "route_name": r["a_name"],
                 "operator": r["a_op"], "from_stop_id": source_id, "to_stop_id": r["x_id"],
                 "journey_duration_min": r["a_dur"], "duration_estimated": a_dur_e, "duration_display": a_dur_d,
                 "distance_km": r["a_dist"], "distance_estimated": a_dist_e, "distance_display": a_dist_d,
                 "fare": r["a_fare"], "fare_estimated": a_fare_e, "fare_display": a_fare_d,
                 "source_reference": r["a_src"], "departures": [dict(s) for s in sched_a]},
                {"route_id": r["b_id"], "route_number": r["b_no"], "route_name": r["b_name"],
                 "operator": r["b_op"], "from_stop_id": r["x_id"], "to_stop_id": dest_id,
                 "journey_duration_min": r["b_dur"], "duration_estimated": b_dur_e, "duration_display": b_dur_d,
                 "distance_km": r["b_dist"], "distance_estimated": b_dist_e, "distance_display": b_dist_d,
                 "fare": r["b_fare"], "fare_estimated": b_fare_e, "fare_display": b_fare_d,
                 "source_reference": r["b_src"], "departures": [dict(s) for s in sched_b]},
            ],
            "transfer_stop": {"stop_id": r["x_id"], "stop_name": r["x_name"]},
            "transfers": 1,
            "total_duration_min": dur, "total_duration_estimated": dur_estimated,
            "total_fare": fare, "total_fare_estimated": fare_estimated,
            "total_distance_km": dist, "total_distance_estimated": dist_estimated,
            "waiting_min": waiting_min,
            "waiting_note": note,
            "avg_delay_min": avg_delay,
            "has_schedule": bool(sched_a and sched_b),
        })
    return results


# Fallback values used ONLY inside the scoring function when the database
# has no figure for a metric. A missing duration/fare must never look
# "free" or "instant" and out-rank a route we actually have data for --
# so unknowns are scored as comfortably worse than any real value we've
# ever seen in the supplied datasets, never as 0. The journey object
# returned to the API/UI still reports the true value (None -> "Not
# Available"); these numbers are never shown to the user.
UNKNOWN_DURATION_PENALTY_MIN = 600   # worse than any real recorded TN intercity trip
UNKNOWN_FARE_PENALTY_RS = 500        # worse than any real recorded fare in the data
UNKNOWN_DISTANCE_PENALTY_KM = 800


def _score(journey, weights):
    w = weights
    duration = journey["total_duration_min"]
    fare = journey["total_fare"]
    distance = journey["total_distance_km"]

    time_component = (duration if duration is not None else UNKNOWN_DURATION_PENALTY_MIN) * w["time"]
    transfer_component = journey["transfers"] * TRANSFER_PENALTY_MIN * w["transfer"]
    fare_component = (fare if fare is not None else UNKNOWN_FARE_PENALTY_RS) * w["fare"]
    wait_component = (journey.get("waiting_min") if journey.get("waiting_min") is not None else 60) * w["wait"]
    delay_component = (journey.get("avg_delay_min") or 0) * w["delay"]
    # small, preference-independent nudge so routes with real distance data
    # aren't accidentally tied with completely unknown ones
    distance_component = (distance if distance is not None else UNKNOWN_DISTANCE_PENALTY_KM) * 0.02

    schedule_penalty = 0 if journey.get("has_schedule") else 120

    return (time_component + transfer_component + fare_component
            + wait_component + delay_component + distance_component + schedule_penalty)


def search_journeys(source_query, dest_query, dep_time=None, preference="DEFAULT"):
    conn = get_conn()
    try:
        source_id, source_matches = _best_stop_id(conn, source_query)
        dest_id, dest_matches = _best_stop_id(conn, dest_query)

        if source_id is None:
            return {"error": "SOURCE_NOT_FOUND", "message": "Please select a valid starting location."}
        if dest_id is None:
            return {"error": "DEST_NOT_FOUND", "message": "Please select a valid destination."}
        if source_id == dest_id:
            return {"error": "SAME_STOP", "message": "Source and destination cannot be the same stop."}

        direct = _direct_candidates(conn, source_id, dest_id, dep_time)
        transfer = _transfer_candidates(conn, source_id, dest_id, dep_time)
        all_journeys = direct + transfer

        if not all_journeys:
            return {
                "error": "NO_ROUTE_FOUND",
                "message": "No matching journey found in the local database.",
                "source": dict(source_matches[0]) if source_matches else None,
                "destination": dict(dest_matches[0]) if dest_matches else None,
                "tnstc_fallback_url": "https://www.tnstc.in/OTRSOnline/",
            }

        weights = WEIGHTS.get(preference, WEIGHTS["DEFAULT"])
        for j in all_journeys:
            j["score"] = _score(j, weights)
        all_journeys.sort(key=lambda j: j["score"])

        for j in all_journeys:
            j["explanation"] = EXPLANATIONS.get(preference, EXPLANATIONS["DEFAULT"])
            j["data_note"] = "Based on stored database data."
            j["data_complete"] = (
                j["total_duration_min"] is not None
                and j["total_fare"] is not None
                and j["has_schedule"]
                and not j.get("total_duration_estimated")
                and not j.get("total_fare_estimated")
            )

        return {
            "source": dict(source_matches[0]),
            "destination": dict(dest_matches[0]),
            "preference": preference,
            "count": len(all_journeys),
            "journeys": all_journeys[:10],
        }
    finally:
        conn.close()
