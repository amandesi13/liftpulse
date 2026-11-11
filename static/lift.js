// Per-lift page: health over time, features against their baseline band, ride traces.
"use strict";

const { el, pill, api, when } = LP;
const LIFT_ID = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
const COLORS = { up: "rgba(15,118,110,0.45)", down: "rgba(99,102,241,0.45)", line: "#0f172a",
  band: "rgba(21,128,61,0.12)", bandEdge: "rgba(21,128,61,0.35)" };
const charts = {};

Chart.defaults.font.size = 13;
Chart.defaults.color = "#334155";

// Shaded rectangles behind a chart: [{axis: "x"|"y", from, to, color, label}]
const bandsPlugin = {
  id: "bands",
  beforeDatasetsDraw(chart, _args, opts) {
    const { ctx, chartArea: a, scales } = chart;
    for (const b of opts.items || []) {
      const s = scales[b.axis];
      if (!s) continue;
      const p1 = s.getPixelForValue(b.from), p2 = s.getPixelForValue(b.to);
      ctx.save();
      ctx.fillStyle = b.color;
      if (b.axis === "x") ctx.fillRect(Math.min(p1, p2), a.top, Math.abs(p2 - p1), a.bottom - a.top);
      else ctx.fillRect(a.left, Math.min(p1, p2), a.right - a.left, Math.abs(p2 - p1));
      if (b.label) {
        ctx.fillStyle = "#64748b"; ctx.font = "12px system-ui";
        ctx.fillText(b.label, b.axis === "x" ? Math.min(p1, p2) + 4 : a.left + 6,
          b.axis === "x" ? a.top + 14 : Math.min(p1, p2) + 14);
      }
      ctx.restore();
    }
  },
};
Chart.register(bandsPlugin);

const dateTicks = { callback: (v) => new Date(v).toLocaleDateString(undefined, { day: "numeric", month: "short" }) };

// x-axis limited to the data, so the axis doesn't run past today
function timeAxis(xs) {
  const axis = { type: "linear", ticks: dateTicks };
  if (xs.length) { axis.min = Math.min(...xs); axis.max = Math.max(...xs); }
  return axis;
}

function makeChart(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

function healthChart(history) {
  const pts = history.filter((h) => h.score != null).map((h) => ({ x: Date.parse(h.date + "T12:00:00Z"), y: h.score }));
  makeChart("health-chart", {
    type: "line",
    data: { datasets: [{ data: pts, borderColor: COLORS.line, borderWidth: 3, pointRadius: 0, tension: 0.25 }] },
    options: {
      maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false },
        bands: { items: [
          { axis: "y", from: 80, to: 100, color: "rgba(21,128,61,0.10)", label: "healthy" },
          { axis: "y", from: 60, to: 80, color: "rgba(183,121,31,0.10)", label: "watch" },
          { axis: "y", from: 0, to: 60, color: "rgba(197,48,48,0.08)", label: "needs attention" }] } },
      scales: { x: timeAxis(pts.map((p) => p.x)), y: { min: 0, max: 100 } },
    },
  });
}

function featureCharts(features) {
  const grid = document.getElementById("features");
  grid.replaceChildren();
  for (const [key, f] of Object.entries(features)) {
    const id = `f-${key}`;
    const card = el("div", { class: "card" },
      el("h3", { text: `${f.label[0].toUpperCase()}${f.label.slice(1)} (${f.unit})` }),
      el("div", { class: "chart-box" }, el("canvas", { id })));
    grid.append(card);
    const band = f.baseline_band;
    const datasets = ["up", "down"].map((dir) => ({
      type: "scatter", label: `${dir} rides`,
      data: f.points.filter((p) => p.dir === dir).map((p) => ({ x: Date.parse(p.t), y: p.v })),
      pointRadius: 2, backgroundColor: COLORS[dir],
    }));
    datasets.push({ type: "line", label: "daily median", borderColor: COLORS.line, borderWidth: 2.5,
      pointRadius: 0, data: f.daily_median.map((d) => ({ x: Date.parse(d.date + "T12:00:00Z"), y: d.v })) });
    makeChart(id, {
      data: { datasets },
      options: {
        maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { boxWidth: 12 } },
          bands: { items: band ? [{ axis: "y", from: band[0], to: band[1], color: COLORS.band }] : [] } },
        scales: { x: timeAxis(datasets.flatMap((d) => d.data.map((p) => p.x))), y: { beginAtZero: true } },
      },
    });
  }
}

async function showRide(rideId) {
  if (!rideId) {
    document.getElementById("trace-meta").textContent = "No valid rides yet.";
    return;
  }
  const r = await api(`/api/rides/${rideId}`);
  document.getElementById("trace-title").textContent = `Ride ${r.id}`;
  const parts = [when(r.started_at)];
  if (r.valid) parts.push(`${r.direction} ${r.floors} floors`, `${Math.round(r.duration_s)} s`,
    r.ride_score == null ? "not scored yet" : `score ${Math.round(r.ride_score)}`);
  else parts.push(`rejected: ${r.reject_reason}`);
  document.getElementById("trace-meta").textContent = parts.join(" · ");
  if (!r.trace) { charts["trace-chart"]?.destroy(); return; }
  const t = r.trace;
  const xy = (ys) => ys.map((y, i) => ({ x: t.t[i], y }));
  const items = [];
  if (t.phases) {
    items.push({ axis: "x", from: t.phases.start_begin, to: t.phases.start_end, color: "rgba(15,118,110,0.12)", label: "start" });
    items.push({ axis: "x", from: t.phases.start_end, to: t.phases.stop_begin, color: "rgba(148,163,184,0.12)", label: "cruise" });
    items.push({ axis: "x", from: t.phases.stop_begin, to: t.phases.stop_end, color: "rgba(197,48,48,0.10)", label: "stop" });
  }
  makeChart("trace-chart", {
    type: "line",
    data: { datasets: [
      { label: "vertical acceleration", data: xy(t.vertical), borderColor: "#0f766e", borderWidth: 2, pointRadius: 0 },
      { label: "horizontal (magnitude)", data: xy(t.horizontal), borderColor: "#c2410c", borderWidth: 1.5, pointRadius: 0 },
    ] },
    options: {
      maintainAspectRatio: false, animation: false,
      plugins: { bands: { items } },
      scales: { x: { type: "linear", min: 0, max: t.t[t.t.length - 1], title: { display: true, text: "seconds" } },
        y: { title: { display: true, text: "m/s²" } } },
    },
  });
}

function renderAlerts(alerts) {
  const open = alerts.filter((a) => !a.resolved);
  const box = document.getElementById("alerts");
  box.replaceChildren(...(open.length ? open.map((a) => el("div", { class: `alert alert-${a.severity}` },
    el("div", { class: "alert-head" }, el("span", { class: `sev sev-${a.severity}`, text: a.severity }),
      el("span", { class: "muted small", text: `since ${when(a.created_at)}` })),
    el("p", { class: "alert-msg", text: a.message }),
    el("button", { class: "btn btn-small", type: "button", onclick: async () => {
      await api(`/api/alerts/${a.id}/resolve`, { method: "POST" }); load(); } }, "Resolve")))
    : [el("p", { class: "muted", text: "No open alerts." })]));
}

async function load() {
  const [d, rides] = await Promise.all([api(`/api/lifts/${encodeURIComponent(LIFT_ID)}`),
    api(`/api/lifts/${encodeURIComponent(LIFT_ID)}/rides?limit=25`)]);
  document.title = `${d.name}: LiftPulse`;
  document.getElementById("building").textContent = d.building_name;
  document.getElementById("name").textContent = d.name;
  document.getElementById("sentence").textContent = d.sentence;
  document.getElementById("meta").textContent =
    `${d.floors} floors · ${d.rides_total} rides recorded (${d.rides_invalid} rejected) · baseline: first ${d.baseline_rides} valid rides per direction`;
  document.getElementById("pill").replaceChildren(pill(d.status));
  document.getElementById("score").textContent = d.health_score == null ? "–" : Math.round(d.health_score);
  renderAlerts(d.alerts);
  healthChart(d.health_history);
  featureCharts(d.features);

  document.getElementById("rides").replaceChildren(...rides.map((r) => el("tr", { class: "clickable", onclick: () => showRide(r.id) },
    el("td", { text: when(r.started_at) }),
    el("td", { text: r.valid ? `${r.direction} ${r.floors} fl, ${Math.round(r.duration_s)} s` : `rejected (${r.reject_reason})` }),
    el("td", { text: r.ride_score == null ? "–" : Math.round(r.ride_score) }))));
  document.getElementById("reports").replaceChildren(...(d.reports.length ? d.reports.map((r) =>
    el("li", {}, el("strong", { text: r.category.replace("_", " ") }), ` · ${when(r.created_at)}`,
      r.comment ? el("div", { class: "muted", text: r.comment }) : null))
    : [el("li", { class: "muted", text: "No reports." })]));
  await showRide(d.latest_ride_id);
  document.getElementById("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

load().catch((e) => { document.getElementById("updated").textContent = `Failed: ${e.message}`; });
