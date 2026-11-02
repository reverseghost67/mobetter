"""READ-ONLY. Writes a short, sanitized compatibility report you can paste into the "Compatibility report" issue form.
It only asks your receiver questions (the same reads the page makes); it never changes a setting.

    python tools/diagnose.py                          (uses the address saved in config.json)
    python tools/diagnose.py --receiver 192.168.0.50

The report holds: model, firmware, which features the receiver says it has, how many inputs/scenes/settings were found,
and which groups of settings could (not) be read. It never holds your address, MAC, serial or device ID, input names,
scene names or Wi-Fi name. Read it before you post it anyway. It is also saved to data/diagnose-report.md.
"""
import argparse, collections, datetime, json, os, re, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import schema_builder  # noqa: E402

CTRL = "/YamahaRemoteControl/ctrl"
SHAPE = re.compile(r"^[A-Za-z0-9_]+$")


def http(ip, method, path, data=None, headers=None, timeout=10):
    req = urllib.request.Request(f"http://{ip}{path}", data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def xml_get(ip, inner):
    st, body = http(ip, "POST", CTRL, f'<YAMAHA_AV cmd="GET">{inner}</YAMAHA_AV>'.encode(), {"Content-Type": "text/xml"})
    text = body.decode("utf8", "replace")
    m = re.search(r'RC="(\d+)"', text)
    return st, (int(m.group(1)) if m else None), text


def tag(text, name):
    m = re.search(rf"<{name}>([^<]*)</{name}>", text)
    return m.group(1) if m else ""


def group_reads(ip, schema, pause=0.1):
    """Same targets as tools/backup_settings.py: one read per group. Returns Counter and the names of groups that failed (other than 'unsupported')."""
    seen, targets = [], []
    for n in schema:
        if not (n["group_get"] or (n["leaf"] and n["cat"])):
            continue
        p = tuple(n["path"])
        if any(p[: len(q)] == q for q in seen):
            continue
        seen.append(p)
        targets.append(p)
    tally, odd = collections.Counter(), []
    for p in targets:
        inner = "GetParam"
        for name in reversed(p):
            inner = f"<{name}>{inner}</{name}>"
        st, rc, _ = xml_get(ip, inner)
        key = "readable" if rc == 0 else "unsupported on this model" if rc == 2 else "action-only / HTTP error" if st != 200 else f"other (RC {rc})"
        tally[key] += 1
        if key.startswith("other"):
            odd.append("/".join(p))
        time.sleep(pause)
    return len(targets), tally, odd


def scrub(text, ip):
    """Last line of defence: anything that looks like an address or an ID never leaves this function."""
    text = text.replace(ip, "<receiver>")
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", text)
    text = re.sub(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b", "<mac>", text)
    return text


def build_report(ip, pause=0.1):
    out = []
    st, rc, cfg = xml_get(ip, "<System><Config>GetParam</Config></System>")
    if st != 200 or not tag(cfg, "Model_Name"):
        return None, f"Could not read the receiver's identity (HTTP {st}). Check the address and that the receiver is on."
    out += [f"- Model: {tag(cfg, 'Model_Name')}", f"- Firmware: {tag(cfg, 'Version')}"]
    fe = re.search(r"<Feature_Existence>(.*?)</Feature_Existence>", cfg, re.S)
    if fe:
        have = re.findall(r"<([A-Za-z0-9_]+)>1</\1>", fe.group(1))
        lack = re.findall(r"<([A-Za-z0-9_]+)>0</\1>", fe.group(1))
        out += [f"- Features it reports: {', '.join(have) or 'none'}", f"- Features it reports as absent: {', '.join(lack) or 'none'}"]
    st1, _ = http(ip, "GET", "/Setup/", headers={"User-Agent": "MoBetter"})
    st2, raw = http(ip, "GET", "/Setup/", headers={"Accept-Encoding": "gzip", "User-Agent": "MoBetter"})
    nodes = []
    if st2 == 200:
        try:
            nodes = schema_builder.build(schema_builder.fetch_setup_page(ip))
        except Exception:
            nodes = []
    out += [f"- Setup page without gzip: HTTP {st1}; with gzip: HTTP {st2}", f"- Settings found in the Setup page: {len(nodes)}"]
    _, rc_i, inputs = xml_get(ip, "<Main_Zone><Input><Input_Sel_Item>GetParam</Input_Sel_Item></Input></Main_Zone>")
    _, rc_s, scenes = xml_get(ip, "<Main_Zone><Scene><Scene_Sel_Item>GetParam</Scene_Sel_Item></Scene></Main_Zone>")
    _, rc_b, status = xml_get(ip, "<Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>")
    n_in = len(re.findall(r"<Param>", inputs))
    n_sc = len([1 for e in re.findall(r"<[A-Za-z0-9_]+>.*?</[A-Za-z0-9_]+>", scenes, re.S) if "<RW>W</RW>" in e])
    out += [f"- Home page reads: inputs RC {rc_i} ({n_in} found), scenes RC {rc_s} ({n_sc} writable found), basic status RC {rc_b}"]
    prog = [n for n in nodes if n["path"][-3:] == ["Program_Sel", "Current", "Sound_Program"]]
    out += [f"- Sound programs offered: {len(prog[0]['control'].get('options', [])) if prog else 'not found'}"]
    if nodes:
        total, tally, odd = group_reads(ip, nodes, pause)
        out += [f"- Setup groups read: {total}"] + [f"  - {k}: {v}" for k, v in sorted(tally.items())]
        if odd:
            out += [f"- Groups with an unusual answer: {', '.join(odd[:20])}"]
    st3, body = http(ip, "GET", "/YamahaExtendedControl/v1/system/getDeviceInfo")
    try:
        d = json.loads(body.decode("utf8", "replace"))
        out += [f"- MusicCast interface: yes (api_version {d.get('api_version', '?')}, system_version {d.get('system_version', '?')})"]
    except Exception:
        out += [f"- MusicCast interface: not available (HTTP {st3})"]
    return scrub("\n".join(out), ip), None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only sanitized compatibility report for MoBetter")
    ap.add_argument("--receiver", help="receiver address (default: the one saved in config.json)")
    a = ap.parse_args(argv)
    ip = a.receiver
    if not ip:
        try:
            ip = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf8"))["receiver"]
        except Exception:
            sys.exit("Give your receiver's address:   python tools/diagnose.py --receiver 192.168.0.50")
    print("Reading your receiver (read-only, about a minute)...")
    body, err = build_report(ip)
    if err:
        sys.exit(err)
    report = f"MoBetter compatibility report ({datetime.date.today().isoformat()})\n\n{body}\n"
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "diagnose-report.md"), "w", encoding="utf8") as f:
        f.write(report)
    print("\n" + report + "\nSaved to data/diagnose-report.md. Read it, then paste it into the Compatibility report form.")


if __name__ == "__main__":
    main()
