"""LIFX bulbs on the home network, over LIFX's own LAN protocol (UDP 56700). Standard library only, no cloud, no app.

Only the bulbs listed in config.json are ever touched, by MAC address:
    "lifx": [{"mac": "d0:73:d5:xx:xx:01"}, {"mac": "d0:73:d5:xx:xx:02"}]
Addresses are found by broadcast and re-found when a bulb stops answering (its IP can change).
Only white light is set (power, brightness, warmth); colors are left to the LIFX app.
"""
import random, re, socket, struct, threading, time

PORT = 56700
K_MIN, K_MAX = 2500, 6500  # warmth range offered on the page; every LIFX white bulb covers it
MAC_OK = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _packet(msg_type, payload=b"", target=None, res_required=False, ack_required=False):
    tagged = target is None
    size = 36 + len(payload)
    proto = 1024 | (1 << 12) | ((1 if tagged else 0) << 13)
    frame = struct.pack("<HHI", size, proto, random.randint(2, 2 ** 31))
    tgt = (bytes.fromhex(target.replace(":", "")) + b"\0\0") if target else b"\0" * 8
    flags = (1 if res_required else 0) | ((1 if ack_required else 0) << 1)
    addr = tgt + b"\0" * 6 + struct.pack("<BB", flags, random.randint(0, 255))
    return frame + addr + struct.pack("<QHH", 0, msg_type, 0) + payload


class Lifx:
    def __init__(self, bulbs):
        self.macs = [b["mac"].lower() for b in bulbs if MAC_OK.match(str(b.get("mac", "")).lower())]
        self.ip = {}  # mac -> ip
        self.lock = threading.Lock()

    def _sock(self, timeout=0.4):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        s.settimeout(timeout)
        return s

    def discover(self):
        s = self._sock()
        try:
            for _ in range(2):
                s.sendto(_packet(2), ("255.255.255.255", PORT))
                end = time.time() + 1.0
                while time.time() < end:
                    try:
                        d, a = s.recvfrom(1024)
                    except socket.timeout:
                        continue
                    if len(d) >= 36 and struct.unpack("<H", d[32:34])[0] == 3:
                        mac = d[8:14].hex(":")
                        if mac in self.macs:
                            self.ip[mac] = a[0]
                if all(m in self.ip for m in self.macs):
                    break
        finally:
            s.close()

    def _ask(self, mac, msg_type, want, payload=b""):
        """Send one message to a bulb and wait for the reply type `want` (or an ack when want == 45)."""
        for attempt in range(2):
            ip = self.ip.get(mac)
            if not ip:
                self.discover()
                ip = self.ip.get(mac)
                if not ip:
                    return None
            s = self._sock()
            try:
                pkt = _packet(msg_type, payload, mac, res_required=(want != 45), ack_required=(want == 45))
                for _ in range(3):
                    s.sendto(pkt, (ip, PORT))
                    end = time.time() + 0.5
                    while time.time() < end:
                        try:
                            d, a = s.recvfrom(1024)
                        except socket.timeout:
                            continue
                        if len(d) >= 36 and d[8:14].hex(":") == mac and struct.unpack("<H", d[32:34])[0] == want:
                            return d
            finally:
                s.close()
            self.ip.pop(mac, None)  # no answer: maybe a new address; look again once
        return None

    def status(self):
        out = []
        with self.lock:
            for mac in self.macs:
                d = self._ask(mac, 101, 107)
                if d is None:
                    out.append({"mac": mac, "online": False, "label": mac[-5:]})
                    continue
                h, sat, b, k, _, pw = struct.unpack("<HHHHhH", d[36:48])
                label = d[48:80].rstrip(b"\0").decode("utf8", "replace") or mac[-5:]
                out.append({"mac": mac, "online": True, "label": label, "on": pw > 0,
                            "brightness": round(b * 100 / 65535), "kelvin": k, "color": sat > 0})
        return out

    def set(self, mac, power=None, brightness=None, kelvin=None, duration_ms=400):
        """Change one bulb. brightness 1-100 (%), kelvin K_MIN-K_MAX. Returns True when the bulb acknowledged."""
        if mac not in self.macs:
            raise ValueError("unknown bulb")
        # Wi-Fi bulbs drop packets, so an acknowledgement is not trusted either way: after each try the bulb's own state is
        # read back, and the command is sent again (up to 4 tries) until the state matches.
        with self.lock:
            want_b = round(max(1, min(100, int(brightness))) * 65535 / 100) if brightness is not None else None
            want_k = max(K_MIN, min(K_MAX, int(kelvin))) if kelvin is not None else None
            for _ in range(4):
                cur = self._ask(mac, 101, 107)
                if cur is None:
                    return False  # not answering at all (already retried and re-discovered inside _ask)
                h, sat, b, k, _, pw = struct.unpack("<HHHHhH", cur[36:48])
                color_ok = (want_b is None or abs(b - want_b) <= 700) and (want_k is None or abs(k - want_k) <= 60) and \
                           (sat == 0 or (want_b is None and want_k is None))
                power_ok = power is None or (pw > 0) == power
                if color_ok and power_ok:
                    return True
                if not color_ok:
                    nb = want_b if want_b is not None else b
                    nk = want_k if want_k is not None else max(K_MIN, min(K_MAX, k))
                    self._ask(mac, 102, 45, struct.pack("<BHHHHI", 0, 0, 0, nb, nk, duration_ms))
                if not power_ok:
                    self._ask(mac, 117, 45, struct.pack("<HI", 65535 if power else 0, duration_ms))
                time.sleep(duration_ms / 1000 + 0.2)
            return False


def start_schedule(bulbs_ctl, entries, audit):
    """Daily timed changes, e.g. [{"at": "18:00", "brightness": 50, "kelvin": 5000}]. Uses this PC's local clock.
    By default a timed change only adjusts bulbs that are already ON (it never switches a light on); add "power": true to change that.
    Fires once per day per entry, at or after its time (never for a time that had already passed when MoBetter started)."""
    import datetime
    jobs = []
    for e in entries or []:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(e.get("at", "")))
        if m:
            jobs.append({"h": int(m[1]), "m": int(m[2]), "e": e, "done": None})
    if not jobs:
        return
    today = datetime.date.today()
    now = datetime.datetime.now()
    for j in jobs:
        if (now.hour, now.minute) >= (j["h"], j["m"]):
            j["done"] = today  # already past today: wait for tomorrow

    def loop():
        while True:
            time.sleep(20)
            now = datetime.datetime.now()
            for j in jobs:
                if j["done"] == now.date() or (now.hour, now.minute) < (j["h"], j["m"]):
                    continue
                j["done"] = now.date()
                e = j["e"]
                try:
                    st = {b["mac"]: b for b in bulbs_ctl.status()}
                    for mac in bulbs_ctl.macs:
                        b = st.get(mac)
                        if not b or not b.get("online"):
                            continue
                        if not b.get("on") and not e.get("power"):
                            continue
                        ok = bulbs_ctl.set(mac, True if e.get("power") else None, e.get("brightness"), e.get("kelvin"), 2000)
                        audit(f"schedule  LIFX {mac[-5:]} at {e['at']} bright={e.get('brightness')} kelvin={e.get('kelvin')}" + ("" if ok else " NO ANSWER"))
                except Exception as ex:
                    audit(f"schedule  LIFX {e.get('at')} FAILED: {type(ex).__name__}: {ex}"[:300])

    threading.Thread(target=loop, daemon=True, name="lifx-schedule").start()
