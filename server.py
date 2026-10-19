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
CFG = {"receiver": None, "port": 8780, "model": "", "firmware": ""}
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


def receiver_call(method, path, data=None, timeout=8):
    req = urllib.request.Request(f"http://{CFG['receiver']}{path}", data=data, method=method, headers={"Content-Type": "text/xml"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "text/plain")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), "text/plain"
    except Exception as e:
        return 502, f"receiver not reachable: {e}".encode(), "text/plain"


def identify_receiver():
    """Ask the receiver who it is (read-only). Returns (model, firmware) or raises."""
    st, body, _ = receiver_call("POST", "/YamahaRemoteControl/ctrl", b'<YAMAHA_AV cmd="GET"><System><Config>GetParam</Config></System></YAMAHA_AV>')
    text = body.decode("utf8", "replace")
    if st != 200 or "<Model_Name>" not in text:
        raise RuntimeError(f"{CFG['receiver']} did not answer like a Yamaha receiver (HTTP {st})")
    pick = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", text) or [None, ""])[1]
    return pick("Model_Name"), pick("Version")


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
            return self.send(200, json.dumps({k: CFG[k] for k in ("receiver", "model", "firmware")}).encode(), "application/json")
        if p == "/api/schema":
            f = os.path.join(DATA, "schema.json")
            return self.send(200, read_bytes(f), "application/json") if os.path.exists(f) else self.send(200, b"[]", "application/json")
        if p == "/api/audit":
            f = os.path.join(DATA, "audit.log")
            return self.send(200, (read_bytes(f).decode("utf8", "replace")[-6000:] if os.path.exists(f) else "").encode())
        if p == "/":
            p = "/index.html"
        fp = os.path.normpath(os.path.join(WEB, p.lstrip("/")))
        if not fp.startswith(WEB) or not os.path.isfile(fp):
            return self.send(404, b"not found")
        self.send(200, read_bytes(fp), TYPES.get(os.path.splitext(fp)[1], "application/octet-stream"))

    def do_POST(self):
        if not self.allowed():
            return self.send(403, b"private network only")
        if self.path != "/api/xml":
            return self.send(404, b"not found")
        if self.headers.get("X-RX-UI") != "1":
            return self.send(403, b"missing page header")
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
    ap = argparse.ArgumentParser(description="MoBetter: a modern web front end for Yamaha AV receivers")
    ap.add_argument("--receiver", help="IP address of your receiver (remembered in config.json)")
    ap.add_argument("--port", type=int, help="port to serve on (default 8780)")
    ap.add_argument("--lan", action="store_true", help="also serve phones and tablets on your home network")
    ap.add_argument("--rebuild-schema", action="store_true", help="download the settings list from the receiver again")
    a = ap.parse_args(argv)
    try:
        CFG.update(json.load(open(CFG_PATH, encoding="utf8")))
    except FileNotFoundError:
        pass
    if a.receiver:
        CFG["receiver"] = a.receiver
    if a.port:
        CFG["port"] = a.port
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
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, int(CFG["port"])), Handler)
    where = "localhost" if host == "127.0.0.1" else "this-PC's-address"
    print(f"MoBetter is running: http://{where}:{CFG['port']}   (Ctrl+C to stop)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
