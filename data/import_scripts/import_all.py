"""
Import pipeline for the Public Transport Journey Optimizer.

Sources imported (ALL real, none fabricated):
  1. data/raw/tnstc_nagercoil_routes.csv   -- 37 routes, TNSTC Nagercoil Region
                                               (transcribed from 2018040592.pdf)
  2. data/processed/tnstc_schedules.csv    -- departure times for the routes
                                               above (transcribed from 2018040581.pdf)
  3. data/raw/tnstc_fare_policy.csv        -- official fare-per-km slab
                                               (transcribed from the fare-table screenshot)
  4. data/raw/SETCbustimings_1_0.csv       -- 549 statewide SETC routes
  5. data/raw/stops.txt, routes.txt        -- MTC (Chennai) GTFS-style reference
                                               data (stop_times.txt is present but
                                               UNUSABLE for route linkage: no
                                               trips.txt was supplied, so trip_id
                                               cannot be mapped to a route_id --
                                               this is called out explicitly rather
                                               than guessed at; see README).

Produces backend/db/transport.db and prints an import report:
  records read / inserted / skipped / needing review, per source.
"""
import csv
import os
import re
import sqlite3
from collections import defaultdict

BASE = os.path.join(os.path.dirname(__file__), "..", "..")
RAW = os.path.join(BASE, "data", "raw")
PROC = os.path.join(BASE, "data", "processed")
DB_DIR = os.path.join(BASE, "backend", "db")
DB_PATH = os.path.join(DB_DIR, "transport.db")
SCHEMA = os.path.join(BASE, "database", "schema.sql")

os.makedirs(DB_DIR, exist_ok=True)
report = defaultdict(lambda: {"read": 0, "inserted": 0, "skipped": 0, "review": 0})


def fresh_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(open(SCHEMA).read())
    conn.commit()
    return conn


def get_or_create_stop(conn, name, city=None, district=None, lat=None, lon=None, stop_type="Town"):
    name = name.strip().title()
    key_city = (city or name).strip().title()
    cur = conn.execute("SELECT stop_id FROM stop WHERE stop_name=? AND city=?", (name, key_city))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO stop (stop_name, district, city, latitude, longitude, stop_type) "
        "VALUES (?,?,?,?,?,?)",
        (name, district, key_city, lat, lon, stop_type),
    )
    return cur.lastrowid


def get_or_create_operator(conn, name, otype, region, source):
    cur = conn.execute("SELECT operator_id FROM transport_operator WHERE operator_name=?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO transport_operator (operator_name, operator_type, region, source_reference) "
        "VALUES (?,?,?,?)",
        (name, otype, region, source),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
def import_tnstc_nagercoil(conn):
    src = "tnstc_nagercoil_routes.csv"
    op_id = get_or_create_operator(
        conn, "TNSTC Tirunelveli (Nagercoil Region)", "State Transport Corporation",
        "Tirunelveli / Kanyakumari", "2018040592.pdf"
    )
    route_ids_by_no = {}
    with open(os.path.join(RAW, "tnstc_nagercoil_routes.csv")) as f:
        for row in csv.DictReader(f):
            report[src]["read"] += 1
            try:
                src_id = get_or_create_stop(conn, row["source"], district="Kanyakumari/Tirunelveli")
                dst_id = get_or_create_stop(conn, row["destination"], district="Tamil Nadu")
                cur = conn.execute(
                    "INSERT INTO route (operator_id, route_number, route_name, source_stop_id, "
                    "destination_stop_id, via, service_type, journey_duration_min, total_distance_km, "
                    "base_fare, total_services, status, source_reference) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (op_id, row["route_no"],
                     f"{row['source'].title()} to {row['destination'].title()}",
                     src_id, dst_id, row["via"] or None, "Mofussil",
                     int(row["journey_time_min"]) if row["journey_time_min"] else None,
                     None, float(row["fare"]) if row["fare"] else None,
                     int(row["total_services"]) if row["total_services"] else None,
                     "ACTIVE", "TNSTC Nagercoil Region route table (2018040592.pdf)"),
                )
                route_id = cur.lastrowid
                route_ids_by_no[row["route_no"]] = (route_id, src_id, dst_id, row["via"])

                # route_stop: origin(1) -> via(2, if any) -> destination(3 or 2)
                conn.execute(
                    "INSERT INTO route_stop (route_id, stop_id, stop_sequence, boarding_allowed, dropoff_allowed) "
                    "VALUES (?,?,1,1,1)", (route_id, src_id)
                )
                seq = 2
                if row["via"]:
                    # via may list multiple comma-separated intermediate towns
                    for via_stop in row["via"].split(","):
                        via_stop = via_stop.strip()
                        if not via_stop:
                            continue
                        via_id = get_or_create_stop(conn, via_stop, district="Tamil Nadu")
                        conn.execute(
                            "INSERT OR IGNORE INTO route_stop (route_id, stop_id, stop_sequence, boarding_allowed, dropoff_allowed) "
                            "VALUES (?,?,?,1,1)", (route_id, via_id, seq)
                        )
                        seq += 1
                conn.execute(
                    "INSERT INTO route_stop (route_id, stop_id, stop_sequence, boarding_allowed, dropoff_allowed) "
                    "VALUES (?,?,?,1,1)", (route_id, dst_id, seq)
                )

                # direct fare row, using the exact fare from the route table
                if row["fare"]:
                    conn.execute(
                        "INSERT INTO fare (route_id, from_stop_id, to_stop_id, fare_amount, fare_type, "
                        "effective_date, source_reference) VALUES (?,?,?,?,?,?,?)",
                        (route_id, src_id, dst_id, float(row["fare"]), "Full Route Fare", "29.01.2018",
                         "2018040592.pdf"),
                    )
                report[src]["inserted"] += 1
            except Exception as e:
                report[src]["skipped"] += 1
                print(f"  [skip] {src}: {row.get('route_no')}: {e}")
    conn.commit()
    return route_ids_by_no


def import_tnstc_schedules(conn, route_ids_by_no):
    src = "tnstc_schedules.csv"
    path = os.path.join(PROC, "tnstc_schedules.csv")
    with open(path) as f:
        for row in csv.DictReader(f):
            report[src]["read"] += 1
            route_no = row["route_no"]
            if route_no == "N/A":
                # timing-sheet-only corridor with no official route number:
                # create a minimal route record for it (source, dest, via all
                # taken verbatim from the PDF section header)
                if route_no not in route_ids_by_no:
                    op_id = get_or_create_operator(
                        conn, "TNSTC Tirunelveli (Nagercoil Region)", "State Transport Corporation",
                        "Tirunelveli / Kanyakumari", "2018040581.pdf"
                    )
                    src_id = get_or_create_stop(conn, row["source"])
                    dst_id = get_or_create_stop(conn, row["destination"])
                    cur = conn.execute(
                        "INSERT INTO route (operator_id, route_number, route_name, source_stop_id, "
                        "destination_stop_id, via, service_type, source_reference) VALUES (?,?,?,?,?,?,?,?)",
                        (op_id, "N/A", f"{row['source'].title()} to {row['destination'].title()} (via {row['via'].title()})",
                         src_id, dst_id, row["via"], "Mofussil",
                         "Route number not stated in source (2018040581.pdf timing sheet); "
                         "created from section header only"),
                    )
                    route_id = cur.lastrowid
                    via_id = get_or_create_stop(conn, row["via"])
                    conn.execute("INSERT INTO route_stop (route_id, stop_id, stop_sequence) VALUES (?,?,1)", (route_id, src_id))
                    conn.execute("INSERT INTO route_stop (route_id, stop_id, stop_sequence) VALUES (?,?,2)", (route_id, via_id))
                    conn.execute("INSERT INTO route_stop (route_id, stop_id, stop_sequence) VALUES (?,?,3)", (route_id, dst_id))
                    route_ids_by_no[route_no] = (route_id, src_id, dst_id, row["via"])
                route_id, src_id, dst_id, _via = route_ids_by_no[route_no]
            else:
                if route_no not in route_ids_by_no:
                    report[src]["review"] += 1
                    continue
                route_id, src_id, dst_id, _via = route_ids_by_no[route_no]

            conn.execute(
                "INSERT INTO schedule (route_id, departure_stop_id, arrival_stop_id, departure_time, "
                "arrival_time, operating_day, schedule_source) VALUES (?,?,?,?,?,?,?)",
                (route_id, src_id, dst_id, row["departure_time"], None, "DAILY",
                 "2018040581.pdf" + (f" | NOTE: {row['match_note']}" if row["match_note"] else "")),
            )
            report[src]["inserted"] += 1
    conn.commit()


def import_fare_policy(conn):
    src = "tnstc_fare_policy.csv"
    with open(os.path.join(RAW, "tnstc_fare_policy.csv")) as f:
        for row in csv.DictReader(f):
            report[src]["read"] += 1
            conn.execute(
                "INSERT INTO fare_policy_slab (service_type, category, min_km, fare_per_km_paise, "
                "min_fare_rs, min_fare_km, effective_from, source_reference) VALUES (?,?,?,?,?,?,?,?)",
                (row["service_type"], row["category"],
                 int(row["min_km"]) if row["min_km"] else None,
                 float(row["fare_per_km_paise"]) if row["fare_per_km_paise"] else None,
                 float(row["min_fare_rs"]) if row["min_fare_rs"] else None,
                 int(row["min_fare_km"]) if row["min_fare_km"] else None,
                 row["effective_from"], "TNSTC Tirunelveli fare table screenshot"),
            )
            report[src]["inserted"] += 1
    conn.commit()


# ---------------------------------------------------------------------------
def parse_time_list(raw):
    """SETC 'Departure Timings' cell: comma separated H.MM / HH.MM values,
    sometimes with stray spaces. Returns list of 'HH:MM' strings; anything
    that doesn't parse as a plausible time is skipped (not guessed)."""
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        m = re.match(r"^(\d{1,2})\.(\d{1,2})$", tok)
        if not m:
            continue
        h, mnt = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mnt <= 59:
            out.append(f"{h:02d}:{mnt:02d}")
    return out


def import_setc(conn):
    src = "SETCbustimings_1_0.csv"
    op_id = get_or_create_operator(
        conn, "SETC (State Express Transport Corporation)", "State Transport Corporation",
        "Tamil Nadu (statewide)", "SETCbustimings_1_0.csv"
    )
    with open(os.path.join(RAW, "SETCbustimings_1_0.csv")) as f:
        for row in csv.DictReader(f):
            report[src]["read"] += 1
            try:
                source_town = row["From"].strip()
                dest_town = row["To"].strip()
                # 'To' sometimes carries a parenthetical via note, e.g. "CHENNAI (Via Chittoor)"
                via = None
                m = re.search(r"\(Via[^)]*\)", dest_town, re.IGNORECASE)
                if m:
                    via = m.group(0).strip("()").replace("Via", "").strip()
                    dest_town = dest_town[:m.start()].strip()
                if not source_town or not dest_town or source_town.lower() == dest_town.lower():
                    report[src]["skipped"] += 1
                    continue
                src_id = get_or_create_stop(conn, source_town)
                dst_id = get_or_create_stop(conn, dest_town)
                depot = row.get("Depot", "").strip() or None
                length_km = None
                try:
                    length_km = float(row["Route Length"]) if row["Route Length"] else None
                except ValueError:
                    pass
                total_services = None
                try:
                    total_services = int(row["No.of Service"]) if row["No.of Service"] else None
                except ValueError:
                    pass
                cur = conn.execute(
                    "INSERT INTO route (operator_id, route_number, route_name, source_stop_id, "
                    "destination_stop_id, via, service_type, total_distance_km, total_services, "
                    "status, source_reference) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (op_id, row["Route No."], f"{source_town.title()} to {dest_town.title()}",
                     src_id, dst_id, via, row.get("Type"), length_km, total_services,
                     "ACTIVE", f"SETC bus timings CSV (Depot: {depot})" if depot else "SETC bus timings CSV"),
                )
                route_id = cur.lastrowid
                conn.execute("INSERT INTO route_stop (route_id, stop_id, stop_sequence) VALUES (?,?,1)", (route_id, src_id))
                conn.execute("INSERT INTO route_stop (route_id, stop_id, stop_sequence) VALUES (?,?,2)", (route_id, dst_id))

                times = parse_time_list(row.get("Departure Timings", ""))
                for t in times:
                    conn.execute(
                        "INSERT INTO schedule (route_id, departure_stop_id, arrival_stop_id, departure_time, "
                        "schedule_source) VALUES (?,?,?,?,?)",
                        (route_id, src_id, dst_id, t, "SETCbustimings_1_0.csv"),
                    )
                report[src]["inserted"] += 1
            except Exception as e:
                report[src]["skipped"] += 1
                print(f"  [skip] {src}: row {report[src]['read']}: {e}")
    conn.commit()


# ---------------------------------------------------------------------------
def import_mtc_reference(conn):
    """MTC (Chennai) GTFS-style stops + routes are imported as reference
    data (they carry real coordinates and real route names). stop_times.txt
    is NOT imported into SCHEDULE/ROUTE_STOP: it has no accompanying
    trips.txt, so a stop_times row's trip_id cannot be linked back to a
    route_id -- doing so would require guessing, which is against the
    project rule. This limitation is recorded in DATA_SOURCE."""
    op_id = get_or_create_operator(
        conn, "MTC (Chennai city bus network)", "City Transport",
        "Chennai", "stops.txt / routes.txt (GTFS-style extract)"
    )
    src = "stops.txt"
    with open(os.path.join(RAW, "stops.txt")) as f:
        for row in csv.DictReader(f):
            report[src]["read"] += 1
            try:
                lat = float(row["stop_lat"]) if row["stop_lat"] else None
                lon = float(row["stop_lon"]) if row["stop_lon"] else None
                get_or_create_stop(conn, row["stop_name"], city="Chennai", lat=lat, lon=lon, stop_type="GTFS Stop")
                report[src]["inserted"] += 1
            except Exception:
                report[src]["skipped"] += 1
    conn.commit()

    src = "routes.txt"
    n_trip_ids = 0
    with open(os.path.join(RAW, "routes.txt")) as f:
        for row in csv.DictReader(f):
            report[src]["read"] += 1
            report[src]["review"] += 1  # not linked to stop-level schedule; needs trips.txt
    report[src]["inserted"] = 0  # not materialized as ROUTE rows (see docstring) -- reference only

    # count distinct trips in stop_times.txt for the data-source description only
    seen = set()
    with open(os.path.join(RAW, "stop_times.txt")) as f:
        next(f)
        for i, line in enumerate(f):
            seen.add(line.split(",", 1)[0])
            if i > 2_000_000:
                break
    n_trip_ids = len(seen)

    conn.execute(
        "INSERT INTO data_source (source_name, source_url, source_type, date_accessed, data_date, description) "
        "VALUES (?,?,?,?,?,?)",
        ("MTC Chennai GTFS-style extract (stops.txt, routes.txt, stop_times.txt)", None, "GTFS",
         "2026-09-16", "Not stated in source",
         f"stops.txt: {report['stops.txt']['inserted']} stops imported with real lat/lon. "
         f"routes.txt: {report['routes.txt']['read']} route names available as reference only. "
         f"stop_times.txt: {n_trip_ids} distinct trip_ids / "
         "1,360,635 stop-time rows present but NOT imported into ROUTE_STOP/SCHEDULE "
         "because the dataset does not include trips.txt, so a trip_id cannot be "
         "linked to a route_id without guessing. Route-level MTC journeys are therefore "
         "not searchable by the optimizer; only the operator/stop/route reference lists "
         "are populated."),
    )
    conn.commit()


# ---------------------------------------------------------------------------
def data_source_rows(conn):
    rows = [
        ("TNSTC Nagercoil Region - Route & Fare Table", None, "PDF", "2026-09-16", "2018",
         "37 mofussil routes with total services, journey time and fare, sourced from official TNSTC document 2018040592.pdf."),
        ("TNSTC Nagercoil Region - Departure Timing Sheet", None, "PDF", "2026-09-16", "2018",
         "Route-wise departure times, sourced from 2018040581.pdf. Only cleanly OCR-extractable "
         "sections were imported; garbled/truncated sections (mostly return legs) were left out "
         "rather than guessed. See data/import_scripts/build_tnstc_schedules.py for exact provenance per route."),
        ("TNSTC Tirunelveli Fare Policy Slab", None, "Image/Screenshot", "2026-09-16", "2018 (effective 29.01.2018)",
         "Official per-km fare table (Rural & City/Urban, by service type), transcribed from the "
         "uploaded fare-table screenshot."),
        ("SETC Statewide Bus Timings", None, "CSV", "2026-09-16", "Not stated in source",
         "549 SETC (State Express) routes across Tamil Nadu with depot, route length, service type, "
         "service count and departure timings."),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO data_source (source_name, source_url, source_type, date_accessed, data_date, description) "
            "VALUES (?,?,?,?,?,?)", r
        )
    conn.commit()


def print_report():
    print("\n===== IMPORT REPORT =====")
    for src, stats in report.items():
        print(f"{src:35s} read={stats['read']:<7} inserted={stats['inserted']:<7} "
              f"skipped={stats['skipped']:<6} needs_review={stats['review']}")
    print("==========================\n")


if __name__ == "__main__":
    conn = fresh_db()
    print("Importing TNSTC Nagercoil route table...")
    route_ids_by_no = import_tnstc_nagercoil(conn)
    print("Importing TNSTC Nagercoil departure schedules...")
    import_tnstc_schedules(conn, route_ids_by_no)
    print("Importing TNSTC fare policy slab...")
    import_fare_policy(conn)
    print("Importing SETC statewide routes...")
    import_setc(conn)
    print("Importing MTC (Chennai) reference data...")
    import_mtc_reference(conn)
    data_source_rows(conn)
    print_report()

    # quick sanity counts
    for tbl in ["transport_operator", "stop", "route", "route_stop", "schedule", "fare", "fare_policy_slab", "data_source"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"{tbl:20s} {n} rows")
    conn.close()
    print(f"\nDatabase built at {DB_PATH}")
