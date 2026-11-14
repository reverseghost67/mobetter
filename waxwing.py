"""Parks Audio Waxwing phono preamp over Bluetooth LE (optional). Library: bleak (pip install bleak).

Uses Parks Audio's published API (https://www.parksaudiollc.com/bluetoothapi.html): write 3 bytes [function, value, 0];
[99, 1, 0] starts a 100-byte status packet every 500 ms. Labels come from the Waxwing manual.

The Waxwing takes one Bluetooth connection at a time, so MoBetter connects only while someone has the page open (the page
asks for status every few seconds) and lets go about 45 seconds after the last request. Then the phone app can connect again.
Every setting change is checked against the list below (function and range) before it is sent."""
import asyncio, threading, time

SERVICE = "4dddd48d-cf30-492e-850b-76d57dd76900"
TX = "4dddd48d-cf30-492e-850b-76d57dd76901"
RX = "4dddd48d-cf30-492e-850b-76d57dd76902"
IDLE_SECONDS = 45

FREQS = ["5 Hz", "10 Hz", "15 Hz", "20 Hz", "25 Hz", "30 Hz", "35 Hz", "40 Hz", "45 Hz", "50 Hz", "70 Hz", "100 Hz", "200 Hz", "400 Hz",
         "700 Hz", "1 kHz", "2 kHz", "5 kHz", "7 kHz", "8 kHz", "9 kHz", "10 kHz", "11 kHz", "12 kHz", "13 kHz", "15 kHz", "20 kHz",
         "30 kHz", "48 kHz"]
EQS = ["Line in (flat)", "Phono (RIAA)", "Teldec LP", "London LP", "AES LP", "NAB LP", "Columbia LP", "CCIR 78", "Columbia 78",
       "Blm300 78", "Eur500 78", "Eur250 78", "Custom"]

# function number -> (key, label, min, max, labels or None). Only these can be changed from MoBetter.
SETTINGS = {
    18: ("output", "Output", 0, 2, ["Analog (RCA)", "Digital 24/48", "Digital 24/96"]),
    0: ("volume", "Volume", 0, 32, None),
    1: ("gain", "Gain", 0, 76, None),
    15: ("load", "Load", 0, 1, ["200 ohms", "47k ohms"]),
    9: ("mode", "Mode", 0, 4, ["Stereo", "Mono", "Super mono", "Mono left", "Mono right"]),
    19: ("eq", "EQ curve", 0, 12, EQS),
    11: ("magic", "Magic (pops and clicks)", 0, 1, ["Off", "On"]),
    13: ("derumble", "DeRumble", 0, 1, ["Off", "On"]),
    2: ("warmth", "Warmth", 0, 12, None),
    3: ("air", "Air", 0, 12, None),
    4: ("treble", "Treble", 0, 12, None),
    5: ("bass", "Bass", 0, 12, None),
    6: ("bassboost", "Bass boost", 0, 1, ["Off", "On"]),
    7: ("hi", "High cut", 0, 28, FREQS),
    8: ("lo", "Low cut", 0, 28, FREQS),
    16: ("balance", "Balance", 0, 30, None),
}


def describe(fn, v):
    key, label, lo, hi, labels = SETTINGS[fn]
    if labels:
        return labels[v] if 0 <= v < len(labels) else str(v)
    if fn == 1:
        return f"{v - 4} dB"                       # 0 = -4 dB ... 76 = +72 dB, 1 dB steps (manual: CD -4, MM 38, MC 56)
    if fn in (2, 3, 4, 5):
        return f"{v - 6:+d}"                        # 0..12 shown as -6..+6 (0 = flat)
    if fn == 16:
        return "Center" if v == 15 else (f"L{15 - v}" if v < 15 else f"R{v - 15}")
    if fn == 0:
        return "Muted" if v == 0 else ("0 dB (full)" if v == 32 else f"step {v} of 32")
    return str(v)


def parse(p):
    if len(p) < 100:
        return None
    u16 = lambda i: p[i] | (p[i + 1] << 8)
    s = {SETTINGS[f][0]: {"fn": f, "value": p[f], "text": describe(f, p[f])} for f in SETTINGS}
    s["meters"] = {"adcL": u16(47), "adcR": u16(49), "dacL": u16(51), "dacR": u16(53),   # peaks; scale not documented
                   "adcClips": p[55], "dacClips": p[56], "stereo": p[57], "rpm": u16(58)}
    s["serial"] = u16(94)
    s["firmware"] = f"{p[71]}.{p[70]}"
    return s


class BridgeWaxwing:
    """Talks to the Waxwing through the small bridge on the piCorePlayer Pi (pi-waxbridge/waxbridge.py), because the Waxwing
    used to drop every link after ~5 s; fixed 14 Nov 2026 with Shannon Parks' advice (wait after subscribing). Changes land in about a second."""

    def __init__(self, url):
        self.url = url.rstrip("/")
        self.error = None

    def get_status(self):
        import json, urllib.request
        try:
            d = json.load(urllib.request.urlopen(self.url + "/status", timeout=4))
        except Exception as e:
            return {"connected": False, "status": None, "error": f"bridge not reachable ({type(e).__name__})", "age": None}
        p = d.get("packet")
        s = parse(bytes(p)) if p else None
        age = d.get("age")
        live = s is not None and age is not None and age < 5
        return {"connected": live, "status": s, "error": None if live else d.get("error"), "age": age}

    def set(self, fn, value):
        import json, urllib.request
        if fn not in SETTINGS:
            raise ValueError("not an allowed setting")
        _, _, lo, hi, _ = SETTINGS[fn]
        value = int(value)
        if not lo <= value <= hi:
            raise ValueError(f"value must be {lo} to {hi}")
        req = urllib.request.Request(self.url + "/set", data=json.dumps({"fn": fn, "value": value}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        r = json.load(urllib.request.urlopen(req, timeout=5))
        if not r.get("queued"):
            raise RuntimeError(r.get("error") or "bridge refused")


class Waxwing:
    def __init__(self, address=None):
        self.address = address          # remembered after the first scan
        self.status, self.when, self.error = None, 0.0, None
        self.last_wanted = 0.0
        self.connected = False
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._lock = None
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._watchdog(), self._loop)

    # ----- called from the web server thread -----
    def get_status(self):
        self.last_wanted = time.time()
        if not self.connected:
            asyncio.run_coroutine_threadsafe(self._ensure(), self._loop)
        fresh = self.status is not None and time.time() - self.when < 5
        return {"connected": self.connected and fresh, "status": self.status, "error": self.error,
                "age": round(time.time() - self.when, 1) if self.when else None}

    def set(self, fn, value):
        if fn not in SETTINGS:
            raise ValueError("not an allowed setting")
        _, _, lo, hi, _ = SETTINGS[fn]
        value = int(value)
        if not lo <= value <= hi:
            raise ValueError(f"value must be {lo} to {hi}")
        self.last_wanted = time.time()
        fut = asyncio.run_coroutine_threadsafe(self._write(fn, value), self._loop)
        fut.result(timeout=25)

    # ----- Bluetooth side (runs on its own event loop) -----
    async def _ensure(self):
        from bleak import BleakClient, BleakScanner
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.connected:
                return
            try:
                dev = None
                if self.address:
                    dev = await BleakScanner.find_device_by_address(self.address, timeout=8)
                if dev is None:
                    found = await BleakScanner.discover(timeout=8, return_adv=True)
                    for addr, (d, adv) in found.items():
                        if SERVICE in [u.lower() for u in (adv.service_uuids or [])]:
                            dev = d
                            break
                if dev is None:
                    self.error = "Waxwing not found (is it on? is the phone app connected to it?)"
                    return
                self.address = dev.address
                client = BleakClient(dev, timeout=20, disconnected_callback=lambda _c: self._gone())
                await client.connect()
                await client.start_notify(RX, self._on_packet)
                # Parks Audio's advice: wait after subscribing before the first command (about 1.1 s on Windows/Mac,
                # about double on Linux), or the Waxwing resets its link after ~5 s.
                import sys as _sys
                await asyncio.sleep(2.3 if _sys.platform.startswith("linux") else 1.2)
                await client.write_gatt_char(TX, bytes([99, 1, 0]), response=True)
                self._client, self.connected, self.error = client, True, None
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:200]
                self.connected = False

    def _gone(self):
        self.connected = False
        self._client = None

    def _on_packet(self, _sender, data):
        s = parse(bytes(data))
        if s:
            self.status, self.when = s, time.time()

    async def _write(self, fn, value):
        if not self.connected:
            await self._ensure()
        if not self.connected:
            raise RuntimeError(self.error or "Waxwing not connected")
        await self._client.write_gatt_char(TX, bytes([fn, value, 0]), response=True)

    async def _watchdog(self):
        while True:
            await asyncio.sleep(2)
            # The status stream can go quiet after a few seconds: ask again, and reconnect if that does not help.
            if self.connected and time.time() - self.last_wanted <= IDLE_SECONDS and time.time() - self.when > 2.5:
                try:
                    await self._client.write_gatt_char(TX, bytes([99, 1, 0]), response=True)
                except Exception:
                    pass
                if self.when and time.time() - self.when > 20:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._gone()
            if self.connected and time.time() - self.last_wanted > IDLE_SECONDS:
                try:
                    await self._client.write_gatt_char(TX, bytes([99, 0, 0]), response=True)
                    await self._client.disconnect()
                except Exception:
                    pass
                self._gone()
