// Maintenance dashboard: every lift's health and the open alerts. Refreshes every 5 s.
"use strict";

const { el, pill, ago, api, ARROWS } = LP;
const REFRESH_MS = 5000;

function tile(label, value, cls = "") {
  return el("div", { class: `tile ${cls}` }, el("div", { class: "tile-value", text: String(value) }),
    el("div", { class: "tile-label", text: label }));
}

function renderTiles(lifts, alerts) {
  const count = (s) => lifts.filter((l) => l.status === s).length;
  const critical = alerts.filter((a) => a.severity === "critical").length;
  document.getElementById("tiles").replaceChildren(
    tile("Lifts monitored", lifts.length),
    tile("Healthy", count("green"), "tile-green"),
    tile("Watch", count("yellow"), "tile-yellow"),
    tile("Needs attention", count("red"), "tile-red"),
    tile("Open alerts", alerts.length, critical ? "tile-red" : ""),
  );
}

function liftCard(l) {
  const [arrow, arrowText] = ARROWS[l.trend] || ARROWS.flat;
  const score = l.health_score == null ? "–" : Math.round(l.health_score);
  return el("a", { class: `lift-card status-${l.status}`, href: `/lifts/${encodeURIComponent(l.id)}` },
    el("div", { class: "lift-card-head" },
      el("div", {}, el("div", { class: "muted small", text: l.building_name }),
        el("div", { class: "lift-name", text: l.name })),
      pill(l.status)),
    el("div", { class: "lift-card-body" },
      el("div", { class: "score" }, el("span", { class: "score-value", text: score }),
        el("span", { class: "score-unit", text: "/100" })),
      el("div", { class: `trend trend-${l.trend}`, title: arrowText, text: `${arrow} ${arrowText}` })),
    el("div", { class: "lift-card-foot muted small" },
      el("span", { text: `${l.rides_7d} rides in 7 days` }),
      el("span", { text: l.open_alerts ? `${l.open_alerts} open alert${l.open_alerts > 1 ? "s" : ""}` : "No alerts" }),
      el("span", { text: `Last ride ${ago(l.last_ride_at)}` })));
}

function alertItem(a, onResolve) {
  return el("div", { class: `alert alert-${a.severity}` },
    el("div", { class: "alert-head" },
      el("span", { class: `sev sev-${a.severity}`, text: a.severity }),
      el("a", { href: `/lifts/${encodeURIComponent(a.lift_id)}`, class: "alert-lift", text: a.lift_name }),
      el("span", { class: "muted small", text: `since ${LP.when(a.created_at)}` })),
    el("p", { class: "alert-msg", text: a.message }),
    el("button", { class: "btn btn-small", type: "button", onclick: () => onResolve(a.id) }, "Resolve"));
}

async function resolve(id) {
  await api(`/api/alerts/${id}/resolve`, { method: "POST" });
  refresh();
}

async function refresh() {
  try {
    const [lifts, alerts] = await Promise.all([api("/api/lifts"), api("/api/alerts")]);
    renderTiles(lifts, alerts);
    const order = { red: 0, yellow: 1, green: 2, grey: 3 };
    lifts.sort((a, b) => order[a.status] - order[b.status] || a.name.localeCompare(b.name));
    document.getElementById("lifts").replaceChildren(
      ...(lifts.length ? lifts.map(liftCard)
        : [el("p", { class: "muted" }, "No lifts yet. ", el("a", { href: "/admin", text: "Add one" }), ".")]));
    document.getElementById("alerts").replaceChildren(
      ...(alerts.length ? alerts.map((a) => alertItem(a, resolve))
        : [el("p", { class: "muted", text: "No open alerts. All lifts are within their normal range." })]));
    document.getElementById("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    document.getElementById("updated").textContent = `Update failed: ${e.message}`;
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
