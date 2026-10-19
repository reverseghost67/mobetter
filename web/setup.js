"use strict";
/* Setup tabs: every setting the receiver describes in its own function tree (schema.json), grouped by the same 17 tabs as the old Web Setup page,
   shown with a proper control (choices, range, text) and the live value. A group is shown only if this receiver supports it (GET answers RC 0),
   or if it is an action-only node (the receiver refuses to read those). Zones 2-4 are hidden unless switched on in Preferences. */
const SETUP = { zones: false };
try { SETUP.zones = localStorage.getItem("zones") === "1"; } catch (e) {}
const DANGER = /(^|\/)(Update|Initialize|Factory_Preset|Restart|Reset|Network_Reboot)(\/|$)|Service\/Control/;   // whole path segments, so "Preset" does not match "Reset"
const pretty = (s) => s.replace(/^_+/, "").replace(/_/g, " ");
const keyOf = (p) => p.join("/");

function catRoots(cat) {
  const nodes = schema.filter((n) => n.cat === cat), keys = new Set(nodes.map((n) => keyOf(n.path))), seen = new Set(), out = [];
  nodes.forEach((n) => {
    const k = keyOf(n.path);
    if (seen.has(k) || n.path.some((_, i) => i < n.path.length - 1 && keys.has(keyOf(n.path.slice(0, i + 1))))) return;
    seen.add(k); out.push(n);
  });
  return out.filter((n) => SETUP.zones || !/^Zone_/.test(n.path[0]));
}
function leavesUnder(root) {
  const seen = new Set(), p = root.path;
  return schema.filter((n) => n.leaf && n.path.length >= p.length && p.every((x, i) => n.path[i] === x) && !seen.has(keyOf(n.path)) && seen.add(keyOf(n.path)));
}
// The receiver only answers GetParam for a GROUP, not for a single leaf (HTTP 400). So read the nearest group around a setting, once, and share the answer.
let readCache = new Map();
function readPathFor(root) {
  const p = root.path;
  for (let n = p.length; n >= 1; n--) { const sub = p.slice(0, n); if (schema.some((x) => x.group_get && x.path.length === n && sub.every((v, i) => x.path[i] === v))) return sub; }
  return p;
}
// Which group(s) to read for a setting: the nearest group around it, or (when it is itself above the groups) the groups beneath it.
function readPlan(root) {
  const near = readPathFor(root), isGroup = schema.some((x) => x.group_get && keyOf(x.path) === keyOf(near));
  if (isGroup) return [near];
  const p = root.path, desc = schema.filter((x) => x.group_get && x.path.length > p.length && p.every((v, i) => x.path[i] === v));
  const tops = desc.filter((x) => !desc.some((y) => y !== x && y.path.length < x.path.length && y.path.every((v, i) => x.path[i] === v)));
  const seen = new Set(), out = [];
  tops.forEach((x) => { const k = keyOf(x.path); if (!seen.has(k)) { seen.add(k); out.push(x.path); } });
  return out.length ? out : [near];
}
function readGroup(path, fresh) { const k = keyOf(path); if (fresh || !readCache.has(k)) readCache.set(k, get(nest(path, "GetParam"))); return readCache.get(k); }
const nest = (path, inner) => path.reduceRight((acc, n) => `<${n}>${acc}</${n}>`, inner);
function walk(d, path) {
  let n = d.documentElement;
  for (const name of path) { n = [...n.children].find((c) => c.tagName === name); if (!n) return null; }
  return n;
}
function unitInfo(hint) {
  const m = hint && hint.match(/^([^(]*)\(.*?(-?[\d.]+) to (-?[\d.]+) \/ ([\d.]+)step/);
  return m ? { unit: m[1], min: +m[2], max: +m[3], step: +m[4] } : null;
}
async function writeValue(path, inner, group) {
  const danger = DANGER.test(keyOf(path));
  if (danger && !confirm("This can restart the receiver or change its firmware.\n\n" + path.join(" > ") + "\n\nContinue?")) return;
  await put(nest(path, inner), danger);
  if (group) setTimeout(() => group.reload(true), 500);
}

function makeRow(leaf, valNode, group, isAction) {
  const row = document.createElement("div"); row.className = "srow2"; row.dataset.label = pretty(leaf.name).toLowerCase();
  const lab = document.createElement("div"); lab.className = "lab"; lab.textContent = pretty(leaf.name);
  if (DANGER.test(keyOf(leaf.path))) lab.textContent += "  ⚠";
  const ctl = document.createElement("div"); ctl.className = "ctl";
  const c = leaf.control, ro = leaf.rw === "R", composite = valNode && [...valNode.children].some((x) => x.tagName === "Val");
  const cur = valNode ? (composite ? null : valNode.textContent.trim()) : null;
  const dis = READONLY || ro;
  const opts = (c.options || []).filter((o) => o && !o.includes(",,"));
  // action-only nodes (copy a speaker pattern, load a scene or memory...) do something on the receiver, so ask first
  const send = (inner) => {
    const what = leaf.path.map(pretty).join(" > ") + "  →  " + inner.replace(/<[^>]*>/g, "");
    if (isAction && !confirm(["This runs an action on the receiver:", what, "Continue?"].join(String.fromCharCode(10, 10)))) return;
    return writeValue(leaf.path, inner, group);
  };
  if (composite) {
    const u = unitInfo(c.hint) || { unit: "", min: -999, max: 999, step: 1 };
    const exp = +(valNode.querySelector("Exp")?.textContent || 0), unit = valNode.querySelector("Unit")?.textContent || u.unit;
    const v = (+valNode.querySelector("Val").textContent) / 10 ** exp;
    const inp = Object.assign(document.createElement("input"), { type: "number", value: v.toFixed(exp), step: u.step, min: u.min, max: u.max, disabled: dis });
    inp.onchange = () => send(`<Val>${Math.round(+inp.value * 10 ** exp)}</Val><Exp>${exp}</Exp><Unit>${unit}</Unit>`);
    ctl.append(inp, Object.assign(document.createElement("span"), { className: "unit2", textContent: unit }));
  } else if ((c.type === "buttons" || c.type === "select") && opts.length) {
    if (opts.length <= 8) opts.forEach((o) => { const b = chip(o, o === cur, () => send(esc(o)), "small"); b.disabled = dis; ctl.append(b); });
    else { const s = document.createElement("select"); s.disabled = dis; if (cur && !opts.includes(cur)) s.add(new Option(cur, cur)); opts.forEach((o) => s.add(new Option(o, o))); s.value = cur ?? ""; s.onchange = () => send(esc(s.value)); ctl.append(s); }
  } else if (c.type === "number") {
    const inp = Object.assign(document.createElement("input"), { type: "number", value: cur ?? "", step: c.step, min: c.min, max: c.max, disabled: dis });
    inp.onchange = () => send(esc(inp.value)); ctl.append(inp, Object.assign(document.createElement("span"), { className: "unit2", textContent: c.hint || "" }));
  } else if (c.type === "text" && !ro) {
    const inp = Object.assign(document.createElement("input"), { type: "text", value: cur ?? "", maxLength: c.max, disabled: dis });
    inp.onchange = () => send(esc(inp.value)); ctl.append(inp);
  } else {
    const t = valNode ? (valNode.children.length ? [...valNode.children].map((x) => pretty(x.tagName) + ": " + x.textContent.trim()).join("   ") : cur) : "";
    ctl.append(Object.assign(document.createElement("span"), { className: "ro", textContent: t || "(no value)" }));
  }
  row.append(lab, ctl); return row;
}

function makeGroup(root, onGone) {
  const sec = document.createElement("section"); sec.className = "grp";
  const h = document.createElement("h3"); h.textContent = root.path.map(pretty).join("  ›  "); sec.append(h);
  const body = document.createElement("div"); sec.append(body);
  const g = { sec, async reload(fresh) {
    const docs = []; let err = null;
    for (const p of readPlan(root)) { try { docs.push(await readGroup(p, fresh)); } catch (e) { err = e; } }
    let action = false;
    if (!docs.length) {
      const m = err ? err.message : "";
      if (/RC 2|RC 1/.test(m)) return onGone(sec);
      action = true;      // the receiver refuses to read action-only nodes (copy, load, restart ...)
      if (!/HTTP 500|HTTP 400/.test(m) && !/RC/.test(m)) { body.textContent = "Could not read: " + m; return; }
    }
    body.innerHTML = "";
    let shown = 0;
    leavesUnder(root).forEach((lf) => {
      const vn = docs.map((d) => walk(d, lf.path)).find(Boolean) || null;
      if (!action && !vn) return;           // supported group, but this leaf is not part of this model
      body.append(makeRow(lf, vn, g, action)); shown++;
    });
    if (!shown) onGone(sec);
  } };
  return g;
}

let openToken = 0;
async function openCategory(cat) {
  readCache = new Map();
  const token = ++openToken, box = $("#catbody"); box.innerHTML = "";
  const filter = Object.assign(document.createElement("input"), { type: "search", placeholder: "Filter settings in this tab…", className: "filter" });
  const status = Object.assign(document.createElement("p"), { className: "muted", textContent: "Reading from the receiver…" });
  const mount = document.createElement("div"); box.append(filter, status, mount);
  filter.oninput = () => {
    const q = filter.value.trim().toLowerCase();
    mount.querySelectorAll(".srow2").forEach((r) => (r.style.display = !q || r.dataset.label.includes(q) ? "" : "none"));
    mount.querySelectorAll(".grp").forEach((s) => (s.style.display = [...s.querySelectorAll(".srow2")].some((r) => r.style.display !== "none") ? "" : "none"));
  };
  const roots = catRoots(cat), groups = roots.map((r) => makeGroup(r, (sec) => sec.remove()));
  groups.forEach((g) => mount.append(g.sec));
  let i = 0;
  await Promise.all([0, 1, 2].map(async () => { while (i < groups.length && token === openToken) { const g = groups[i++]; await g.reload(); } }));
  if (token !== openToken) return;
  status.textContent = mount.querySelector(".grp") ? `${mount.querySelectorAll(".srow2").length} settings` : "Nothing in this tab is available on this receiver.";
}
