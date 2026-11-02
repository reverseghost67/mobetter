"use strict";
const $ = (s) => document.querySelector(s);
const READONLY = new URLSearchParams(location.search).has("readonly");   // ?readonly=1 : look, but every control is disabled and nothing is sent
const wrap = (inner, cmd = "PUT") => `<YAMAHA_AV cmd="${cmd}">${inner}</YAMAHA_AV>`;
let schema = [], online = false, dragging = 0, lastState = null, ceilTenths = -200;

async function xml(body, confirm) {
  const r = await fetch("/api/xml", { method: "POST", headers: { "Content-Type": "text/xml", "X-RX-UI": "1", ...(confirm ? { "X-Confirm": "yes" } : {}) }, body });
  if (!r.ok) throw new Error(r.status === 409 ? "needs confirmation" : "HTTP " + r.status);
  const d = new DOMParser().parseFromString(await r.text(), "text/xml");
  const rc = d.documentElement.getAttribute("RC");
  if (rc && rc !== "0") throw new Error("receiver said RC " + rc);
  return d;
}
const get = (inner) => xml(wrap(inner, "GET"));
async function put(inner, confirm) {
  if (READONLY) return toast("Preview only: nothing is sent");
  try { await xml(wrap(inner), confirm); } catch (e) { toast("Not changed: " + e.message); }
  setTimeout(refresh, 250);
}
const txt = (d, sel) => { const n = d.querySelector(sel); return n ? n.textContent : null; };
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2600); }

/* ---------- schema helpers (the receiver's own function tree, parsed once by tools/build_schema.py) ---------- */
const node = (path) => schema.find((n) => n.path.join("/") === path);
function options(path) {
  const n = node(path); if (!n) return null;
  const o = (n.control.options || []).filter((x) => x && !x.includes(",,"));
  return o.length ? o : null;
}
function range(path, fallback) {   // numeric limits from the schema's hint text, e.g. "dB(-6.0 to 6.0 / 0.5step)"
  const n = node(path), m = n && n.control.hint && n.control.hint.match(/(-?[\d.]+) to (-?[\d.]+) \/ ([\d.]+)step/);
  return m ? { min: +m[1], max: +m[2], step: +m[3] } : fallback;
}

/* ---------- building blocks ---------- */
function chip(label, on, fn, cls = "") {
  const b = document.createElement("button"); b.className = "chip " + cls + (on ? " on" : ""); b.textContent = label; b.disabled = READONLY; b.onclick = fn; return b;
}
function setOnline(ok, sub) {
  online = ok; $("#dot").className = "dot " + (ok ? "ok" : "bad");
  $("#sub").textContent = ok ? (sub || "") : "receiver not reachable";
}

/* ---------- Home ---------- */
const STREAMING = new Set(["Napster", "SiriusXM", "Pandora", "Spotify", "TIDAL", "Deezer", "AirPlay", "MusicCast Link", "SERVER", "NET RADIO", "Bluetooth", "USB", "Qobuz", "JUKE", "radiko.jp", "Amazon Music", "Alexa"]);
const GROUP_ORDER = ["Streaming", "Tuner", "HDMI", "AV", "Audio", "Other"];
function groupOf(p) {
  if (STREAMING.has(p)) return "Streaming";
  if (/^(TUNER|DAB)/i.test(p)) return "Tuner";
  if (/^HDMI/i.test(p)) return "HDMI";
  if (/^AV\d/i.test(p)) return "AV";
  if (/^(AUDIO|PHONO|V-AUX|AUX|COAXIAL|OPTICAL)/i.test(p)) return "Audio";
  return "Other";
}
let inputItems = [], sceneItems = [];

async function loadLists() {
  const di = await get("<Main_Zone><Input><Input_Sel_Item>GetParam</Input_Sel_Item></Input></Main_Zone>");
  inputItems = [...di.querySelectorAll("Input_Sel_Item > *")].map((e) => ({ param: txt(e, "Param"), title: txt(e, "Title") })).filter((x) => x.param);
  const ds = await get("<Main_Zone><Scene><Scene_Sel_Item>GetParam</Scene_Sel_Item></Scene></Main_Zone>");
  // the list also repeats every input as read-only entries (RW "R"); only the writable ones are scenes. Default names like "SCENE5" read better as "Scene 5".
  sceneItems = [...ds.querySelectorAll("Scene_Sel_Item > *")].filter((e) => txt(e, "RW") === "W")
    .map((e) => ({ param: txt(e, "Param"), title: (txt(e, "Title") || "").replace(/^SCENE(\d+)$/, "Scene $1") })).filter((x) => x.param && x.title);
  const sel = $("#program"); sel.innerHTML = "";
  (options("Main_Zone/Surround/Program_Sel/Current/Sound_Program") || []).forEach((o) => sel.add(new Option(o, o)));
  sel.onchange = () => put(`<Main_Zone><Surround><Program_Sel><Current><Sound_Program>${esc(sel.value)}</Sound_Program></Current></Program_Sel></Surround></Main_Zone>`);
  sel.disabled = READONLY;
}
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function render(d) {
  lastState = d;
  const on = txt(d, "Power_Control > Power") === "On";
  $("#power").classList.toggle("on", on); document.body.classList.toggle("off", !on);
  const val = +txt(d, "Volume > Lvl > Val"); const volEl = $("#vol");
  $("#volnum").textContent = isNaN(val) ? "--" : (val / 10).toFixed(1);
  if (!dragging) volEl.value = val;
  volEl.max = Math.min(165, ceilTenths);
  const muted = txt(d, "Volume > Mute") !== "Off";
  $("#mute").classList.toggle("on", muted); $("#mute").textContent = muted ? "Muted" : "Mute";
  $("#volnote").textContent = val > ceilTenths ? `Above your ${(ceilTenths / 10).toFixed(1)} dB maximum` : "";
  setOnline(true, `${on ? "On" : "Standby"} · ${txt(d, "Program_Sel > Current > Sound_Program") || ""}`);

  // inputs
  const cur = txt(d, "Input > Input_Sel"); const box = $("#inputs"); box.innerHTML = "";
  GROUP_ORDER.forEach((g) => {
    const items = inputItems.filter((i) => groupOf(i.param) === g); if (!items.length) return;
    const lab = document.createElement("div"); lab.className = "muted"; lab.style.cssText = "width:100%;font-size:12px;margin:6px 0 0"; lab.textContent = g; box.append(lab);
    items.forEach((i) => box.append(chip(i.title || i.param, i.param === cur, () => put(`<Main_Zone><Input><Input_Sel>${esc(i.param)}</Input_Sel></Input></Main_Zone>`))));
  });

  // sound
  $("#program").value = txt(d, "Program_Sel > Current > Sound_Program") || "";
  const tg = $("#toggles"); tg.innerHTML = "";
  [["Straight", "Program_Sel > Current > Straight", (v) => `<Main_Zone><Surround><Program_Sel><Current><Straight>${v}</Straight></Current></Program_Sel></Surround></Main_Zone>`],
   ["Enhancer", "Program_Sel > Current > Enhancer", (v) => `<Main_Zone><Surround><Program_Sel><Current><Enhancer>${v}</Enhancer></Current></Program_Sel></Surround></Main_Zone>`],
   ["Pure Direct", "Pure_Direct > Mode", (v) => `<Main_Zone><Sound_Video><Pure_Direct><Mode>${v}</Mode></Pure_Direct></Sound_Video></Main_Zone>`]]
    .forEach(([name, sel, mk]) => { const v = txt(d, sel); tg.append(chip(name, v === "On", () => put(mk(v === "On" ? "Off" : "On")))); });

  // sliders (rebuilt only when not dragging)
  if (!dragging) {
    const s = $("#sliders"); s.innerHTML = "";
    [["Bass", "Sound_Video > Tone > Bass", "Main_Zone/Sound_Video/Tone/Bass", (v) => `<Main_Zone><Sound_Video><Tone><Bass>${v}</Bass></Tone></Sound_Video></Main_Zone>`, { min: -6, max: 6, step: .5 }],
     ["Treble", "Sound_Video > Tone > Treble", "Main_Zone/Sound_Video/Tone/Treble", (v) => `<Main_Zone><Sound_Video><Tone><Treble>${v}</Treble></Tone></Sound_Video></Main_Zone>`, { min: -6, max: 6, step: .5 }],
     ["Subwoofer", "Volume > Subwoofer_Trim", "Main_Zone/Volume/Subwoofer_Trim", (v) => `<Main_Zone><Volume><Subwoofer_Trim>${v}</Subwoofer_Trim></Volume></Main_Zone>`, { min: -6, max: 6, step: .5 }]]
      .forEach(([label, sel, path, mk, fb]) => {
        const v = txt(d, sel + " > Val"); if (v === null) return; const r = range(path, fb);
        const row = document.createElement("div"); row.className = "srow";
        const out = document.createElement("output"); out.textContent = (v / 10).toFixed(1) + " dB";
        const inp = document.createElement("input"); inp.type = "range"; inp.min = r.min * 10; inp.max = r.max * 10; inp.step = r.step * 10; inp.value = v; inp.disabled = READONLY;
        const send = debounce((x) => put(mk(`<Val>${x}</Val><Exp>1</Exp><Unit>dB</Unit>`)), 250);
        inp.oninput = () => { dragging = Date.now(); out.textContent = (inp.value / 10).toFixed(1) + " dB"; send(inp.value); };
        inp.onchange = () => { dragging = 0; };
        const lb = document.createElement("label"); lb.textContent = label; row.append(lb, inp, out); s.append(row);
      });
    const ex = $("#extras"); ex.innerHTML = "";
    [["YPAO Volume", "YPAO_Volume", "Main_Zone/Sound_Video/YPAO_Volume"], ["Extra Bass", "Extra_Bass", "Main_Zone/Sound_Video/Extra_Bass"], ["Adaptive DRC", "Adaptive_DRC", "Main_Zone/Sound_Video/Adaptive_DRC"]].forEach(([label, tag, path]) => {
      const cur2 = txt(d, "Sound_Video > " + tag), opts = options(path); if (cur2 === null || !opts) return;
      const lab = document.createElement("span"); lab.className = "muted"; lab.style.cssText = "font-size:13px;align-self:center;margin-right:2px"; lab.textContent = label; ex.append(lab);
      opts.forEach((o) => ex.append(chip(o, o === cur2, () => put(`<Main_Zone><Sound_Video><${tag}>${esc(o)}</${tag}></Sound_Video></Main_Zone>`))));
      const br = document.createElement("div"); br.style.flexBasis = "100%"; ex.append(br);
    });
  }

  // scenes
  const sc = $("#scenes"); if (!sc.childElementCount) sceneItems.forEach((s) => sc.append(chip(s.title, false, () => put(`<Main_Zone><Scene><Scene_Load>${esc(s.param)}</Scene_Load></Scene></Main_Zone>`))));
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function refresh() {
  if (document.hidden) return;
  try { render(await get("<Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>")); } catch (e) { setOnline(false); }
}

/* ---------- controls ---------- */
function wire() {
  const vol = $("#vol");
  const sendVol = debounce((v) => put(`<Main_Zone><Volume><Lvl><Val>${v}</Val><Exp>1</Exp><Unit>dB</Unit></Lvl></Volume></Main_Zone>`), 220);
  const setVol = (v) => { v = Math.max(-805, Math.min(ceilTenths, Math.round(v / 5) * 5)); vol.value = v; $("#volnum").textContent = (v / 10).toFixed(1); sendVol(v); };
  vol.oninput = () => { dragging = Date.now(); setVol(+vol.value); };
  vol.onchange = () => { setTimeout(() => (dragging = 0), 600); };
  $("#vup").onclick = () => { dragging = Date.now(); setVol(+vol.value + 5); setTimeout(() => (dragging = 0), 900); };
  $("#vdn").onclick = () => { dragging = Date.now(); setVol(+vol.value - 5); setTimeout(() => (dragging = 0), 900); };
  $("#mute").onclick = () => { const m = lastState && txt(lastState, "Volume > Mute") !== "Off"; put(`<Main_Zone><Volume><Mute>${m ? "Off" : "On"}</Mute></Volume></Main_Zone>`); };
  $("#power").onclick = () => { const on = lastState && txt(lastState, "Power_Control > Power") === "On"; put(`<Main_Zone><Power_Control><Power>${on ? "Standby" : "On"}</Power></Power_Control></Main_Zone>`); };
  [...$("#tabs").children].forEach((b) => (b.onclick = () => {
    [...$("#tabs").children].forEach((x) => x.classList.toggle("on", x === b));
    $("#home").classList.toggle("hidden", b.dataset.view !== "home"); $("#setup").classList.toggle("hidden", b.dataset.view !== "setup");
  }));
  [...document.querySelectorAll("button.step,#mute,#power")].forEach((b) => (b.disabled = READONLY));
  vol.disabled = READONLY;
}

/* ---------- Setup (placeholder until the generic renderer is built) ---------- */
function buildSetup(cfg) {
  $("#oldlink").href = `http://${cfg.receiver}/Setup/`;
  const cats = [...new Set(schema.map((n) => n.cat).filter(Boolean))];
  const box = $("#cats");
  const pref = document.createElement("div"); pref.className = "card"; pref.style.margin = "14px 0";
  pref.innerHTML = `<h2>Preferences (this browser)</h2><label class="field"><span>Maximum volume the sliders and buttons allow, in dB</span><input id="ceil" type="number" step="0.5" min="-80" max="16.5" style="width:120px;background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:10px;padding:8px"></label>`;
  $("#catbody").before(pref);
  const ceil = pref.querySelector("#ceil"); ceil.value = (ceilTenths / 10).toFixed(1);
  ceil.onchange = () => { const v = Math.max(-80, Math.min(16.5, +ceil.value || 0)); ceilTenths = Math.round(v * 10); try { localStorage.setItem("ceil", ceilTenths); } catch (e) {} toast("Maximum volume " + v.toFixed(1) + " dB"); refresh(); };
  const zrow = document.createElement("label"); zrow.className = "field"; zrow.style.marginTop = "12px";
  zrow.innerHTML = `<span><input id="zones" type="checkbox"> Also show Zone 2, 3 and 4 settings (hidden by default)</span>`; pref.append(zrow);
  const zb = zrow.querySelector("#zones"); zb.checked = SETUP.zones;
  zb.onchange = () => { SETUP.zones = zb.checked; try { localStorage.setItem("zones", zb.checked ? "1" : "0"); } catch (e) {} const on = box.querySelector(".chip.on"); if (on) on.click(); };
  cats.forEach((c) => { const b = chip(c === "Model Information" ? "Model Information" : c, false, () => { [...box.children].forEach((x) => x.classList.remove("on")); b.classList.add("on"); openCategory(c); }); b.disabled = false; box.append(b); });
}

(async function init() {
  try { const c = Number(localStorage.getItem("ceil")); if (!isNaN(c) && localStorage.getItem("ceil") !== null) ceilTenths = c; } catch (e) {}
  wire();
  if (READONLY) $("#sub").textContent = "preview (read-only)";
  try {
    schema = await (await fetch("/api/schema")).json();
    const cfg = await (await fetch("/api/config")).json();
    if (cfg.model) { document.title = "MoBetter · " + cfg.model; $("#model").textContent = cfg.model; }
    await loadLists(); buildSetup(cfg);
  } catch (e) { setOnline(false); }
  refresh(); setInterval(() => { if (!dragging || Date.now() - dragging > 2500) { dragging = 0; refresh(); } }, 1500);
})();
