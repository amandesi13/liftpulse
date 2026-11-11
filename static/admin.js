// Admin page: create buildings and lifts, print QR stickers.
"use strict";

const { el, api } = LP;

function say(text) { document.getElementById("msg").textContent = text; }

async function qrUrl(liftId) {
  // The QR image needs the admin token; fetch it and show it as a blob URL.
  const r = await fetch(`/api/admin/lifts/${encodeURIComponent(liftId)}/qr.png`,
    { headers: { "X-Admin-Token": LP.token() || "" } });
  return r.ok ? URL.createObjectURL(await r.blob()) : "";
}

async function sticker(lift) {
  const img = el("img", { alt: `QR code for ${lift.name}`, class: "qr" });
  qrUrl(lift.id).then((u) => { img.src = u; });
  return el("div", { class: "sticker" },
    el("div", { class: "sticker-brand", text: "LiftPulse" }),
    el("div", { class: "sticker-lift", text: lift.name }),
    el("div", { class: "muted", text: lift.building_name }),
    img,
    el("div", { class: "sticker-cta", text: "Scan to check this lift or report a problem" }),
    el("div", { class: "sticker-url", text: `/r/${lift.id}` }));
}

async function load() {
  const [buildings, lifts] = await Promise.all([api("/api/admin/buildings"), api("/api/lifts")]);
  const select = document.getElementById("building-select");
  select.replaceChildren(...buildings.map((b) => el("option", { value: b.id, text: b.name })));
  if (!buildings.length) select.append(el("option", { value: "", text: "Create a building first" }));
  document.getElementById("stickers").replaceChildren(
    ...(lifts.length ? await Promise.all(lifts.map(sticker)) : [el("p", { class: "muted", text: "No lifts yet." })]));
}

document.getElementById("building-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  try {
    const b = await api("/api/admin/buildings", { method: "POST",
      body: JSON.stringify({ name: f["name"].value, address: f["address"].value }) });
    say(`Created building ${b.name}.`);
    ev.target.reset();
    await load();
    document.getElementById("building-select").value = String(b.id);
  } catch (e) { say(`Error: ${e.message}`); }
});

document.getElementById("lift-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  try {
    const l = await api("/api/admin/lifts", { method: "POST", body: JSON.stringify({
      building_id: Number(f["building_id"].value), name: f["name"].value,
      floors: Number(f["floors"].value), floor_height_m: Number(f["floor_height_m"].value) }) });
    say(`Created ${l.name}: ${l.ride_url}`);
    f["name"].value = "";
    await load();
  } catch (e) { say(`Error: ${e.message}`); }
});

load().catch((e) => say(`Error: ${e.message}`));
