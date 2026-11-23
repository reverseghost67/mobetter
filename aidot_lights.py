"""AiDot / Linkind Wi-Fi bulbs (Leedarson-made), controlled on the home network with the python-aidot library.

One-time setup: sign in once with aidot_login (see README) to fetch each bulb's own local key into a devices file.
After that nothing goes to the cloud: bulbs are found by UDP broadcast and driven over TCP port 10000 on the LAN.

config.json:
    "aidot": {"devices_file": "data/aidot-devices.json",
              "groups": [{"name": "Basement", "macs": ["1c:d6:bd:xx:xx:01", ...]},
                         {"name": "Lamp", "macs": ["1c:d6:bd:xx:xx:02"]}]}
Only bulbs listed in a group are ever touched.
Library: pip install python-aidot
"""
import asyncio, json, os, re, threading, time

MAC_OK = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
K_MIN, K_MAX = 2700, 6500  # Linkind RGBW BR30 white range


class Aidot:
    def __init__(self, cfg, root):
        from aidot.device_client import DeviceClient  # noqa: F401 (fail early if the library is missing)
        path = cfg.get("devices_file") or "data/aidot-devices.json"
        path = path if os.path.isabs(path) else os.path.join(root, path)
        data = json.load(open(path, encoding="utf8"))
        self.user_id = data["user_id"]
        self.devs = {d["mac"].lower(): d for d in data["devices"] if MAC_OK.match(str(d.get("mac", "")).lower())}
        self.groups = []
        for g in cfg.get("groups") or []:
            macs = [m.lower() for m in g.get("macs") or [] if m.lower() in self.devs]
            if macs and str(g.get("name", "")).strip():
                self.groups.append({"name": str(g["name"]).strip()[:30], "macs": macs})
        self.macs = sorted({m for g in self.groups for m in g["macs"]})
        self.ip = {}
        self.clients = {}
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="aidot").start()
        asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(15)

    async def _start(self):
        from aidot.device_client import DeviceClient
        from aidot.discover import Discover
        by_id = {}
        for mac in self.macs:
            d = self.devs[mac]
            self.clients[mac] = DeviceClient(d, {"id": self.user_id}, None)
            by_id[d["id"]] = mac

        def found(dev_id, ev):
            mac = by_id.get(dev_id)
            if mac:
                ip = ev.get("ipAddress")
                self.ip[mac] = ip
                self.clients[mac].update_ip_address(ip)

        self.discover = Discover({"id": self.user_id}, found)
        self.discover.start_repeat_broadcast()

    def _run(self, coro, timeout=8):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def status(self):
        out = []
        for g in self.groups:
            bulbs = []
            for mac in g["macs"]:
                c = self.clients.get(mac)
                s = c.status if c else None
                online = bool(s and s.online)
                r, gr, b, w = (s.rgbw if s else (0, 0, 0, 0))
                bulbs.append({"mac": mac, "name": self.devs[mac].get("name") or mac[-5:], "online": online,
                              "on": bool(online and s.on), "brightness": round((s.dimming if s else 0) * 100 / 255),
                              "kelvin": s.cct if s else None, "rgb": [r, gr, b], "ip": self.ip.get(mac)})
            out.append({"name": g["name"], "bulbs": bulbs})
        return out

    async def _set_one(self, mac, attr):
        c = self.clients[mac]
        if not c.status.online:
            ip = self.ip.get(mac)
            if ip:
                await c.connect(ip)
            if not c.status.online:
                return False
        await c.send_dev_attr(attr)
        return True

    def set(self, macs, power=None, brightness=None, kelvin=None, rgb=None):
        """power bool, brightness 1-100 %, kelvin K_MIN-K_MAX (white) or rgb [r,g,b] 0-255 (color). Returns the bulbs that failed."""
        attr = {}
        if power is not None:
            attr["OnOff"] = 1 if power else 0
        if brightness is not None:
            attr["Dimming"] = max(1, min(100, int(brightness)))
        if rgb is not None:
            r, g, b = (max(0, min(255, int(x))) for x in rgb)
            v = (r << 24) | (g << 16) | (b << 8)
            attr["RGBW"] = v - (1 << 32) if v >= (1 << 31) else v  # the bulb wants a signed 32-bit number
        elif kelvin is not None:
            attr["CCT"] = max(K_MIN, min(K_MAX, int(kelvin)))
        if (brightness is not None or rgb is not None or kelvin is not None) and power is None:
            attr["OnOff"] = 1  # changing the light implies on
        bad = []
        for mac in macs:
            if mac not in self.clients:
                raise ValueError("unknown bulb")
        async def go():
            res = await asyncio.gather(*(self._set_one(m, dict(attr)) for m in macs), return_exceptions=True)
            return [m for m, ok in zip(macs, res) if ok is not True]
        try:
            bad = self._run(go(), 12)
        except Exception:
            bad = list(macs)
        return bad
