"use strict";
/* Google TV remote card: mirrors the onn 4K Pro remote. Buttons go to /api/gtv (server.py -> gtv.py, local network only).
   Volume and mute go to the receiver (the same as MoBetter's volume buttons), because the receiver is what makes the sound. */
(function () {
  const card = document.getElementById("gtvcard");
  if (!card) return;
  const RO = new URLSearchParams(location.search).has("readonly");
  const note = (t) => (document.getElementById("gtvnote").textContent = t || "");
  const say = (m) => (window.toast ? window.toast(m) : note(m));
  async function post(path, body) {
    if (RO) return say("Preview only: nothing is sent");
    const r = await fetch("/api/gtv/" + path, { method: "POST", headers: { "Content-Type": "application/json", "X-RX-UI": "1" }, body: JSON.stringify(body || {}) });
    if (!r.ok) { const t = await r.text(); say("Google TV: " + t); throw new Error(t); }
    return r;
  }
  const key = (k) => post("key", { key: k }).catch(() => {});
  const app = (a) => post("app", { app: a }).catch(() => {});

  // Every element with data-key / data-app / data-rx inside the card is a button.
  card.querySelectorAll("[data-key]").forEach((b) => (b.onclick = () => key(b.dataset.key)));
  card.querySelectorAll("[data-app]").forEach((b) => (b.onclick = () => app(b.dataset.app)));
  card.querySelectorAll("[data-rx]").forEach((b) => (b.onclick = () => { const t = document.getElementById(b.dataset.rx); if (t) t.click(); }));
  if (RO) card.querySelectorAll("button").forEach((b) => (b.disabled = true));

  // Pairing (first time only): the TV shows a 6-digit code; type it here.
  document.getElementById("gtvpair").onclick = async () => {
    try {
      note("Look at the TV for a 6-digit code…");
      await post("pair/start");
      const code = prompt("Type the 6-digit code shown on the TV:");
      if (!code) return note("Pairing cancelled");
      await post("pair/finish", { code: code.trim() });
      say("Google TV paired");
      refresh();
    } catch (e) { note("Pairing did not finish. Try again."); }
  };

  async function refresh() {
    let s = {};
    try { s = await (await fetch("/api/gtv/status")).json(); } catch (e) {}
    if (!s.configured) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    const pairBtn = document.getElementById("gtvpair");
    if (!s.available) { note("Google TV library not installed on this PC (pip install androidtvremote2)"); pairBtn.classList.add("hidden"); return; }
    pairBtn.classList.toggle("hidden", !!s.paired && !!s.connected);
    document.getElementById("gtvpower").classList.toggle("on", s.on === true);
    if (!s.paired) note("Not paired yet: tap Pair once, with the TV on.");
    else if (!s.connected) note("Paired, but the box is not answering (" + (s.error || "offline") + ")");
    else note(s.on === false ? "Box is asleep" : (s.app ? "Now: " + s.app.replace(/^com\./, "") : ""));
  }
  refresh();
  setInterval(refresh, 8000);
})();

/* Screen view: refreshed screenshots of the Google TV (wireless debugging). Off by default; remembered per device. */
(function () {
  const btn = document.getElementById("gtvscreenbtn"), wrap = document.getElementById("gtvscreenwrap"), img = document.getElementById("gtvscreen");
  const pairBtn = document.getElementById("gtvscreenpair"), note = document.getElementById("gtvscreennote");
  if (!btn) return;
  let on = false, busy = false, fails = 0;
  try { on = localStorage.getItem("gtvscreen") === "1"; } catch (e) {}
  async function tick() {
    if (!on || busy || document.hidden) return;
    busy = true;
    try {
      const r = await fetch("/api/gtv/screen?t=" + Date.now());
      if (r.ok) { const b = await r.blob(); const old = img.src; img.src = URL.createObjectURL(b); if (old.startsWith("blob:")) URL.revokeObjectURL(old); fails = 0; pairBtn.classList.add("hidden"); note.textContent = "Streaming apps show black here; they still play on the TV."; }
      else { fails++; if (fails >= 2) { note.textContent = "No picture yet: turn on Wireless debugging on the TV, then tap Pair screen."; pairBtn.classList.remove("hidden"); } }
    } catch (e) {}
    busy = false;
  }
  function set(v) {
    on = v; try { localStorage.setItem("gtvscreen", v ? "1" : "0"); } catch (e) {}
    wrap.classList.toggle("hidden", !v); btn.classList.toggle("on", v); btn.textContent = v ? "Hide screen" : "Show screen";
    if (v) tick(); else fetch("/api/gtv/screen/off", { method: "POST", headers: { "Content-Type": "application/json", "X-RX-UI": "1" }, body: "{}" }).catch(() => {});
  }
  btn.onclick = () => set(!on);
  pairBtn.onclick = async () => {
    const port = prompt("On the TV: Developer options > Wireless debugging > Pair device with pairing code. Type the PORT shown after the colon (for example 37215):");
    if (!port) return;
    const code = prompt("Now type the 6-digit pairing code shown on the TV:");
    if (!code) return;
    const r = await fetch("/api/gtv/screen/pair", { method: "POST", headers: { "Content-Type": "application/json", "X-RX-UI": "1" }, body: JSON.stringify({ port: port.trim(), code: code.trim() }) });
    note.textContent = r.ok ? "Paired. The picture should appear in a moment." : "Pairing failed: " + (await r.text());
    if (r.ok) { fails = 0; tick(); }
  };
  set(on);
  setInterval(tick, 1200);
})();
