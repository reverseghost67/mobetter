"""READ-ONLY. Saves a backup of your receiver's current settings to data/backup-<date>.json (never published, never committed).
It asks the receiver for each group of settings the same way its own Web Setup page does when you press [R].
    python tools/backup_settings.py            (uses the address saved in config.json)
"""
import datetime, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf8"))
schema = json.load(open(os.path.join(ROOT, "data", "schema.json"), encoding="utf8"))
IP = cfg["receiver"]


def read_group(path):
    inner = "GetParam"
    for name in reversed(path):
        inner = f"<{name}>{inner}</{name}>"
    req = urllib.request.Request(f"http://{IP}/YamahaRemoteControl/ctrl", data=f'<YAMAHA_AV cmd="GET">{inner}</YAMAHA_AV>'.encode(), headers={"Content-Type": "text/xml"})
    return urllib.request.urlopen(req, timeout=10).read().decode("utf8", "replace")


targets, seen = [], []
for n in schema:
    if not (n["group_get"] or (n["leaf"] and n["cat"])):
        continue
    p = tuple(n["path"])
    if any(p[: len(q)] == q for q in seen):
        continue
    seen.append(p)
    targets.append(p)
out, ok = [], 0
for p in targets:
    try:
        xml = read_group(list(p))
        rc = int(xml.split('RC="')[1].split('"')[0]) if 'RC="' in xml else -1
    except Exception as e:  # write-only action nodes answer HTTP 500 to a read: expected
        xml, rc = str(e)[:100], -9
    ok += rc == 0
    out.append({"path": list(p), "rc": rc, "xml": xml})
    time.sleep(0.12)
name = os.path.join(ROOT, "data", f"backup-{datetime.date.today().isoformat()}.json")
json.dump({"taken": datetime.datetime.now().isoformat(timespec="seconds"), "groups": out}, open(name, "w", encoding="utf8"), ensure_ascii=False)
print(f"saved {name}: {ok} of {len(targets)} groups readable (the rest are unsupported on this model or action-only)")
