"""MoBetter: a modern browser front end for Yamaha AV receivers. Standard library only.

    python server.py --receiver 192.168.0.50          # first run (remembers the address in config.json)
    python server.py                                   # later runs
    python server.py --lan                             # also reachable from phones and tablets on your home network

It serves the web page (./web) and forwards the page's commands to your receiver (a browser cannot call a receiver directly).
Receivers have NO password, so this server is the only gate:
  * only private-network clients are served (192.168.x.x, 10.x.x.x, 172.16-31.x.x, localhost); the Host name must be an IP, localhost or this PC's name
  * only the receiver's two control paths are forwarded; the MusicCast interface only for read endpoints ("get...")
  * every change must come from the page itself (header X-RX-UI) so another web site cannot fire commands at it
  * risky commands (restart, firmware update, initialize, factory preset) need a confirmation header the page sends only after you confirm
  * every change is appended to data/audit.log
Never expose this server to the internet.
"""
import argparse, datetime, ipaddress, json, os, re, socket, sys, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DATA = os.path.join(ROOT, "data")
CFG_PATH = os.path.join(ROOT, "config.json")
PORT = 8780  # fixed on purpose: the start scripts open this address, and the port is never saved in config.json
CFG = {"receiver": None, "model": "", "firmware": ""}
GTV = None  # Google TV remote (gtv.py), only when a Google TV address is configured
GTV_SCREEN = None  # Google TV screen view (gtv.GTVScreen), same address
GLV = False  # GlitterVis visualizer on this PC (control port 127.0.0.1:8795), only when "glittervis": true in config.json
GLV_CMD = re.compile(r"(start|stop|next|prev|lock|calm|title|fullscreen|window|size/(small|medium|large)|scene/[a-z]{1,12})")
GAMES = False  # GlitterMame games menu on this PC (scheduled task "GlitterMame"), only when "games": true in config.json
GAMES_PROCS = ("attractplus.exe", "mame.exe", "retroarch.exe")  # the only processes "Close" may end
HDMI_WATCH = None  # hdmi_watch.HdmiWatch, only when "hdmi_watch" is set in config.json (Windows)
AIDOT = None  # AiDot/Linkind Wi-Fi bulbs (aidot_lights.py), only when "aidot" is set in config.json
KASA = None  # TP-Link Kasa smart plugs (kasa_plugs.py), only when "kasa" is set in config.json
LIFX = None  # LIFX bulbs on the LAN (lifx.py), only when "lifx" lists bulbs in config.json
WAX = None  # Parks Audio Waxwing over Bluetooth LE (waxwing.py), only when "waxwing": true in config.json and bleak is installed


def pick_port(cli_port=None):
    """The port is always 8780 unless --port is given for this one run. It is never read from or written to config.json."""
    return cli_port or PORT
NAME_OK = re.compile(r"[A-Za-z0-9 .\-/&'+()]{1,15}")
RISKY = re.compile(r"<Service>\s*<Control>|<Update>|Initialize|Factory|<Restart>|<Reset>|Network_Reboot", re.I)
HOST_OK = re.compile(r"^(localhost|\[::1\]|\d{1,3}(\.\d{1,3}){3})(:\d+)?$", re.I)
MY_NAMES = {socket.gethostname().lower(), socket.gethostname().lower() + ".local"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def audit(line):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "audit.log"), "a", encoding="utf8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {line}\n")


def receiver_call(method, path, data=None, timeout=8, ctype="text/xml"):
    req = urllib.request.Request(f"http://{CFG['receiver']}{path}", data=data, method=method, headers={"Content-Type": ctype} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "text/plain")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), "text/plain"
    except Exception as e:
        return 502, f"receiver not reachable: {e}".encode(), "text/plain"


def peer_links():
    """Buttons to other MoBetter pages (one copy per receiver), from config.json "peers": [{"name": "Office", "port": 8781}].
    Only a name and a port (same host) or a plain http(s) URL; anything else is dropped."""
    out = []
    for e in CFG.get("peers") or []:
        if not isinstance(e, dict) or not str(e.get("name", "")).strip():
            continue
        name = str(e["name"]).strip()[:20]
        if isinstance(e.get("port"), int) and 1 <= e["port"] <= 65535:
            out.append({"name": name, "port": e["port"]})
        elif isinstance(e.get("url"), str) and e["url"].startswith(("http://", "https://")):
            out.append({"name": name, "url": e["url"]})
    return out


def identify_receiver():
    """Ask the receiver who it is (read-only). Returns (model, firmware) or raises."""
    st, body, _ = receiver_call("POST", "/YamahaRemoteControl/ctrl", b'<YAMAHA_AV cmd="GET"><System><Config>GetParam</Config></System></YAMAHA_AV>')
    text = body.decode("utf8", "replace")
    if st != 200 or "<Model_Name>" not in text:
        raise RuntimeError(f"{CFG['receiver']} did not answer like a Yamaha receiver (HTTP {st})")
    pick = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", text) or [None, ""])[1]
    return pick("Model_Name"), pick("Version")


def games_running():
    """True when any GlitterMame program is running (Windows tasklist)."""
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout.lower()
    except Exception:
        return False
    return any(('"' + n + '"') in out for n in GAMES_PROCS)


class Handler(BaseHTTPRequestHandler):
    server_version = "mobetter"

    def log_message(self, *a):
        pass

    def host_ok(self):
        # defence against DNS rebinding: a web page on the internet must not reach this server through a name it controls
        h = (self.headers.get("Host") or "").lower()
        return bool(HOST_OK.match(h)) or h.split(":")[0] in MY_NAMES

    def allowed(self):
        if not self.host_ok():
            return False
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            return ip.is_private or ip.is_loopback
        except ValueError:
            return False

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            return self.send(403, b"private network only")
        p = self.path.split("?")[0]
        if p.startswith("/api/yxc/"):
            if not re.search(r"/get[A-Za-z]*$", p):  # setters are plain GETs too: refuse everything that is not a read
                return self.send(405, b"read-only endpoints only")
            q = ("?" + self.path.split("?", 1)[1]) if "?" in self.path else ""
            st, body, ct = receiver_call("GET", "/YamahaExtendedControl/v1/" + p[len("/api/yxc/"):] + q)
            return self.send(st, body, ct)
        if p == "/api/config":
            return self.send(200, json.dumps({**{k: CFG[k] for k in ("receiver", "model", "firmware")}, "peers": peer_links()}).encode(), "application/json")
        if p == "/api/scenes":
            # Your own one-tap scenes (the Listen card): scenes.json next to server.py, or the example file if you have none yet.
            for name in ("scenes.json", "scenes.example.json"):
                f = os.path.join(ROOT, name)
                if os.path.exists(f):
                    try:
                        data = json.loads(read_bytes(f).decode("utf-8-sig"))
                    except ValueError:
                        return self.send(500, b"scenes file is not valid JSON")
                    return self.send(200, json.dumps({"file": name, "scenes": data.get("scenes", [])}).encode(), "application/json")
            return self.send(200, b'{"file": null, "scenes": []}', "application/json")
        if p == "/api/schema":
            f = os.path.join(DATA, "schema.json")
            return self.send(200, read_bytes(f), "application/json") if os.path.exists(f) else self.send(200, b"[]", "application/json")
        if p == "/api/gtv/screen":
            if GTV_SCREEN is None:
                return self.send(404, b"screen view not set up")
            png = GTV_SCREEN.screenshot()
            if not png:
                return self.send(503, b"no screenshot (is wireless debugging on and paired?)")
            return self.send(200, png, "image/png")
        if p == "/api/waxwing/status":
            if WAX is None:
                return self.send(200, json.dumps({"configured": False}).encode(), "application/json")
            import waxwing
            meta = [{"fn": f, "key": k, "label": l, "min": lo, "max": hi, "labels": lb} for f, (k, l, lo, hi, lb) in waxwing.SETTINGS.items()]
            return self.send(200, json.dumps({"configured": True, "settings": meta, **WAX.get_status()}).encode(), "application/json")
        if p == "/api/aidot/status":
            if AIDOT is None:
                return self.send(200, b'{"configured": false}', "application/json")
            import aidot_lights
            return self.send(200, json.dumps({"configured": True, "kmin": aidot_lights.K_MIN, "kmax": aidot_lights.K_MAX, "groups": AIDOT.status()}).encode(), "application/json")
        if p == "/api/kasa/status":
            if KASA is None:
                return self.send(200, b'{"configured": false}', "application/json")
            return self.send(200, json.dumps({"configured": True, "plugs": KASA.status()}).encode(), "application/json")
        if p == "/api/lifx/status":
            if LIFX is None:
                return self.send(200, b'{"configured": false}', "application/json")
            import lifx
            return self.send(200, json.dumps({"configured": True, "kmin": lifx.K_MIN, "kmax": lifx.K_MAX, "bulbs": LIFX.status()}).encode(), "application/json")
        if p == "/api/games/status":
            if not GAMES:
                return self.send(200, b'{"configured": false}', "application/json")
            return self.send(200, json.dumps({"configured": True, "running": games_running()}).encode(), "application/json")
        if p == "/api/glittervis/status":
            if not GLV:
                return self.send(200, b'{"configured": false}', "application/json")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8795/status", timeout=1.5) as r:
                    st = json.loads(r.read().decode("utf8"))
                return self.send(200, json.dumps({"configured": True, "running": True, **st}).encode(), "application/json")
            except Exception:
                return self.send(200, b'{"configured": true, "running": false}', "application/json")
        if p == "/api/hdmiwatch":
            if HDMI_WATCH is None:
                return self.send(200, b'{"configured": false}', "application/json")
            return self.send(200, json.dumps({"configured": True, "input": HDMI_WATCH.input, "state": HDMI_WATCH.last,
                                              "pc_peak": round(HDMI_WATCH.peak, 3)}).encode(), "application/json")
        if p == "/api/gtv/status":
            if GTV is None:
                return self.send(200, json.dumps({"configured": False}).encode(), "application/json")
            return self.send(200, json.dumps({"configured": True, **GTV.status()}).encode(), "application/json")
        if p == "/api/audit":
            f = os.path.join(DATA, "audit.log")
            return self.send(200, (read_bytes(f).decode("utf8", "replace")[-6000:] if os.path.exists(f) else "").encode())
        if p == "/":
            p = "/index.html"
        fp = os.path.normpath(os.path.join(WEB, p.lstrip("/")))
        if not fp.startswith(WEB) or not os.path.isfile(fp):
            return self.send(404, b"not found")
        self.send(200, read_bytes(fp), TYPES.get(os.path.splitext(fp)[1], "application/octet-stream"))

    def rename_input(self):
        # Rename one input (the name the receiver shows on its display and in apps). Narrow on purpose: only an input id the receiver
        # itself lists, only a short plain name, and every change goes into the audit log.
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(min(n, 2000)).decode("utf8"))
            iid, text = str(req.get("id", "")), str(req.get("text", "")).strip()
        except (ValueError, AttributeError):
            return self.send(400, b"bad request")
        if not NAME_OK.fullmatch(text):
            return self.send(400, b"name must be 1-15 plain letters, digits, spaces or - . / & ' + ( )")
        st, body, _ = receiver_call("GET", "/YamahaExtendedControl/v1/system/getNameText")
        try:
            ids = {x.get("id") for x in json.loads(body.decode("utf8")).get("input_list", [])}
        except (ValueError, AttributeError):
            ids = set()
        if iid not in ids:
            return self.send(400, b"unknown input")
        audit(f"{self.client_address[0]}  RENAME  {iid} -> {text}")
        st, out, ct = receiver_call("POST", "/YamahaExtendedControl/v1/system/setNameText",
                                    json.dumps({"id": iid, "text": text}).encode("utf8"), ctype="application/json")
        return self.send(st, out, "application/json")

    def gtv_command(self):
        # Google TV remote: only the buttons and apps listed in gtv.py, plus the one-time pairing. Each press is audited.
        if GTV is None:
            return self.send(404, b"no Google TV configured (start with --gtv <address>)")
        import gtv
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(min(n, 2000)).decode("utf8") or "{}")
        except ValueError:
            return self.send(400, b"bad request")
        what = self.path[len("/api/gtv/"):]
        try:
            if what == "key" and req.get("key") in gtv.KEYS:
                GTV.key(req["key"])
            elif what == "app" and req.get("app") in gtv.APPS:
                GTV.app(req["app"])
            elif what == "pair/start":
                audit(f"{self.client_address[0]}  GTV pairing started")
                GTV.start_pairing()
            elif what == "screen/pair" and GTV_SCREEN is not None and re.fullmatch(r"\d{6}", str(req.get("code", ""))) and re.fullmatch(r"\d{2,5}", str(req.get("port", ""))):
                ok, msg = GTV_SCREEN.pair(req["port"], req["code"])
                audit(f"{self.client_address[0]}  GTV screen pairing: {'ok' if ok else 'failed'}")
                if not ok:
                    return self.send(502, ("pairing failed: " + msg).encode()[:300])
            elif what == "screen/off" and GTV_SCREEN is not None:
                GTV_SCREEN.disconnect()
            elif what == "pair/finish" and re.fullmatch(r"[0-9A-Fa-f]{6}", str(req.get("code", ""))):
                GTV.finish_pairing(req["code"])
                audit(f"{self.client_address[0]}  GTV paired")
            else:
                return self.send(400, b"unknown command")
        except Exception as e:
            audit(f"{self.client_address[0]}  GTV {what} FAILED: {type(e).__name__}: {e}"[:300])
            return self.send(502, f"Google TV: {type(e).__name__}: {e}".encode()[:300])
        if what in ("key", "app"):
            audit(f"{self.client_address[0]}  GTV {what} {req.get(what)}")
        return self.send(200, b'{"ok":true}', "application/json")

    def waxwing_set(self):
        # Waxwing: only the settings listed in waxwing.SETTINGS, within their ranges. Each change is audited.
        if WAX is None:
            return self.send(404, b"no Waxwing configured")
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(min(n, 500)).decode("utf8") or "{}")
            fn, value = int(req.get("fn")), int(req.get("value"))
        except (ValueError, TypeError):
            return self.send(400, b"bad request")
        try:
            WAX.set(fn, value)
        except Exception as e:
            audit(f"{self.client_address[0]}  WAXWING set {fn}={value} FAILED: {type(e).__name__}: {e}"[:300])
            return self.send(502, f"Waxwing: {e}".encode()[:300])
        audit(f"{self.client_address[0]}  WAXWING set {fn}={value}")
        return self.send(200, b'{"ok":true}', "application/json")

    def glittervis_command(self):
        # GlitterVis: only the commands in GLV_CMD. "start" runs the GlitterVis scheduled task, which opens it on the TV's
        # desktop session (this server runs in a background session and cannot show a window itself). Each press is audited.
        if not GLV:
            return self.send(404, b"GlitterVis not configured")
        what = self.path[len("/api/glittervis/"):]
        if not GLV_CMD.fullmatch(what):
            return self.send(400, b"unknown command")
        audit(f"{self.client_address[0]}  GLITTERVIS {what}")
        try:
            if what == "start":
                import subprocess
                r = subprocess.run(["schtasks", "/run", "/tn", "GlitterVis"], capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    return self.send(502, ("could not start: " + (r.stderr or r.stdout)).encode()[:300])
            else:
                urllib.request.urlopen("http://127.0.0.1:8795/cmd/" + ("quit" if what == "stop" else what), timeout=2).read()
        except Exception as e:
            return self.send(502, f"GlitterVis: {e}".encode()[:300])
        return self.send(200, b'{"ok":true}', "application/json")

    def games_command(self):
        # GlitterMame: "start" runs the GlitterMame scheduled task (opens the games menu on the TV's desktop session);
        # "stop" ends only the three game programs in GAMES_PROCS. Each press is audited.
        if not GAMES:
            return self.send(404, b"Games not configured")
        what = self.path[len("/api/games/"):]
        if what not in ("start", "stop"):
            return self.send(400, b"unknown command")
        audit(f"{self.client_address[0]}  GAMES {what}")
        import subprocess
        try:
            if what == "start":
                r = subprocess.run(["schtasks", "/run", "/tn", "GlitterMame"], capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    return self.send(502, ("could not start: " + (r.stderr or r.stdout)).encode()[:300])
            else:
                for name in GAMES_PROCS:
                    subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, text=True, timeout=15)
        except Exception as e:
            return self.send(502, f"Games: {e}".encode()[:300])
        return self.send(200, b'{"ok":true}', "application/json")

    def aidot_set(self):
        # AiDot/Linkind: only bulbs listed in config.json groups; power, brightness 1-100, warmth or an RGB color. Each change is audited.
        if AIDOT is None:
            return self.send(404, b"no AiDot bulbs configured")
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(min(n, 4000)).decode("utf8") or "{}")
            macs = [str(m).lower() for m in (req.get("macs") or [])]
            if not macs or len(macs) > 50:
                raise ValueError
            power = None if req.get("power") is None else bool(req["power"])
            bright = None if req.get("brightness") is None else int(req["brightness"])
            kelvin = None if req.get("kelvin") is None else int(req["kelvin"])
            rgb = req.get("rgb")
            if rgb is not None:
                rgb = [int(x) for x in rgb]
                if len(rgb) != 3:
                    raise ValueError
        except (ValueError, TypeError, AttributeError):
            return self.send(400, b"bad request")
        if power is None and bright is None and kelvin is None and rgb is None:
            return self.send(400, b"nothing to change")
        try:
            bad = AIDOT.set(macs, power, bright, kelvin, rgb)
        except ValueError:
            return self.send(400, b"unknown bulb")
        audit(f"{self.client_address[0]}  AIDOT {len(macs)} bulb(s) power={power} bright={bright} kelvin={kelvin} rgb={rgb}" + (f" NO ANSWER {[m[-5:] for m in bad]}" if bad else ""))
        if bad and len(bad) == len(macs):
            return self.send(502, b"no answer from the bulbs")
        return self.send(200, json.dumps({"ok": True, "failed": bad}).encode(), "application/json")

    def kasa_set(self):
        # Kasa plugs: only plugs listed in config.json; on/off only. Each change is audited.
        if KASA is None:
            return self.send(404, b"no Kasa plugs configured")
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(min(n, 1000)).decode("utf8") or "{}")
            plug, power = str(req["id"]), req["power"]
            if not isinstance(power, bool):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            return self.send(400, b"bad request")
        try:
            ok = KASA.set(plug, power)
        except ValueError:
            return self.send(400, b"unknown plug")
        audit(f"{self.client_address[0]}  KASA plug {plug} power={power}" + ("" if ok else " NO ANSWER"))
        if not ok:
            return self.send(502, b"no answer from the plug")
        return self.send(200, b'{"ok":true}', "application/json")

    def lifx_set(self):
        # LIFX: only bulbs listed in config.json (by MAC); only power, brightness 1-100 and warmth. Each change is audited.
        if LIFX is None:
            return self.send(404, b"no LIFX bulbs configured")
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(min(n, 2000)).decode("utf8") or "{}")
            macs = req.get("macs") or []
            if not isinstance(macs, list) or not macs or not all(isinstance(m, str) for m in macs):
                raise ValueError
            power = None if req.get("power") is None else bool(req["power"])
            bright = None if req.get("brightness") is None else int(req["brightness"])
            kelvin = None if req.get("kelvin") is None else int(req["kelvin"])
        except (ValueError, TypeError, AttributeError):
            return self.send(400, b"bad request")
        if power is None and bright is None and kelvin is None:
            return self.send(400, b"nothing to change")
        bad = []
        for m in macs:
            try:
                if not LIFX.set(m.lower(), power, bright, kelvin):
                    bad.append(m)
            except ValueError:
                return self.send(400, b"unknown bulb")
        audit(f"{self.client_address[0]}  LIFX {','.join(m[-5:] for m in macs)} power={power} bright={bright} kelvin={kelvin}" + (f" NO ANSWER {bad}" if bad else ""))
        if bad:
            return self.send(502, ("no answer from " + ", ".join(bad)).encode())
        return self.send(200, b'{"ok":true}', "application/json")

    def do_POST(self):
        # Read the whole request body first (up to 20 kB), even when the request is about to be refused: answering before the
        # body is read makes Windows abort the connection. Handlers below read it from this buffer as before.
        import io
        n = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile = io.BytesIO(self.rfile.read(min(n, 20000)) if n > 0 else b"")
        if not self.allowed():
            return self.send(403, b"private network only")
        if self.path not in ("/api/xml", "/api/rename", "/api/waxwing/set", "/api/lifx/set", "/api/aidot/set", "/api/kasa/set") and not self.path.startswith(("/api/gtv/", "/api/glittervis/", "/api/games/")):
            return self.send(404, b"not found")
        if self.headers.get("X-RX-UI") != "1":
            return self.send(403, b"missing page header")
        if self.path == "/api/rename":
            return self.rename_input()
        if self.path == "/api/waxwing/set":
            return self.waxwing_set()
        if self.path == "/api/aidot/set":
            return self.aidot_set()
        if self.path == "/api/kasa/set":
            return self.kasa_set()
        if self.path == "/api/lifx/set":
            return self.lifx_set()
        if self.path.startswith("/api/gtv/"):
            return self.gtv_command()
        if self.path.startswith("/api/glittervis/"):
            return self.glittervis_command()
        if self.path.startswith("/api/games/"):
            return self.games_command()
        n = int(self.headers.get("Content-Length", 0))
        text = self.rfile.read(min(n, 20000)).decode("utf8", "replace")
        if 'cmd="PUT"' in text.replace("'", '"'):
            if RISKY.search(text) and self.headers.get("X-Confirm") != "yes":
                audit(f"{self.client_address[0]}  REFUSED (needs confirmation)  {text[:300]}")
                return self.send(409, b"risky command needs confirmation")
            audit(f"{self.client_address[0]}  PUT  {text[:400]}")
        st, out, _ = receiver_call("POST", "/YamahaRemoteControl/ctrl", text.encode("utf8"))
        self.send(st, out, "text/xml; charset=utf-8")


def main(argv=None):
    # Under the supervisor, output goes to data\server.log: write each line straight away, so a crash log has the last words.
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    import threading, traceback
    threading.excepthook = lambda a: print("thread crashed: " + "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)), flush=True)
    ap = argparse.ArgumentParser(description="MoBetter: a modern web front end for Yamaha AV receivers")
    ap.add_argument("--receiver", help="IP address of your receiver (remembered in config.json)")
    ap.add_argument("--port", type=int, help="port to serve on (default 8780)")
    ap.add_argument("--lan", action="store_true", help="also serve phones and tablets on your home network")
    ap.add_argument("--gtv", help="IP address of a Google TV / Android TV box to add a remote for (remembered in config.json)")
    ap.add_argument("--rebuild-schema", action="store_true", help="download the settings list from the receiver again")
    a = ap.parse_args(argv)
    try:
        CFG.update(json.load(open(CFG_PATH, encoding="utf8")))
    except FileNotFoundError:
        pass
    CFG.pop("port", None)  # older config files saved a port; ignore it so the address never drifts
    port = pick_port(a.port)
    if a.receiver:
        CFG["receiver"] = a.receiver
    if not CFG["receiver"]:
        sys.exit("Tell MoBetter your receiver's address the first time:   python server.py --receiver 192.168.0.50")
    try:
        CFG["model"], CFG["firmware"] = identify_receiver()
    except Exception as e:
        sys.exit(f"Could not reach the receiver: {e}")
    json.dump(CFG, open(CFG_PATH, "w", encoding="utf8"), indent=1)
    print(f"Found {CFG['model']} (firmware {CFG['firmware']}) at {CFG['receiver']}")
    schema_path = os.path.join(DATA, "schema.json")
    if a.rebuild_schema or not os.path.exists(schema_path):
        try:
            import schema_builder
            print("Reading the receiver's own settings list (first run only)...")
            print(f"  {schema_builder.build_for_receiver(CFG['receiver'], schema_path)} entries saved locally")
        except Exception as e:
            print(f"  Could not build the settings list ({e}). The Home page will work; the Setup tab will be empty.")
    if a.gtv:
        CFG["gtv"] = a.gtv
        json.dump(CFG, open(CFG_PATH, "w", encoding="utf8"), indent=1)
    if CFG.get("gtv"):
        global GTV
        import gtv
        GTV = gtv.GoogleTV(CFG["gtv"], DATA)
        global GTV_SCREEN
        if CFG.get("gtv_screen", True):
            GTV_SCREEN = gtv.GTVScreen(CFG["gtv"])
        print(f"Google TV remote: {CFG['gtv']}" + ("" if gtv.AVAILABLE else "  (library missing: pip install androidtvremote2)"))
    if CFG.get("waxwing_bridge"):
        global WAX
        import waxwing
        WAX = waxwing.BridgeWaxwing(CFG["waxwing_bridge"])
        print(f"Waxwing panel: on (through the bridge at {CFG['waxwing_bridge']})")
    elif CFG.get("waxwing"):
        try:
            import waxwing, bleak  # noqa: F401
            WAX = waxwing.Waxwing(CFG.get("waxwing_address"))
            print("Waxwing panel: on (Bluetooth LE)")
        except ImportError:
            print("Waxwing panel: off (library missing: pip install bleak)")
    if CFG.get("hdmi_watch") and sys.platform == "win32":
        global HDMI_WATCH
        import hdmi_watch
        HDMI_WATCH = hdmi_watch.HdmiWatch(CFG["hdmi_watch"], receiver_call, audit)
        HDMI_WATCH.start()
        print(f"HDMI audio watchdog: on (input {HDMI_WATCH.input})")
    if CFG.get("aidot"):
        global AIDOT
        try:
            import aidot_lights
            AIDOT = aidot_lights.Aidot(CFG["aidot"], ROOT)
            print(f"AiDot lights panel: on ({len(AIDOT.macs)} bulbs in {len(AIDOT.groups)} group(s))")
        except ImportError:
            print("AiDot lights panel: off (library missing: pip install python-aidot)")
        except Exception as e:
            print(f"AiDot lights panel: off ({type(e).__name__}: {e})")
    if CFG.get("kasa"):
        global KASA
        try:
            import kasa_plugs
            KASA = kasa_plugs.Kasa(CFG["kasa"], audit)
            print(f"Plugs panel: on ({len(KASA.plugs)} Kasa plug(s))")
        except ImportError:
            print("Plugs panel: off (library missing: pip install python-kasa)")
    if CFG.get("lifx"):
        global LIFX
        import lifx
        LIFX = lifx.Lifx(CFG["lifx"])
        print(f"Lights panel: on ({len(LIFX.macs)} LIFX bulbs)")
        if CFG.get("lifx_schedule"):
            lifx.start_schedule(LIFX, CFG["lifx_schedule"], audit)
            print("  timed changes: " + ", ".join(str(e.get("at")) for e in CFG["lifx_schedule"]))
    if CFG.get("games"):
        global GAMES
        GAMES = True
        print("Games panel: on")
    if CFG.get("glittervis"):
        global GLV
        GLV = True
        print("GlitterVis panel: on")
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        sys.exit(f"Port {port} is already in use. MoBetter may already be running: open http://localhost:{port}\n"
                 f"(or close the other MoBetter window and start again).")
    where = "localhost" if host == "127.0.0.1" else "this-PC's-address"
    print(f"MoBetter is running: http://{where}:{port}   (Ctrl+C to stop)")
    start_self_check(port)
    srv.serve_forever()


def start_self_check(port, every=60, misses=3):
    """Every minute, ask our own page for /api/config. If it fails 3 times in a row, MoBetter is running but
    frozen: exit on purpose (code 3) so supervisor.py starts a fresh copy. Off with MOBETTER_NO_SELFCHECK=1."""
    if os.environ.get("MOBETTER_NO_SELFCHECK") == "1":
        return
    import threading, time

    def private_mb():
        """This process's committed memory in MB (Windows); None elsewhere."""
        if sys.platform != "win32":
            return None
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] +                        [(n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage",
                        "PeakPagefileUsage", "PrivateUsage")]
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        try:
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = wintypes.HANDLE
            f = ctypes.windll.psapi.GetProcessMemoryInfo
            f.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            if f(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return c.PrivateUsage // (1024 * 1024)
        except Exception:  # noqa: BLE001  (never let the memory reading stop the health check)
            pass
        return None

    limit = int(os.environ.get("MOBETTER_MEMORY_LIMIT_MB", "1500"))

    def loop():
        bad = 0
        tick = 0
        time.sleep(every)
        while True:
            mb = private_mb()
            if mb is not None:
                if tick % 10 == 0:  # every ~10 minutes, so a slow leak shows up in server.log
                    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} memory: {mb} MB", flush=True)
                if mb > limit:
                    # 23 Nov 2026: a MoBetter process grew to ~61 GB and starved the whole PC. Never again.
                    print(f"self-check: memory {mb} MB is over the {limit} MB limit, exiting so the supervisor restarts MoBetter", flush=True)
                    audit(f"self-check  memory {mb} MB over limit, restarting")
                    os._exit(4)
            tick += 1
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=15) as r:
                    bad = 0 if r.status == 200 else bad + 1
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"self-check: no answer from our own page ({type(e).__name__}), {bad} of {misses}", flush=True)
            if bad >= misses:
                print(f"self-check: page frozen for {misses} minutes, exiting so the supervisor restarts MoBetter", flush=True)
                os._exit(3)
            time.sleep(every)
    threading.Thread(target=loop, daemon=True, name="self-check").start()


if __name__ == "__main__":
    main()
