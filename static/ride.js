// Resident ride page: records the phone's accelerometer during one lift ride
// and uploads it. Opened from the QR code at /r/<lift_id>.
"use strict";

const LIFT_ID = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
const MAX_RIDE_MS = 120000;     // hard limit
const STILL_AFTER_STOP_MS = 3000; // auto-stop: stationary this long after braking
const MOVE_TH = 0.25;            // m/s^2 deviation of |a| from rest = lift accelerating
const STILL_TH = 0.08;           // m/s^2 deviation counted as "stationary"
const STILL_ROT_DPS = 15;        // rotation below this counts as stationary

const $ = (id) => document.getElementById(id);
const memory = {};

function deviceId() {
  const make = () => (crypto.randomUUID ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36));
  try {
    let id = localStorage.getItem("liftpulse_device");
    if (!id) { id = make(); localStorage.setItem("liftpulse_device", id); }
    return id;
  } catch (e) {
    if (!memory.device) memory.device = make();   // private mode / blocked storage
    return memory.device;
  }
}

function show(el, on) { el.classList.toggle("hidden", !on); }
function showError(msg) { $("error").textContent = msg; show($("error"), !!msg); }

// ------------------------------------------------------------ lift status

function renderStatus(s) {
  $("building").textContent = s.building_name;
  $("lift-name").textContent = s.lift_name;
  document.title = `${s.lift_name}: LiftPulse`;
  $("status").innerHTML = "";
  const dot = document.createElement("span");
  dot.className = `dot dot-${s.status}`;
  const text = document.createElement("span");
  text.textContent = s.sentence;
  $("status").append(dot, text);
}

async function loadStatus() {
  try {
    const r = await fetch(`/api/lifts/${encodeURIComponent(LIFT_ID)}/status`);
    if (r.status === 404) {
      $("lift-name").textContent = "Unknown lift";
      $("status-text").textContent = "This QR code doesn't belong to a registered lift.";
      return false;
    }
    if (!r.ok) throw new Error(r.statusText);
    renderStatus(await r.json());
    return true;
  } catch (e) {
    $("status-text").textContent = "Couldn't load the lift status. Check your connection.";
    return true; // recording can still work
  }
}

// -------------------------------------------------------------- recording

const rec = {
  samples: [], t0: 0, timer: null, wakeLock: null, running: false,
  g0: null, window: [], ema: null, phase: "waiting", sign: 0,
  pulseSince: 0, stillSince: 0, live: [],
};

function resetRec() {
  Object.assign(rec, {
    samples: [], t0: performance.now(), g0: null, window: [], ema: null,
    phase: "waiting", sign: 0, pulseSince: 0, stillSince: 0, live: [],
  });
}

function round(v, d) { return v == null ? null : Math.round(v * 10 ** d) / 10 ** d; }

function onMotion(ev) {
  const a = ev.accelerationIncludingGravity;
  if (!a || a.x == null || a.y == null || a.z == null) return;
  const r = ev.rotationRate || {};
  const t = performance.now() - rec.t0;
  rec.samples.push([round(t, 1), round(a.x, 4), round(a.y, 4), round(a.z, 4),
    round(r.alpha, 2), round(r.beta, 2), round(r.gamma, 2)]);

  const mag = Math.hypot(a.x, a.y, a.z);
  const rot = Math.hypot(r.alpha || 0, r.beta || 0, r.gamma || 0);
  rec.ema = rec.ema == null ? mag : rec.ema + 0.15 * (mag - rec.ema);
  trackPhase(t, mag, rot);
  rec.live.push([t, rec.g0 == null ? 0 : rec.ema - rec.g0]);
}

// Auto-stop: remember |a| at rest, wait for the start pulse, then for the
// opposite (braking) pulse, then for the phone to be still for 3 s.
function trackPhase(t, mag, rot) {
  if (rec.phase === "waiting") {
    rec.window.push([t, mag]);
    while (rec.window.length && t - rec.window[0][0] > 500) rec.window.shift();
    const m = rec.window.map((w) => w[1]);
    const mean = m.reduce((s, v) => s + v, 0) / m.length;
    const sd = Math.sqrt(m.reduce((s, v) => s + (v - mean) ** 2, 0) / m.length);
    if (m.length > 10 && sd < 0.05 && rot < STILL_ROT_DPS) rec.g0 = mean;
  }
  if (rec.g0 == null) return;
  const dev = rec.ema - rec.g0;

  if (rec.phase === "waiting") {
    if (Math.abs(dev) > MOVE_TH) {
      if (!rec.pulseSince) rec.pulseSince = t;
      if (t - rec.pulseSince > 250) {
        rec.phase = "moving"; rec.sign = Math.sign(dev); rec.pulseSince = 0;
        $("rec-hint").textContent = rec.sign > 0 ? "Lift moving (going up?)." : "Lift moving (going down?).";
      }
    } else rec.pulseSince = 0;
  } else if (rec.phase === "moving") {
    if (dev * rec.sign < -MOVE_TH) {
      if (!rec.pulseSince) rec.pulseSince = t;
      if (t - rec.pulseSince > 200) { rec.phase = "braking"; $("rec-hint").textContent = "Lift slowing down…"; }
    } else rec.pulseSince = 0;
  } else if (rec.phase === "braking") {
    if (Math.abs(dev) < STILL_TH && rot < STILL_ROT_DPS) {
      if (!rec.stillSince) rec.stillSince = t;
      if (t - rec.stillSince > STILL_AFTER_STOP_MS) stopRecording("auto");
      else $("rec-hint").textContent = "Lift stopped. Finishing…";
    } else rec.stillSince = 0;
  }
}

function drawTrace() {
  const c = $("trace");
  const ctx = c.getContext("2d");
  const w = c.width, h = c.height, span = 10000, scale = h / 3; // ±1.5 m/s²
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#cbd5e1"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  const now = performance.now() - rec.t0;
  ctx.strokeStyle = "#0f766e"; ctx.lineWidth = 3; ctx.beginPath();
  let first = true;
  for (const [t, v] of rec.live) {
    if (now - t > span) continue;
    const x = w - ((now - t) / span) * w;
    const y = h / 2 - Math.max(-1.5, Math.min(1.5, v)) * scale;
    if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
  }
  ctx.stroke();
  $("rec-time").textContent = `${Math.floor(now / 1000)} s`;
}

async function startRecording() {
  showError("");
  show($("result"), false);
  if (!window.isSecureContext) {
    showError("Motion sensors need a secure (https://) connection.");
    return;
  }
  if (typeof DeviceMotionEvent === "undefined") {
    showError("This browser doesn't give access to motion sensors.");
    return;
  }
  // iOS 13+: permission must be requested inside the tap handler.
  if (typeof DeviceMotionEvent.requestPermission === "function") {
    try {
      const answer = await DeviceMotionEvent.requestPermission();
      if (answer !== "granted") {
        showError("Motion access was denied. Allow it in the browser settings to check the lift.");
        return;
      }
    } catch (e) {
      showError("Couldn't get motion access: " + e.message);
      return;
    }
  }
  try { rec.wakeLock = await navigator.wakeLock?.request("screen"); } catch (e) { rec.wakeLock = null; }

  resetRec();
  rec.running = true;
  window.addEventListener("devicemotion", onMotion);
  show($("start"), false); show($("stop"), true); show($("recording"), true);
  $("rec-hint").textContent = "Waiting for the lift to move.";
  rec.timer = setInterval(() => {
    drawTrace();
    const t = performance.now() - rec.t0;
    if (t > 2500 && rec.samples.length === 0) stopRecording("nosensor");
    else if (t > MAX_RIDE_MS) stopRecording("limit");
  }, 50);
}

async function stopRecording(why) {
  if (!rec.running) return;
  rec.running = false;
  window.removeEventListener("devicemotion", onMotion);
  clearInterval(rec.timer);
  try { await rec.wakeLock?.release(); } catch (e) { /* ignore */ }
  show($("stop"), false); show($("recording"), false); show($("start"), true);
  $("start").textContent = "Record another ride";

  if (why === "nosensor") {
    showError("No motion data arrived. This device or browser may not have an accelerometer.");
    return;
  }
  await upload();
}

async function upload() {
  const result = $("result");
  result.innerHTML = "<p>Analysing your ride…</p>";
  show(result, true);
  const intervals = rec.samples.length > 1
    ? (rec.samples[rec.samples.length - 1][0] - rec.samples[0][0]) / (rec.samples.length - 1) : 0;
  const body = {
    lift_id: LIFT_ID,
    device_id: deviceId(),
    sample_rate_hint: intervals > 0 ? Math.round(1000 / intervals) : null,
    samples: rec.samples,
  };
  try {
    const r = await fetch("/api/rides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`upload failed (${r.status})`);
    renderResult(await r.json());
  } catch (e) {
    result.innerHTML = "";
    showError("Couldn't upload the ride: " + e.message);
  }
}

function renderResult(res) {
  const result = $("result");
  result.innerHTML = "";
  const h = document.createElement("h2");
  const p = document.createElement("p");
  if (res.valid) {
    const f = res.features;
    h.textContent = "Thanks, ride recorded";
    const floors = f.floors_travelled_est;
    p.textContent = `${f.direction === "up" ? "Up" : "Down"} ${floors} floor${floors === 1 ? "" : "s"} in ${Math.round(f.duration_s)} s.`;
  } else {
    h.textContent = "This ride couldn't be used";
    p.textContent = res.reject_message || "The recording couldn't be analysed.";
  }
  result.append(h, p);
  renderStatus(res.lift_status);
}

// ---------------------------------------------------------------- reports

async function sendReport(ev) {
  ev.preventDefault();
  const form = ev.target;
  const data = new FormData(form);
  try {
    const r = await fetch("/api/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lift_id: LIFT_ID, category: data.get("category"), comment: data.get("comment") || "" }),
    });
    if (!r.ok) throw new Error(r.statusText);
    $("report").innerHTML = "<h2>Thank you</h2><p>Your report was sent to the maintenance team.</p>";
  } catch (e) {
    showError("Couldn't send the report. Please try again.");
  }
}

// ------------------------------------------------------------------- init

$("start").addEventListener("click", startRecording);
$("stop").addEventListener("click", () => stopRecording("manual"));
$("report-open").addEventListener("click", () => {
  show($("report"), true);
  $("report").scrollIntoView({ behavior: "smooth" });
});
$("report-form").addEventListener("submit", sendReport);

loadStatus().then((known) => { $("start").disabled = !known; });
