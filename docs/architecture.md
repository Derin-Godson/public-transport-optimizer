# Architecture notes

## Request flow

```
User types "Nagercoil" / "Tirunelveli", picks "Fastest", hits Search
        │
        ▼
frontend/app.js  →  GET /api/optimize?from=Nagercoil&to=Tirunelveli&preference=FASTEST
        │
        ▼
backend/app.py (Flask route: optimize())
        │
        ▼
backend/optimizer.py: search_journeys()
   1. resolve "Nagercoil" / "Tirunelveli" to stop_id via fuzzy LIKE match
   2. _direct_candidates(): route_stop self-join for routes where
      source's stop_sequence < destination's stop_sequence
   3. _transfer_candidates(): route_stop x route_stop join through a
      shared intermediate stop, on two different routes
   4. attach schedule rows (next departures) per candidate leg
   5. score every candidate with the preference's weights
   6. sort ascending by score, attach a plain-English explanation
        │
        ▼
JSON response  →  frontend renders journey cards, ranks the top pick
```

## Why SQLite for the bundled runnable app

The project spec calls for MySQL, and `database/schema_mysql.sql` gives
a MySQL-compatible version of the exact same schema for that purpose.
The bundled, immediately-runnable app uses SQLite instead so that
`pip install -r requirements.txt && python3 import_all.py && python3
app.py` is genuinely all that's needed — no separate database server to
install, configure, or authenticate against. The schema, queries, and
application logic are otherwise unchanged; swapping the `sqlite3` calls
in `backend/db.py` for a MySQL connector is the only change needed to
point this at a real MySQL instance.

## Why the optimizer is a Python graph search, not raw recursive SQL

SQLite's `WITH RECURSIVE` could express the same one-transfer join, but
the multi-criteria scoring (different weight sets per user preference,
graceful handling of missing values, transfer-waiting-time calculation)
is clearer and easier to keep correct as ordinary Python over query
results than as one large recursive CTE. The underlying route/stop
traversal is still expressed as SQL joins (see `_direct_candidates` and
`_transfer_candidates` in `backend/optimizer.py`) — Python only handles
the scoring and formatting on top.
