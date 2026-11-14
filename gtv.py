"""Google TV / Android TV remote for MoBetter (optional).

Uses the same local "Android TV Remote" protocol as Google's phone remote app (library: androidtvremote2, also used by
Home Assistant). Everything stays on the home network: no Google account, no cloud. The first time, the TV shows a
6-digit code and you type it into MoBetter; the pairing certificate is kept in data/ (never published).

If the library is not installed, MoBetter still runs; only the Google TV card says it is unavailable.
"""
import asyncio, os, threading

try:
    from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
    AVAILABLE = True
except Exception:  # library not installed
    AVAILABLE = False

# Only these keys can be sent from the page (the buttons on the onn remote). Values are Android key names.
KEYS = {
    "power": "POWER", "star": "BOOKMARK", "assistant": "ASSIST", "profile": "PROFILE_SWITCH", "settings": "SETTINGS",
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "ok": "DPAD_CENTER",
    "back": "BACK", "home": "HOME", "tv": "TV", "guide": "GUIDE",
    "vol_up": "VOLUME_UP", "vol_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
    "ch_up": "CHANNEL_UP", "ch_down": "CHANNEL_DOWN",
    "play_pause": "MEDIA_PLAY_PAUSE", "rewind": "MEDIA_REWIND", "forward": "MEDIA_FAST_FORWARD",
}
# App buttons: opened by link, as the Google TV remote app does.
APPS = {
    "youtube": "https://www.youtube.com",
    "netflix": "https://www.netflix.com/title",
    "disney": "https://www.disneyplus.com",
    "paramount": "https://www.paramountplus.com",
}


class GoogleTV:
    def __init__(self, host, data_dir):
        self.host = host
        self.cert_file = os.path.join(data_dir, "gtv-cert.pem")
        self.key_file = os.path.join(data_dir, "gtv-key.pem")
        self.remote = None
        self.state = {"available": AVAILABLE, "host": host, "paired": os.path.exists(self.cert_file), "connected": False,
                      "on": None, "app": None, "error": None}
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro, timeout=20):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _remote(self):
        if self.remote is None:
            self.remote = AndroidTVRemote("MoBetter", self.cert_file, self.key_file, self.host)
            await self.remote.async_generate_cert_if_missing()
            self.remote.add_is_on_updated_callback(lambda v: self.state.update(on=v))
            self.remote.add_current_app_updated_callback(lambda v: self.state.update(app=v))
        return self.remote

    async def _connect(self):
        r = await self._remote()
        if self.state["connected"]:
            return r
        try:
            await r.async_connect()
            r.keep_reconnecting()
            self.state.update(connected=True, paired=True, error=None, on=r.is_on, app=r.current_app)
        except InvalidAuth:
            self.state.update(connected=False, paired=False, error="needs pairing")
            raise
        except (CannotConnect, ConnectionClosed, OSError) as e:
            self.state.update(connected=False, error=f"not reachable: {e}")
            raise
        return r

    # ---- called from the web server (thread-safe) ----
    def status(self):
        if AVAILABLE and self.state["paired"] and not self.state["connected"]:
            try:
                self.run(self._connect(), 8)
            except Exception:
                pass
        return dict(self.state)

    def start_pairing(self):
        async def go():
            r = await self._remote()
            self.state.update(connected=False)
            await r.async_start_pairing()
        self.run(go())

    def finish_pairing(self, code):
        async def go():
            r = await self._remote()
            await r.async_finish_pairing(code)
            self.state.update(paired=True)
            await self._connect()
        self.run(go())

    def key(self, name):
        async def go():
            (await self._connect()).send_key_command(KEYS[name])
        self.run(go())

    def app(self, name):
        async def go():
            (await self._connect()).send_launch_app_command(APPS[name])
        self.run(go())


# ---- Screen view (optional): screenshots through Android's "wireless debugging" (adb), local network only. ----
# Streaming apps block screenshots (they show black); menus and the home screen show. Off unless switched on in the page.
import re as _re, shutil as _shutil, subprocess as _sp

ADB = os.environ.get("MOBETTER_ADB") or _shutil.which("adb") or r"C:\ClaudeMaster\tools\platform-tools\adb.exe"


class GTVScreen:
    def __init__(self, host):
        self.host = host
        self.target = None  # "ip:port" of the debugging connection (the port changes after a reboot)
        self.lock = threading.Lock()

    def _adb(self, *args, timeout=8, binary=False):
        r = _sp.run([ADB, *args], capture_output=True, timeout=timeout)
        return r.stdout if binary else r.stdout.decode("utf8", "replace") + r.stderr.decode("utf8", "replace")

    def available(self):
        return os.path.exists(ADB) or bool(_shutil.which("adb"))

    def _find_target(self):
        # Wireless debugging announces itself on the network (mDNS); pick the one at the Google TV's address.
        out = self._adb("mdns", "services")
        for m in _re.finditer(r"_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+):(\d+)", out):
            if m.group(1) == self.host:
                return f"{m.group(1)}:{m.group(2)}"
        # already connected from an earlier run?
        for line in self._adb("devices").splitlines():
            if line.startswith(self.host + ":") and "\tdevice" in line:
                return line.split("\t")[0]
        return None

    def connect(self):
        t = self._find_target()
        if not t:
            return None
        out = self._adb("connect", t)
        if "connected" in out and "cannot" not in out:
            self.target = t
            return t
        return None

    def pair(self, port, code):
        out = self._adb("pair", f"{self.host}:{int(port)}", str(code), timeout=20)
        return "Successfully paired" in out, out.strip()[:200]

    def screenshot(self):
        with self.lock:
            for attempt in (1, 2):
                if not self.target and not self.connect():
                    return None
                png = self._adb("-s", self.target, "exec-out", "screencap", "-p", timeout=10, binary=True)
                if png[:8] == b"\x89PNG\r\n\x1a\n":
                    return png
                self.target = None  # port changed or connection dropped: find it again once
            return None

    def disconnect(self):
        self._adb("disconnect")
        self.target = None
