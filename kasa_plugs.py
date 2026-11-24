"""TP-Link Kasa smart plugs, controlled on the home network with the python-kasa library (no cloud, no account for
older plugs such as the HS103).

config.json:
    "kasa": {"plugs": [{"name": "Office heater", "host": "192.168.0.166", "mac": "5C:A6:E6:5A:2C:5C",
                        "auto_off_minutes": 180}]}
Only plugs listed here are ever touched. "mac" is optional but recommended: if the plug's address changes (DHCP),
MoBetter finds it again by MAC with a broadcast.

auto_off_minutes: a safety timer, meant for heaters. MoBetter turns the plug off once it has been ON for that long,
however it was switched on (MoBetter, the Kasa app, or the button on the plug). It counts from the moment MoBetter
first sees it on, so after a MoBetter restart the full time starts again. Nothing here ever switches a plug ON by itself.
Library: pip install python-kasa
"""
import asyncio, re, threading, time

MAC_OK = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
POLL_SECONDS = 20


class Kasa:
    def __init__(self, cfg, audit=None):
        import kasa  # noqa: F401 (fail early if the library is missing)
        self.audit = audit or (lambda m: None)
        self.plugs = []
        for i, p in enumerate(cfg.get("plugs") or []):
            host, name = str(p.get("host", "")).strip(), str(p.get("name", "")).strip()[:40]
            if not host or not name:
                continue
            mac = str(p.get("mac", "")).upper()
            self.plugs.append({"id": str(i), "name": name, "host": host, "mac": mac if MAC_OK.match(mac) else "",
                               "auto_off": max(0, int(p.get("auto_off_minutes") or 0)),
                               "dev": None, "online": False, "on": False, "on_since": None, "model": "", "seen": 0})
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="kasa").start()
        threading.Thread(target=self._watch, daemon=True, name="kasa-watch").start()

    def _run(self, coro, timeout=10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _connect(self, p):
        from kasa import Discover
        try:
            dev = await Discover.discover_single(p["host"], timeout=4)
            await dev.update()
            if p["mac"] and dev.mac.upper() != p["mac"]:  # someone else now has that address
                raise OSError("different device at that address")
            p["dev"] = dev
            return dev
        except Exception:
            if not p["mac"]:
                raise
        found = await Discover.discover(timeout=6)  # find it again by MAC
        for ip, dev in found.items():
            if dev.mac.upper() == p["mac"]:
                await dev.update()
                p["host"], p["dev"] = ip, dev
                return dev
        raise OSError("plug not found on the network")

    async def _refresh(self, p):
        try:
            dev = p["dev"] or await self._connect(p)
            await dev.update()
        except Exception:
            p["dev"] = None
            try:
                dev = await self._connect(p)
            except Exception:
                p["online"] = False
                return
        was_on = p["on"]
        p.update(online=True, on=bool(dev.is_on), model=dev.model, seen=time.time())
        if p["on"] and (not was_on or p["on_since"] is None):
            p["on_since"] = time.time()
        if not p["on"]:
            p["on_since"] = None

    def _watch(self):
        while True:
            for p in self.plugs:
                try:
                    self._run(self._refresh(p), 20)
                    if p["auto_off"] and p["on"] and p["on_since"] and time.time() - p["on_since"] >= p["auto_off"] * 60:
                        ok = self._switch(p, False)
                        self.audit(f"auto-off  KASA {p['name']} off after {p['auto_off']} min" + ("" if ok else " (NO ANSWER, will retry)"))
                except Exception:
                    pass
            time.sleep(POLL_SECONDS)

    def _switch(self, p, power):
        async def go():
            dev = p["dev"] or await self._connect(p)
            await (dev.turn_on() if power else dev.turn_off())
            await dev.update()
            return bool(dev.is_on) == power
        try:
            with self.lock:
                ok = self._run(go(), 15)
        except Exception:
            p["dev"] = None
            return False
        p["on"] = power
        p["on_since"] = time.time() if power else None
        return ok

    def status(self):
        now = time.time()
        out = []
        for p in self.plugs:
            left = None
            if p["auto_off"] and p["on"] and p["on_since"]:
                left = max(0, round((p["auto_off"] * 60 - (now - p["on_since"])) / 60))
            out.append({"id": p["id"], "name": p["name"], "online": p["online"], "on": p["on"], "model": p["model"],
                        "auto_off_minutes": p["auto_off"], "minutes_left": left})
        return out

    def set(self, plug_id, power):
        p = next((x for x in self.plugs if x["id"] == plug_id), None)
        if p is None:
            raise ValueError("unknown plug")
        return self._switch(p, bool(power))
