// Shared helpers for the maintenance pages: admin token, API calls, formatting.
"use strict";

const LP = (() => {
  const KEY = "liftpulse_admin_token";
  let memoryToken = null;

  function readToken() {
    try { return sessionStorage.getItem(KEY) || memoryToken; } catch (e) { return memoryToken; }
  }
  function saveToken(t) {
    memoryToken = t;
    try { sessionStorage.setItem(KEY, t); } catch (e) { /* in-memory only */ }
  }

  // ?token=... in the URL (e.g. a bookmarked demo link) is taken once and removed.
  const params = new URLSearchParams(location.search);
  if (params.has("token")) {
    saveToken(params.get("token"));
    params.delete("token");
    const q = params.toString();
    history.replaceState(null, "", location.pathname + (q ? "?" + q : "") + location.hash);
  }

  function askToken() {
    return new Promise((resolve) => {
      let box = document.getElementById("token-box");
      if (!box) {
        box = document.createElement("div");
        box.id = "token-box";
        box.className = "token-box";
        box.innerHTML = `<form class="card"><h2>Maintenance login</h2>
          <p>Enter the admin token (LIFTPULSE_ADMIN_TOKEN).</p>
          <input type="password" name="token" autocomplete="current-password" required>
          <button class="btn" type="submit">Continue</button></form>`;
        document.body.append(box);
      }
      box.classList.remove("hidden");
      const form = box.querySelector("form");
      form.onsubmit = (ev) => {
        ev.preventDefault();
        saveToken(form.token.value.trim());
        box.classList.add("hidden");
        resolve();
      };
      form.token.focus();
    });
  }

  async function api(path, options = {}) {
    for (let attempt = 0; attempt < 3; attempt++) {
      const token = readToken();
      if (!token) { await askToken(); continue; }
      const headers = Object.assign({ "X-Admin-Token": token }, options.headers || {});
      if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
      const r = await fetch(path, Object.assign({}, options, { headers }));
      if (r.status === 401) { saveToken(""); await askToken(); continue; }
      if (!r.ok) {
        let detail = r.statusText;
        try { detail = (await r.json()).detail || detail; } catch (e) { /* not JSON */ }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      return r.status === 204 ? null : r.json();
    }
    throw new Error("not authorised");
  }

  const STATUS_TEXT = { green: "Healthy", yellow: "Watch", red: "Needs attention", grey: "Collecting data" };
  const ARROWS = { up: ["▲", "Improving"], down: ["▼", "Getting worse"], flat: ["▬", "Stable"] };

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    }
    for (const c of children) if (c != null) e.append(c);
    return e;
  }

  function pill(status) {
    return el("span", { class: `pill pill-${status}` }, el("span", { class: `dot dot-${status}` }),
      STATUS_TEXT[status] || status);
  }

  function ago(isoString) {
    if (!isoString) return "never";
    const s = (Date.now() - new Date(isoString).getTime()) / 1000;
    if (s < 90) return "just now";
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    if (s < 129600) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }

  function when(isoString) {
    return new Date(isoString).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  return { api, el, pill, ago, when, token: readToken, STATUS_TEXT, ARROWS };
})();
