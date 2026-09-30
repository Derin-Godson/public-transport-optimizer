"""
Transcribes ONLY the cleanly OCR'd departure-time lists from
2018040581.pdf (TNSTC Nagercoil Region timing sheet) into a flat CSV.

Sections where the PDF text extraction was garbled/truncated (mostly the
*return* legs, e.g. Tirunelveli -> Nagercoil, Madurai -> Nagercoil) are
DELIBERATELY OMITTED rather than guessed at, per the no-fabrication rule.
Where a section's departure count does not match the "Total Services"
figure in the official route table (2018040592.pdf), that is noted in
`match_note` and surfaced to the user in the UI/README — the times
themselves are still the literal source text, just possibly a partial
capture of the full published timetable.

Output: data/processed/tnstc_schedules.csv
Columns: route_no,source,destination,via,departure_time,match_note
"""
import csv
import os

RAW = os.path.join(os.path.dirname(__file__), "..", "raw")
OUT = os.path.join(os.path.dirname(__file__), "..", "processed")
os.makedirs(OUT, exist_ok=True)

def hhmm(t):
    h, m = t.strip().split(".")
    return f"{int(h):02d}:{int(m):02d}"

rows = []

def add(route_no, source, dest, via, times_csv, note=""):
    for t in times_csv.split(","):
        t = t.strip()
        if not t:
            continue
        rows.append([route_no, source, dest, via, hhmm(t), note])

# Exact match to official "Total Services" figure in the route table
add("505", "NAGERCOIL", "MADURAI", "",
    "05.15,06.10,08.25,08.50,09.10,10.30,10.40,11.30,12.30,21.50,23.00")
add("622", "NAGERCOIL", "KUMULI", "RAJAPALAYAM, THENI",
    "05.00,08.20,11.30,16.30,18.00,19.45,23.00")
add("505/TPR", "NAGERCOIL", "TIRUPPUR", "",
    "00.40,06.00,08.40,14.30,19.00")
add("505-DIN", "NAGERCOIL", "DINDIGUL", "",
    "07.30,13.45,15.10,19.50,20.55")
add("505-TRI", "NAGERCOIL", "TRICHIRAPPALLI", "MADURAI",
    "07.20,10.50,19.30")
add("505/PERI", "NAGERCOIL", "PERIAKULAM", "MADURAI", "04.00")
add("505-KOD", "NAGERCOIL", "KODAIKANAL", "MADURAI", "06.20,21.40")
add("505-PAL", "NAGERCOIL", "PALANI", "MADURAI", "07.10,10.00")
add("505-TAN", "NAGERCOIL", "THANJAVUR", "", "09.00,20.30")
add("505K", "MONDAYNAGAR", "KARAIKUDI", "MADURAI", "14.45")
add("505/RAME", "KANYAKUMARI", "RAMESWARAM", "MADURAI", "19.40")
add("579/RAME", "KANYAKUMARI", "RAMESWARAM", "THOOTHUKUDI", "05.00,08.30")
add("595", "NAGERCOIL", "THOOTHUKUDI", "VALLIYOOR",
    "04.30,07.30,08.00,10.00,12.00,12.30,13.00,13.30,14.00,17.20,19.00")
add("565-EE", "NAGERCOIL", "TIRUNELVELI", "",
    "04.00,04.18,04.36,04.45,04.54,05.03,05.12,05.21,05.30,05.39,05.45,"
    "05.57,06.06,06.15,06.25,06.33,06.42,06.51,07.00,07.09,07.18,07.27,"
    "07.36,07.45,07.54,08.03,08.12,08.21,08.30,08.39,08.48,08.57,09.06,"
    "09.15,09.24,09.33,09.42,09.51,10.00,10.18,10.27,10.36,10.45,10.54,"
    "11.03,11.12,11.21,11.30,11.39,11.48,11.57,12.06,12.15,12.24,12.33,"
    "12.42,12.51,13.00,13.09,13.18,13.27,13.36,13.45,13.54,14.03,14.12,"
    "14.21,14.30,14.39,14.48,14.57,15.06,15.15,15.24,15.33,15.42,15.51,"
    "16.00,16.09,16.18,16.27,16.36,16.45,16.54,17.03,17.12,17.21,17.30,"
    "17.39,17.48,17.57,18.06,18.15,18.24,18.33,18.42,18.51,19.09,19.29")

# Partial / count differs from the official route table -> flagged
add("505-EXP", "NAGERCOIL", "MADURAI", "TIRUNELVELI",
    "03.30,05.45,08.00,09.25,15.00,16.30,20.40,21.05",
    "Timing-sheet lists 8 departures for this corridor; official route "
    "table records Total Services = 4 for 505-EXP. Kept as-is (source "
    "text), discrepancy not resolved by guessing.")
add("505-CBE", "NAGERCOIL", "COIMBATORE", "",
    "18.10,19.20,20.20",
    "Timing sheet shows only 3 of the 6 daily services listed in the "
    "official route table (2018040592.pdf); remainder not legible in "
    "source scan.")
add("505-SLM", "KALIAKKAVILAI", "SALEM", "MADURAI", "17.00",
    "Only outbound leg legible in source scan; route table records 2 "
    "total services (both directions).")
add("505-VKI", "KALIAKKAVILAI", "VELANKANNI", "THOOTHUKUDI", "13.45",
    "Only outbound leg legible in source scan; route table records 2 "
    "total services (both directions).")
add("579", "NAGERCOIL", "THOOTHUKUDI", "TIRUNELVELI",
    "02.15,04.55,06.35,07.45,11.05,13.30,14.30,16.05,18.05",
    "Timing sheet lists 9 departures; official route table records "
    "Total Services = 4 for plain route 579 (via Tirunelveli). Kept as "
    "the literal source text; discrepancy not resolved by guessing.")
add("450", "KANYAKUMARI", "NEDUMANKADU", "", "05.45,13.45",
    "Timing sheet lists 2 departures; official route table records "
    "Total Services = 1.")
add("N/A", "NAGERCOIL", "THOOTHUKUDI", "OVARI",
    "04.15,05.30,06.00,08.00,09.00,10.00,11.00,12.45,13.45,15.00,15.30,"
    "17.15,18.00,19.15",
    "No route number given for this corridor in either source PDF; "
    "route created from the timing-sheet section header only.")

with open(os.path.join(OUT, "tnstc_schedules.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_no", "source", "destination", "via", "departure_time", "match_note"])
    w.writerows(rows)

print(f"Wrote {len(rows)} schedule rows -> data/processed/tnstc_schedules.csv")
