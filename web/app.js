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

// Rename an input: tap "Rename", then tap the input. The receiver keeps the name (front display, Yamaha's app, and here).
let renameMode = false;
const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
function setRenameMode(on) {
  renameMode = on;
  $("#renamebtn").classList.toggle("on", on); $("#renamebtn").textContent = on ? "Done" : "Rename";
  $("#renamenote").textContent = on ? "Tap an input to rename it." : "";
}
async function renameInput(item) {
  if (READONLY) return toast("Preview only: nothing is sent");
  // The input buttons show the receiver's Input_Name list (System > Input_Output > Input_Name), e.g. AUDIO3 is AUDIO_3 there.
  // Find this input's entry in the receiver's own settings list; only those can be renamed.
  const leaf = schema.find((n) => n.leaf && n.path.length === 4 && n.path[2] === "Input_Name" && norm(n.path[3]) === norm(item.param));
  if (!leaf) return toast("This input can't be renamed");
  const name = prompt(`New name for ${item.title || item.param} (up to 14 characters):`, item.title || "");
  if (name === null) return;
  const v = name.trim();
  if (!v || v === item.title) return;
  if (v.length > 14 || !/^[ -~ -ÿ]+$/.test(v)) return toast("Use up to 14 plain characters");
  await put(`<System><Input_Output><Input_Name><${leaf.path[3]}>${esc(v)}</${leaf.path[3]}></Input_Name></Input_Output></System>`);
  // Keep the other name list (Yamaha's app) the same, when the receiver has one for this input. Best effort only.
  try {
    const list = (await (await fetch("/api/yxc/system/getNameText")).json()).input_list || [];
    const m = list.find((x) => norm(x.id) === norm(item.param));
    if (m) await fetch("/api/rename", { method: "POST", headers: { "Content-Type": "application/json", "X-RX-UI": "1" }, body: JSON.stringify({ id: m.id, text: v }) });
  } catch (e) {}
  toast(`Renamed to ${v}`);
  setRenameMode(false);
  setTimeout(async () => { await loadLists(); refresh(); }, 800);
}

async function loadLists() {
  const di = await get("<Main_Zone><Input><Input_Sel_Item>GetParam</Input_Sel_Item></Input></Main_Zone>");
  inputItems = [...di.querySelectorAll("Input_Sel_Item > *")].map((e) => ({ param: txt(e, "Param"), title: txt(e, "Title") })).filter((x) => x.param);
  // With HDMI Control (CEC) on, the receiver reports each HDMI device's own name here (e.g. "Basement:2") instead of the
  // name you gave the input. Prefer a name you set yourself (anything other than the default "AV1" style name); otherwise keep the
  // device's own name, which is often more useful than "AV2" (e.g. "Fire TV Stick").
  try {
    const names = (await (await fetch("/api/yxc/system/getNameText")).json()).input_list || [];
    inputItems.forEach((i) => { const m = names.find((x) => norm(x.id) === norm(i.param)); if (m && m.text && norm(m.text) !== norm(i.param)) i.title = m.text; });
  } catch (e) {}
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
    items.forEach((i) => box.append(chip(i.title || i.param, i.param === cur, () => (renameMode ? renameInput(i) : put(`<Main_Zone><Input><Input_Sel>${esc(i.param)}</Input_Sel></Input></Main_Zone>`)))));
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
  $("#renamebtn").onclick = () => setRenameMode(!renameMode);
  $("#renamebtn").disabled = READONLY;
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

/* ---------- Listen: one-tap scenes for your own rack ----------
   Scenes come from scenes.json next to server.py (copy scenes.example.json and edit it). Each scene turns the receiver on,
   picks an input and, if you say so, sets Pure Direct / Straight and the Waxwing's output. "note" is shown after the tap:
   use it for anything the page cannot do for you (for example "set the DAC to its coax input"). */
const ON = `<Main_Zone><Power_Control><Power>On</Power></Power_Control></Main_Zone>`;
const INPUT = (p) => `<Main_Zone><Input><Input_Sel>${esc(p)}</Input_Sel></Input></Main_Zone>`;
const PURE = (v) => `<Main_Zone><Sound_Video><Pure_Direct><Mode>${v}</Mode></Pure_Direct></Sound_Video></Main_Zone>`;
const STRAIGHT = (v) => `<Main_Zone><Surround><Program_Sel><Current><Straight>${v}</Straight></Current></Program_Sel></Surround></Main_Zone>`;
const onOff = (v) => (v === true || v === "On" || v === "on" ? "On" : "Off");
let SCENES = [];
async function runScene(s) {
  if (READONLY) return toast("Preview only: nothing is sent");
  let note = s.note || "";
  const steps = [ON, INPUT(s.input)];
  if (s.pure_direct !== undefined) steps.push(PURE(onOff(s.pure_direct)));
  if (s.straight !== undefined) steps.push(STRAIGHT(onOff(s.straight)));
  try {
    for (const cmd of steps) { await xml(wrap(cmd)); await new Promise((r) => setTimeout(r, 300)); }
    if (s.waxwing_output !== undefined) {
      try { await waxSet(18, +s.waxwing_output); } catch (e) { note = "Receiver set, but the Waxwing did not answer: set its output in its app."; }
    }
    toast(s.name);
  } catch (e) { toast("Not finished: " + e.message); }
  $("#listennote").textContent = note;
  setTimeout(refresh, 300);
}
async function buildListen() {
  const box = $("#listen"); if (!box || box.childElementCount) return;
  try { const d = await (await fetch("/api/scenes")).json(); SCENES = (d.scenes || []).filter((s) => s && s.name && s.input);
    if (d.file === "scenes.example.json") $("#listennote").textContent = "Example scenes: copy scenes.example.json to scenes.json and edit it for your rack."; } catch (e) {}
  if (!SCENES.length) { $("#listencard").classList.add("hidden"); return; }
  SCENES.forEach((s) => box.append(chip(s.name, false, () => runScene(s))));
}

/* ---------- Waxwing (Parks Audio phono preamp, Bluetooth LE through the server) ---------- */
let waxMeta = null, waxBusy = 0, waxPeak = 1;
async function waxSet(fn, value) {
  if (READONLY) throw new Error("preview");
  const r = await fetch("/api/waxwing/set", { method: "POST", headers: { "Content-Type": "application/json", "X-RX-UI": "1" }, body: JSON.stringify({ fn, value }) });
  if (!r.ok) throw new Error(await r.text());
}
function waxRow(label, valueText) {
  const row = document.createElement("div"); row.className = "srow";
  const lb = document.createElement("label"); lb.textContent = label; const out = document.createElement("output"); out.textContent = valueText;
  row.append(lb); return [row, out];
}
function waxRender(st) {
  const body = $("#waxbody"); if (Date.now() - waxBusy < 1500) return; body.innerHTML = "";
  const S = st.status; if (!S) return;
  const meta = Object.fromEntries(waxMeta.map((m) => [m.key, m]));
  const send = async (fn, v, label) => { waxBusy = Date.now(); try { await waxSet(fn, v); toast(label); } catch (e) { toast("Waxwing: " + e.message); } waxBusy = 0; setTimeout(waxPoll, 600); };
  // choice rows: chips
  ["output", "load", "mode", "magic", "derumble", "bassboost"].forEach((k) => {
    const m = meta[k], cur = S[k]; if (!m || !cur) return;
    const wrap1 = document.createElement("div"); wrap1.className = "chips small"; wrap1.style.margin = "4px 0 10px";
    const lab = document.createElement("span"); lab.className = "muted"; lab.style.cssText = "font-size:13px;align-self:center;min-width:96px"; lab.textContent = m.label; wrap1.append(lab);
    m.labels.forEach((t, i) => wrap1.append(chip(t, i === cur.value, () => send(m.fn, i, `${m.label}: ${t}`))));
    body.append(wrap1);
  });
  // list choices: selects
  ["eq", "lo", "hi"].forEach((k) => {
    const m = meta[k], cur = S[k]; if (!m || !cur) return;
    const f = document.createElement("label"); f.className = "field"; f.style.margin = "4px 0 10px"; f.innerHTML = `<span>${m.label}</span>`;
    const sel = document.createElement("select"); m.labels.forEach((t, i) => { const o = document.createElement("option"); o.value = i; o.textContent = t; if (i === cur.value) o.selected = true; sel.append(o); });
    sel.disabled = READONLY; sel.onchange = () => send(m.fn, +sel.value, `${m.label}: ${m.labels[+sel.value]}`); f.append(sel); body.append(f);
  });
  // number settings: sliders (sent when you let go)
  [["gain", (v) => `${v - 4} dB`], ["volume", (v) => (v === 0 ? "Muted" : v === 32 ? "0 dB" : `${v}/32`)], ["warmth", (v) => `${v - 6 >= 0 ? "+" : ""}${v - 6}`],
   ["air", (v) => `${v - 6 >= 0 ? "+" : ""}${v - 6}`], ["treble", (v) => `${v - 6 >= 0 ? "+" : ""}${v - 6} dB`], ["bass", (v) => `${v - 6 >= 0 ? "+" : ""}${v - 6} dB`],
   ["balance", (v) => (v === 15 ? "Center" : v < 15 ? `L${15 - v}` : `R${v - 15}`)]].forEach(([k, fmt]) => {
    const m = meta[k], cur = S[k]; if (!m || !cur) return;
    const [row, out] = waxRow(m.label, fmt(cur.value));
    const inp = document.createElement("input"); inp.type = "range"; inp.min = m.min; inp.max = m.max; inp.step = 1; inp.value = cur.value; inp.disabled = READONLY;
    inp.oninput = () => { waxBusy = Date.now(); out.textContent = fmt(+inp.value); };
    inp.onchange = () => send(m.fn, +inp.value, `${m.label}: ${fmt(+inp.value)}`);
    row.append(inp, out); body.append(row);
    if (k === "gain") {
      const p = document.createElement("div"); p.className = "chips small"; p.style.margin = "0 0 10px";
      [["MM 38 dB", 42], ["MC 56 dB", 60]].forEach(([t, v]) => p.append(chip(t, cur.value === v, () => send(m.fn, v, `Gain: ${t}`))));
      body.append(p);
    }
    if (k === "volume") {
      const p = document.createElement("div"); p.className = "chips small"; p.style.margin = "0 0 10px";
      p.append(chip(cur.value === 0 ? "Unmute (cue)" : "Mute (cue)", cur.value === 0, () => {
        if (cur.value === 0) send(m.fn, +(localStorage.getItem("waxvol") || 32), "Waxwing unmuted");
        else { try { localStorage.setItem("waxvol", cur.value); } catch (e) {} send(m.fn, 0, "Waxwing muted"); }
      }));
      body.append(p);
    }
  });
  // levels (peak, relative; the Waxwing does not document the scale)
  const M = S.meters; if (M) {
    waxPeak = Math.max(waxPeak * 0.98, M.adcL, M.adcR, 1);
    const lv = document.createElement("div"); lv.className = "note";
    const bar = (v) => `<span style="display:inline-block;height:8px;width:${Math.round(100 * v / waxPeak)}px;max-width:100px;background:var(--accent);border-radius:4px;vertical-align:middle"></span>`;
    lv.innerHTML = `Levels L ${bar(M.adcL)} R ${bar(M.adcR)} · clips ${M.adcClips}/${M.dacClips}` + (M.stereo ? "" : " · mono signal");
    body.append(lv);
  }
}
async function waxPoll() {
  if (document.hidden) return;
  let st; try { st = await (await fetch("/api/waxwing/status")).json(); } catch (e) { return; }
  if (!st.configured) return;
  $("#waxcard").classList.remove("hidden"); waxMeta = st.settings;
  $("#waxstate").textContent = st.connected ? "connected" : "connecting…";
  $("#waxnote").textContent = st.connected ? (st.slow ? "Changes take about 5–10 seconds to reach the Waxwing (it reconnects every few seconds)." : "")
    : (st.error || "Looking for the Waxwing over Bluetooth. Close the Waxwing phone app if it is open.");
  if (st.connected) waxRender(st);
}

/* ---------- GlitterVis (the TV visualizer on this PC) ---------- */
let glvBusy = 0;
async function glv(cmd) {
  glvBusy = Date.now();
  try {
    const r = await fetch("/api/glittervis/" + cmd, { method: "POST", headers: { "X-RX-UI": "1" } });
    if (!r.ok) $("#glvnote").textContent = await r.text();
  } catch (e) {}
  setTimeout(glvPoll, cmd === "start" ? 3000 : 400);
}
function glvChip(label, cmd, on, title) {
  const b = document.createElement("button"); b.className = "chip" + (on ? " on" : ""); b.textContent = label;
  if (title) b.title = title; b.onclick = () => glv(cmd); return b;
}
async function glvPoll() {
  if (document.hidden) return;
  let st; try { st = await (await fetch("/api/glittervis/status")).json(); } catch (e) { return; }
  if (!st.configured) return;
  $("#glvcard").classList.remove("hidden");
  const run = $("#glvrun"), sc = $("#glvscene"), win = $("#glvwin");
  run.innerHTML = ""; sc.innerHTML = ""; win.innerHTML = "";
  if (!st.running) {
    $("#glvstate").textContent = "off";
    $("#glvnote").textContent = Date.now() - glvBusy < 8000 ? "Starting on the TV…" : "Music visualizer for the TV.";
    run.append(glvChip("▶ Start on the TV", "start", false));
    return;
  }
  $("#glvstate").textContent = st.scene + (st.locked ? " · locked" : "");
  $("#glvnote").textContent = st.track ? "♪ " + st.track : "";
  run.append(glvChip("◀ Prev", "prev"), glvChip("Next ▶", "next"), glvChip("Lock", "lock", st.locked, "Stay on this scene"),
    glvChip("Calm", "calm", st.calm, "Slower and softer"), glvChip("Title", "title", false, "Show the track title"), glvChip("■ Stop", "stop"));
  (st.scenes || []).forEach((n) => sc.append(glvChip(n[0].toUpperCase() + n.slice(1), "scene/" + n, n === st.scene)));
  win.append(glvChip("Full screen", "fullscreen", st.fullscreen), glvChip("Small", "size/small", !st.fullscreen && st.size === "small"),
    glvChip("Medium", "size/medium", !st.fullscreen && st.size === "medium"), glvChip("Large", "size/large", !st.fullscreen && st.size === "large"));
}

/* ---------- Games (GlitterMame: arcade + console menu on the TV) ---------- */
let gameBusy = 0;
async function games(cmd) {
  gameBusy = Date.now();
  try {
    const r = await fetch("/api/games/" + cmd, { method: "POST", headers: { "X-RX-UI": "1" } });
    if (!r.ok) $("#gamenote").textContent = await r.text();
  } catch (e) {}
  setTimeout(gamesPoll, cmd === "start" ? 3000 : 800);
}
async function gamesPoll() {
  if (document.hidden) return;
  let st; try { st = await (await fetch("/api/games/status")).json(); } catch (e) { return; }
  if (!st.configured) return;
  $("#gamecard").classList.remove("hidden");
  const run = $("#gamerun"); run.innerHTML = "";
  const chip = (label, cmd, title) => { const b = document.createElement("button"); b.className = "chip"; b.textContent = label;
    if (title) b.title = title; b.onclick = () => games(cmd); return b; };
  if (st.running) {
    $("#gamestate").textContent = "on";
    $("#gamenote").textContent = "Games are open on the TV. On the pad, Select + Start leaves a game.";
    run.append(chip("■ Close games", "stop", "Ends the games menu and any game"));
  } else {
    $("#gamestate").textContent = "off";
    $("#gamenote").textContent = Date.now() - gameBusy < 8000 ? "Starting on the TV…" : "Arcade and console games on the TV.";
    run.append(chip("▶ Start on the TV", "start"));
  }
}

/* ---------- Lights (LIFX bulbs on the home network, through the server) ----------
   One-tap presets act on every bulb; each bulb also gets its own on/off, brightness and warmth. White light only. */
const LIGHT_PRESETS = [
  ["☀ Daylight", { power: true, brightness: 100, kelvin: 5000 }],
  ["☀ Daylight 50%", { power: true, brightness: 50, kelvin: 5000 }],
  ["Warm 50%", { power: true, brightness: 50, kelvin: 2700 }],
  ["All off", { power: false }],
];
let lightBulbs = [], lightBusy = 0, lightK = [2500, 6500];
async function lightSet(macs, change, label) {
  if (READONLY) return toast("Preview only: nothing is sent");
  lightBusy = Date.now();
  try {
    const r = await fetch("/api/lifx/set", { method: "POST", headers: { "Content-Type": "application/json", "X-RX-UI": "1" }, body: JSON.stringify({ macs, ...change }) });
    if (!r.ok) throw new Error(await r.text());
    if (label) toast(label);
  } catch (e) { toast("Lights: " + e.message); }
  lightBusy = 0; setTimeout(lightPoll, 700);
}
function lightRender() {
  const pre = $("#lightpresets"), body = $("#lightbody");
  const all = lightBulbs.filter((b) => b.online).map((b) => b.mac);
  if (!pre.childElementCount) LIGHT_PRESETS.forEach(([t, c]) => pre.append(chip(t, false, () => lightSet(lightBulbs.filter((b) => b.online).map((b) => b.mac), c, t))));
  if (Date.now() - lightBusy < 1500) return;
  body.innerHTML = "";
  lightBulbs.forEach((b) => {
    const box = document.createElement("div"); box.style.margin = "10px 0 4px";
    const head = document.createElement("div"); head.className = "chips small";
    const name = document.createElement("span"); name.className = "muted"; name.style.cssText = "font-size:13px;align-self:center;min-width:70px"; name.textContent = "Light " + b.label;
    head.append(name);
    if (!b.online) { const n = document.createElement("span"); n.className = "muted"; n.style.fontSize = "13px"; n.textContent = "not answering"; head.append(n); body.append(head); return; }
    head.append(chip(b.on ? "On" : "Off", b.on, () => lightSet([b.mac], { power: !b.on }, `Light ${b.label} ${b.on ? "off" : "on"}`)));
    if (b.color) { const n = document.createElement("span"); n.className = "muted"; n.style.fontSize = "12px"; n.textContent = "(a color from the LIFX app)"; head.append(n); }
    box.append(head);
    [["Brightness", "brightness", 1, 100, 1, (v) => v + "%"], ["Warmth", "kelvin", lightK[0], lightK[1], 100, (v) => v + "K"]].forEach(([lab, key, mn, mx, st, fmt]) => {
      const row = document.createElement("div"); row.className = "srow";
      const lb = document.createElement("label"); lb.textContent = lab; const out = document.createElement("output");
      const inp = document.createElement("input"); inp.type = "range"; inp.min = mn; inp.max = mx; inp.step = st; inp.disabled = READONLY;
      inp.value = Math.max(mn, Math.min(mx, b[key])); out.textContent = fmt(+inp.value);
      inp.oninput = () => { lightBusy = Date.now(); out.textContent = fmt(+inp.value); };
      inp.onchange = () => lightSet([b.mac], { [key]: +inp.value }, `Light ${b.label}: ${fmt(+inp.value)}`);
      row.append(lb, inp, out); box.append(row);
    });
    body.append(box);
  });
  const on = lightBulbs.filter((b) => b.online && b.on).length;
  $("#lightstate").textContent = all.length ? `${on} of ${all.length} on` : "not answering";
}
async function lightPoll() {
  if (document.hidden || Date.now() - lightBusy < 1500) return;
  let st; try { st = await (await fetch("/api/lifx/status")).json(); } catch (e) { return; }
  if (!st.configured) return;
  $("#lightcard").classList.remove("hidden");
  lightBulbs = st.bulbs || []; lightK = [st.kmin || 2500, st.kmax || 6500];
  lightRender();
}

/* ---------- drag the boxes around (order saved in this browser) ---------- */
function layoutKey() { return "cardOrder"; }
function applyOrder() {
  const home = $("#home"); let order = [];
  try { order = JSON.parse(localStorage.getItem(layoutKey()) || "[]"); } catch (e) {}
  order.forEach((id) => { const el = document.getElementById(id); if (el && el.parentElement === home) home.append(el); });
  [...home.children].filter((c) => !order.includes(c.id)).forEach((c) => home.append(c));
}
function saveOrder() { try { localStorage.setItem(layoutKey(), JSON.stringify([...$("#home").children].map((c) => c.id).filter(Boolean))); } catch (e) {} }
function makeDraggable() {
  // Pointer-based (works with mouse AND touch, unlike HTML drag-and-drop). Drag a box by its title, or by the small grip
  // on boxes that have no title (the volume box); sliders and buttons inside a box are never drag handles.
  const home = $("#home");
  [...home.querySelectorAll(":scope > section.card")].forEach((card, i) => {
    if (!card.id) card.id = "card" + i;
    let h = card.querySelector("h2");
    if (!h) { h = document.createElement("div"); h.className = "grip"; h.title = "Drag to move this box"; h.textContent = "⠿"; card.prepend(h); }
    h.classList.add("draghandle");
    h.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button,input,select,a")) return;
      const sx = e.clientX, sy = e.clientY; let moving = false, target = null, after = false;
      try { h.setPointerCapture(e.pointerId); } catch (x) {}
      const clear = () => [...home.children].forEach((c) => c.classList.remove("dropbefore", "dropafter"));
      const move = (ev) => {
        if (!moving) { if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 6) return; moving = true; card.classList.add("dragging"); }
        ev.preventDefault();
        card.style.pointerEvents = "none";
        const under = document.elementFromPoint(ev.clientX, ev.clientY);
        card.style.pointerEvents = "";
        const c = under && under.closest("#home > section.card");
        clear(); target = null;
        if (c && c !== card) { const r = c.getBoundingClientRect(); after = ev.clientY > r.top + r.height / 2; target = c; c.classList.add(after ? "dropafter" : "dropbefore"); }
      };
      const up = () => {
        h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up); h.removeEventListener("pointercancel", up);
        if (moving && target) { home.insertBefore(card, after ? target.nextSibling : target); saveOrder(); }
        card.classList.remove("dragging"); clear();
      };
      h.addEventListener("pointermove", move); h.addEventListener("pointerup", up); h.addEventListener("pointercancel", up);
    });
  });
  applyOrder();
}

function buildPeers(peers) {
  // Buttons to the other MoBetter pages (one copy per receiver). A "port" means the same host, so it works by IP or by name.
  const nav = $("#peers"); nav.textContent = "";
  for (const p of peers) {
    const a = document.createElement("a");
    a.className = "peer";
    a.href = p.url || (location.protocol + "//" + location.hostname + ":" + p.port + "/");
    a.textContent = p.name + " ↗";
    a.title = "Open the " + p.name + " receiver";
    nav.appendChild(a);
  }
}

(async function init() {
  try { const c = Number(localStorage.getItem("ceil")); if (!isNaN(c) && localStorage.getItem("ceil") !== null) ceilTenths = c; } catch (e) {}
  wire();
  if (READONLY) $("#sub").textContent = "preview (read-only)";
  try {
    schema = await (await fetch("/api/schema")).json();
    const cfg = await (await fetch("/api/config")).json();
    if (cfg.model) { document.title = "MoBetter · " + cfg.model; $("#model").textContent = cfg.model; }
    buildPeers(cfg.peers || []);
    await loadLists(); buildSetup(cfg); buildListen();
  } catch (e) { setOnline(false); }
  makeDraggable();
  refresh(); setInterval(() => { if (!dragging || Date.now() - dragging > 2500) { dragging = 0; refresh(); } }, 1500);
  waxPoll(); setInterval(waxPoll, 3000);
  glvPoll(); setInterval(glvPoll, 3000);
  gamesPoll(); setInterval(gamesPoll, 5000);
  lightPoll(); setInterval(lightPoll, 5000);
})();
