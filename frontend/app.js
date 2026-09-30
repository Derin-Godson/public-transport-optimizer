const API = "http://127.0.0.1:5000/api";

// ------------------------------------------------------------------ nav
document.querySelectorAll(".nav-link").forEach(btn => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});
function showView(name) {
  document.querySelectorAll(".nav-link").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById(`view-${name}`).classList.add("active");
  if (name === "dashboard") loadDashboard();
  if (name === "about") loadAbout();
}

// ------------------------------------------------------------------ stop autocomplete
function wireAutocomplete(inputId, suggestId) {
  const input = document.getElementById(inputId);
  const box = document.getElementById(suggestId);
  let timer = null;

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { box.classList.remove("open"); return; }
    timer = setTimeout(async () => {
      try {
        const res = await fetch(`${API}/stops?q=${encodeURIComponent(q)}`);
        const stops = await res.json();
        renderSuggest(box, stops, input);
      } catch (e) { box.classList.remove("open"); }
    }, 180);
  });

  input.addEventListener("blur", () => setTimeout(() => box.classList.remove("open"), 150));
  input.addEventListener("focus", () => { if (box.children.length) box.classList.add("open"); });
}

function renderSuggest(box, stops, input) {
  if (!stops.length) { box.classList.remove("open"); box.innerHTML = ""; return; }
  box.innerHTML = stops.slice(0, 10).map(s =>
    `<div class="suggest-item" data-name="${escapeHtml(s.stop_name)}">${escapeHtml(s.stop_name)}<span class="city">${escapeHtml(s.city || "")}</span></div>`
  ).join("");
  box.classList.add("open");
  box.querySelectorAll(".suggest-item").forEach(el => {
    el.addEventListener("mousedown", () => {
      input.value = el.dataset.name;
      box.classList.remove("open");
    });
  });
}
wireAutocomplete("from-input", "from-suggest");
wireAutocomplete("to-input", "to-suggest");

document.getElementById("swap-btn").addEventListener("click", () => {
  const a = document.getElementById("from-input");
  const b = document.getElementById("to-input");
  [a.value, b.value] = [b.value, a.value];
});

// ------------------------------------------------------------------ preference chips
let currentPref = "DEFAULT";
document.querySelectorAll(".pref-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll(".pref-chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    currentPref = chip.dataset.pref;
  });
});

// ------------------------------------------------------------------ search
let lastJourneys = [];
let lastSourceLabel = "", lastDestLabel = "";

document.getElementById("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const from = document.getElementById("from-input").value.trim();
  const to = document.getElementById("to-input").value.trim();
  const time = document.getElementById("time-input").value;
  const results = document.getElementById("results");

  if (!from || !to) return;
  results.innerHTML = `<div class="state-msg">Searching the database…</div>`;

  const params = new URLSearchParams({ from, to, preference: currentPref });
  if (time) params.set("departure_time", time);

  let data;
  try {
    const res = await fetch(`${API}/optimize?${params.toString()}`);
    data = await res.json();
  } catch (err) {
    results.innerHTML = `<div class="state-msg error">
      Could not reach the backend API at <code>${API}</code>. Make sure
      <code>python backend/app.py</code> is running locally, then try again.
    </div>`;
    return;
  }

  renderResults(data, results, from, to);
});

function renderResults(data, container, from, to) {
  if (data.error === "SOURCE_NOT_FOUND") {
    container.innerHTML = `<div class="state-msg error">Please select a valid starting location.</div>`;
    return;
  }
  if (data.error === "DEST_NOT_FOUND") {
    container.innerHTML = `<div class="state-msg error">Please select a valid destination.</div>`;
    return;
  }
  if (data.error === "SAME_STOP") {
    container.innerHTML = `<div class="state-msg error">Source and destination cannot be the same stop.</div>`;
    return;
  }
  if (data.error === "NO_ROUTE_FOUND") {
    container.innerHTML = `
      <div class="state-msg">No matching route was found in the current database for
        <strong>${escapeHtml(from)} → ${escapeHtml(to)}</strong>.
        <div class="fallback-box">
          <strong>Check Current TNSTC Services</strong>
          <p style="margin:6px 0 0;color:var(--ink-soft);font-size:13.5px;">This local database is a historical/snapshot dataset, not a live feed. For up-to-date TNSTC services and reservations:</p>
          <a class="btn" href="${data.tnstc_fallback_url}" target="_blank" rel="noopener">Open Official TNSTC Search</a>
        </div>
      </div>`;
    return;
  }

  lastJourneys = data.journeys || [];
  lastJourneys.forEach((j, i) => j.__idx = i);
  lastSourceLabel = data.source.stop_name;
  lastDestLabel = data.destination.stop_name;

  const rows = lastJourneys.map((j, i) => journeyCardHtml(j, i === 0)).join("");
  container.innerHTML = `
    <div class="results-heading">
      <h3>${escapeHtml(lastSourceLabel)} → ${escapeHtml(lastDestLabel)}</h3>
      <span class="results-count">${data.count} journey${data.count === 1 ? "" : "s"} found · based on stored database data</span>
    </div>
    ${rows}
  `;
  container.querySelectorAll(".journey-card").forEach((card, i) => {
    card.dataset.idx = i;
    card.addEventListener("click", () => openDetail(i));
  });
}

function fmtMin(min, estimated) {
  if (min === null || min === undefined) return null;
  const h = Math.floor(min / 60), m = min % 60;
  const s = h > 0 ? `${h}h ${m}m` : `${m}m`;
  return estimated ? `≈ ${s}` : s;
}
function fmtFare(v, estimated) {
  if (v === null || v === undefined) return null;
  return estimated ? `≈ ₹${v}` : `₹${v}`;
}
function fmtKm(v, estimated) {
  if (v === null || v === undefined) return null;
  return estimated ? `≈ ${v} km` : `${v} km`;
}

function statHtml(label, value) {
  return `<div class="stat"><span class="k">${label}</span>
    <span class="v ${value === null ? "na" : ""}">${value === null ? "Not available" : escapeHtml(String(value))}</span></div>`;
}

function journeyCardHtml(j, isTop) {
  const legChips = j.legs.map(l => `<span class="leg-chip">${escapeHtml(l.route_number)}</span>`).join(`<span class="arrow">→</span>`);
  const badge = j.type === "DIRECT"
    ? `<span class="journey-type-badge">DIRECT</span>`
    : `<span class="journey-type-badge transfer">${j.transfers} TRANSFER${j.transfers > 1 ? "S" : ""}</span>`;
  return `
    <div class="journey-card ${isTop ? "top" : ""}" data-idx="${j.__idx ?? ""}">
      <div class="journey-top-row">
        <div style="display:flex;align-items:center;gap:10px;">${badge}${isTop ? '<span class="recommended-tag">★ Top pick for this search</span>' : ""}</div>
      </div>
      <div class="journey-route-line">${legChips}</div>
      <div class="journey-stats">
        ${statHtml("Journey", fmtMin(j.total_duration_min, j.total_duration_estimated))}
        ${statHtml("Fare", fmtFare(j.total_fare, j.total_fare_estimated))}
        ${statHtml("Distance", fmtKm(j.total_distance_km, j.total_distance_estimated))}
        ${statHtml("Transfers", j.transfers)}
      </div>
      <div class="journey-explain">${escapeHtml(j.explanation)}</div>
      ${j.total_fare_estimated ? `<div class="incomplete-note">Fare marked "≈" is estimated from the official per-km fare policy for a leg with no quoted fare — not that service's actual price.</div>` : ""}
      ${(j.total_duration_estimated || j.total_distance_estimated) ? `<div class="incomplete-note">Journey/distance marked "≈" assumes an average speed of 40 km/h to fill a missing figure — this speed is NOT from your source data, it's a generic planning assumption.</div>` : ""}
      ${!j.data_complete ? `<div class="incomplete-note">Some figures for this journey are not available in the database — shown as "Not available" rather than estimated.</div>` : ""}
    </div>`;
}

// assign stable indices once rendered (kept for journeyCardHtml's data-idx attr)

// ------------------------------------------------------------------ detail view
document.getElementById("detail-back").addEventListener("click", () => showView("search"));

function openDetail(idx) {
  const j = lastJourneys[idx];
  if (!j) return;
  const content = document.getElementById("detail-content");

  const nodes = [];
  nodes.push({ label: "Start", name: lastSourceLabel, time: j.legs[0].departures[0]?.departure_time || null, end: false });
  j.legs.forEach((leg, i) => {
    const isLast = i === j.legs.length - 1;
    const stopName = isLast ? lastDestLabel : (j.transfer_stop ? j.transfer_stop.stop_name : "");
    nodes.push({
      label: isLast ? "Destination" : "Transfer",
      name: stopName,
      time: leg.departures[0]?.arrival_time || null,
      routeNote: `via Route ${leg.route_number} (${leg.operator})`,
      end: isLast,
    });
  });

  const timelineHtml = nodes.map(n => `
    <div class="tl-node">
      <div class="tl-dot ${n.end ? "end" : ""}"></div>
      <div class="tl-label">${n.label}</div>
      <div class="tl-name">${escapeHtml(n.name)}</div>
      ${n.time ? `<div class="tl-time">${n.time}</div>` : ""}
      ${n.routeNote ? `<div class="tl-route-note">${escapeHtml(n.routeNote)}</div>` : ""}
    </div>`).join("");

  const legDetails = j.legs.map((leg, i) => `
    <div class="detail-grid">
      <div class="detail-cell"><div class="k">Route</div><div class="v">${escapeHtml(leg.route_number)}</div></div>
      <div class="detail-cell"><div class="k">Operator</div><div class="v" style="font-family:inherit;font-size:14px;">${escapeHtml(leg.operator)}</div></div>
      <div class="detail-cell"><div class="k">Duration</div><div class="v">${fmtMin(leg.duration_display, leg.duration_estimated) || "N/A"}</div></div>
      <div class="detail-cell"><div class="k">Distance</div><div class="v">${fmtKm(leg.distance_display, leg.distance_estimated) || "N/A"}</div></div>
      <div class="detail-cell"><div class="k">Fare</div><div class="v">${fmtFare(leg.fare_display, leg.fare_estimated) || "N/A"}</div></div>
      ${leg.via ? `<div class="detail-cell"><div class="k">Via</div><div class="v" style="font-family:inherit;font-size:14px;">${escapeHtml(leg.via)}</div></div>` : ""}
    </div>
    ${leg.departures && leg.departures.length ? `
      <div class="k" style="font-size:11.5px;color:var(--ink-soft);text-transform:uppercase;margin-top:6px;">Next departures</div>
      <div class="departures-list">${leg.departures.map(d => `<span class="departure-pill">${d.departure_time}</span>`).join("")}</div>
    ` : `<p class="incomplete-note">Schedule information is unavailable for this route segment.</p>`}
    <div class="source-note">Source: ${escapeHtml(leg.source_reference)}</div>
  `).join(`<hr style="border:none;border-top:1px solid var(--line);margin:20px 0;">`);

  const waitingNote = j.waiting_note ? `<p class="incomplete-note">${escapeHtml(j.waiting_note)}</p>`
    : (j.waiting_min !== null && j.waiting_min !== undefined ? `<p class="source-note">Estimated waiting time at transfer: ${j.waiting_min} min.</p>` : "");

  content.innerHTML = `
    <div class="detail-title">${escapeHtml(lastSourceLabel)} → ${escapeHtml(lastDestLabel)}</div>
    <div class="detail-sub">${j.explanation}</div>
    <div class="timeline">${timelineHtml}</div>
    <div class="detail-grid">
      ${statCell("Total journey", fmtMin(j.total_duration_min, j.total_duration_estimated))}
      ${statCell("Total fare", fmtFare(j.total_fare, j.total_fare_estimated))}
      ${statCell("Total distance", fmtKm(j.total_distance_km, j.total_distance_estimated))}
      ${statCell("Transfers", j.transfers)}
    </div>
    ${waitingNote}
    <h3 style="font-family:var(--serif);margin-top:28px;">Route segments</h3>
    ${legDetails}
  `;
  showView("detail");
}
function statCell(k, v) {
  return `<div class="detail-cell"><div class="k">${k}</div><div class="v">${v === null ? "Not available" : escapeHtml(String(v))}</div></div>`;
}

// ------------------------------------------------------------------ dashboard
let dashboardLoaded = false;
async function loadDashboard() {
  if (dashboardLoaded) return;
  const el = document.getElementById("dashboard-content");
  el.innerHTML = `<div class="state-msg">Loading…</div>`;
  try {
    const res = await fetch(`${API}/dashboard`);
    const d = await res.json();
    dashboardLoaded = true;
    renderDashboard(d, el);
  } catch (e) {
    el.innerHTML = `<div class="state-msg error">Could not reach the backend API. Make sure the Flask server is running.</div>`;
  }
}

function renderDashboard(d, el) {
  const s = d.stats;
  const tiles = [
    ["Operators", s.total_operators], ["Routes", s.total_routes], ["Stops", s.total_stops],
    ["Schedules", s.total_schedules], ["Route-stop links", s.total_route_stops],
    ["Defined transfers", s.total_transfers_defined], ["Delay records", s.total_delay_records],
  ];

  const maxRoutes = Math.max(...d.routes_by_operator.map(r => r.n), 1);
  const routesByOpBars = d.routes_by_operator.map(r => barRow(r.operator_name, r.n, r.n, maxRoutes)).join("");

  const maxFare = Math.max(...d.avg_fare_by_operator.map(r => r.avg_fare || 0), 1);
  const fareBars = d.avg_fare_by_operator.map(r => barRow(r.operator_name, `₹${r.avg_fare}`, r.avg_fare, maxFare)).join("");

  const maxTime = Math.max(...d.avg_journey_time_by_operator.map(r => r.avg_min || 0), 1);
  const timeBars = d.avg_journey_time_by_operator.map(r => barRow(r.operator_name, `${r.avg_min}m`, r.avg_min, maxTime)).join("");

  const maxConn = Math.max(...d.most_connected_stops.map(r => r.n), 1);
  const connBars = d.most_connected_stops.slice(0, 8).map(r => barRow(r.stop_name, r.n, r.n, maxConn)).join("");

  const maxDist = Math.max(...d.routes_by_district.map(r => r.n), 1);
  const distBars = d.routes_by_district.map(r => barRow(r.district, r.n, r.n, maxDist)).join("");

  el.innerHTML = `
    <div class="stat-grid">${tiles.map(([l, n]) => `<div class="stat-tile"><div class="num">${n}</div><div class="lbl">${l}</div></div>`).join("")}</div>
    <div class="dash-panels">
      <div class="panel"><h4>Routes by operator</h4>${routesByOpBars}</div>
      <div class="panel"><h4>Average fare by operator</h4>${fareBars}</div>
      <div class="panel"><h4>Average journey time by operator</h4>${timeBars}</div>
      <div class="panel"><h4>Most-connected stops</h4>${connBars}</div>
      <div class="panel"><h4>Routes by origin district</h4>${distBars}</div>
    </div>
  `;
}
function barRow(label, display, value, max) {
  const pct = Math.max(4, Math.round((value / max) * 100));
  return `<div class="bar-row">
    <span class="bar-label">${escapeHtml(String(label))}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
    <span class="bar-val">${escapeHtml(String(display))}</span>
  </div>`;
}

// ------------------------------------------------------------------ about
let aboutLoaded = false;
async function loadAbout() {
  if (aboutLoaded) return;
  const el = document.getElementById("about-content");
  el.innerHTML = `<div class="state-msg">Loading…</div>`;
  try {
    const res = await fetch(`${API}/dashboard`);
    const d = await res.json();
    aboutLoaded = true;
    el.innerHTML = d.data_sources.map(s => `
      <div class="source-card">
        <h4>${escapeHtml(s.source_name)}</h4>
        <div class="meta">${escapeHtml(s.source_type)} · data period: ${escapeHtml(s.data_date || "Not stated")} · imported ${escapeHtml(s.date_accessed)}</div>
        <p>${escapeHtml(s.description)}</p>
      </div>
    `).join("") + `
      <div class="source-card">
        <h4>What this system deliberately does NOT do</h4>
        <p>It does not show live bus positions, live seat availability, current traffic, or real-time
        cancellations — none of that exists in the supplied datasets. It does not invent bus numbers,
        timings, fares or distances that aren't in the data; missing values are always labelled
        "Not available" rather than estimated. For live TNSTC search and reservations, see the
        <a href="https://www.tnstc.in/OTRSOnline/" target="_blank" rel="noopener">official TNSTC site</a>.</p>
      </div>`;
  } catch (e) {
    el.innerHTML = `<div class="state-msg error">Could not reach the backend API. Make sure the Flask server is running.</div>`;
  }
}

// ------------------------------------------------------------------ util
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
