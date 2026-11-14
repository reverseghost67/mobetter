"""Waxwing Bluetooth bridge for a Raspberry Pi near the Waxwing (written for piCorePlayer, which ships Python 3.11 + bleak).

Why a bridge: the Waxwing is a Bluetooth LE device, and the computer running MoBetter may be out of range or have a weak
Bluetooth stack. This small program runs on a Linux box next to the Waxwing, keeps one connection open while MoBetter is
being used, keeps the newest status packet and sends setting changes about a second after you make them. MoBetter talks to
it over the home network (set "waxwing_bridge": "http://<pi address>:8790" in MoBetter's config.json):
    GET  /status            -> {"connected", "age", "status": {...}, "error"}
    POST /set {"fn","value"} -> sends one setting (only the functions and ranges listed in SETTINGS)
Only private-network clients are served. It connects only while it is being asked for status (60 s idle -> it lets go, so the
phone app can connect). Log: /tmp/waxbridge.log
"""
import asyncio, ipaddress, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from bleak import BleakScanner, BleakClient

PORT = 8790
SERVICE = "4dddd48d-cf30-492e-850b-76d57dd76900"
TX = "4dddd48d-cf30-492e-850b-76d57dd76901"
RX = "4dddd48d-cf30-492e-850b-76d57dd76902"
IDLE = 60
# function -> (min, max); the same list MoBetter's panel offers (waxwing.py SETTINGS), plus 99 (status stream).
ALLOWED = {18: (0, 2), 0: (0, 32), 1: (0, 76), 15: (0, 1), 9: (0, 4), 19: (0, 12), 11: (0, 1), 13: (0, 1),
           2: (0, 12), 3: (0, 12), 4: (0, 12), 5: (0, 12), 6: (0, 1), 7: (0, 28), 8: (0, 28), 16: (0, 30)}

state = {"packet": None, "when": 0.0, "connected": False, "error": None, "wanted": 0.0, "address": None}
queue = []          # pending (fn, value) writes
qlock = threading.Lock()


def log(m):
    with open("/tmp/waxbridge.log", "a") as f:
        f.write(time.strftime("%H:%M:%S ") + m + "\n")


async def find():
    # Connect by address when we know it: a Waxwing that is still linked (even a leftover link) does not advertise,
    # so a scan would miss it. The address is learned on the first scan and kept in /home/tc/waxbridge/address.
    if not state["address"]:
        try:
            state["address"] = open("/home/tc/waxbridge/address").read().strip() or None
        except OSError:
            pass
    if state["address"]:
        return state["address"]
    for addr, (d, adv) in (await BleakScanner.discover(timeout=6, return_adv=True)).items():
        if SERVICE in [u.lower() for u in (adv.service_uuids or [])]:
            state["address"] = d.address
            try:
                open("/home/tc/waxbridge/address", "w").write(d.address)
            except OSError:
                pass
            return d
    return None


async def session(dev):
    lost = asyncio.Event()
    def on(_s, data):
        state["packet"], state["when"] = bytes(data), time.time()
    client = BleakClient(dev, timeout=10, disconnected_callback=lambda _c: lost.set())
    await client.connect()
    state["connected"], state["error"] = True, None
    try:
        # Shannon Parks (Parks Audio), 14 Nov 2026: subscribe ONCE, then wait before the first command, or the Waxwing hits an
        # internal com timeout and resets after ~5 s. 1.1 s is enough on Windows/Mac; Linux needs about double. Space commands
        # about 500 ms apart so the Waxwing is never flooded.
        await client.start_notify(RX, on)
        await asyncio.sleep(2.3)
        await client.write_gatt_char(TX, bytes([99, 1, 0]), response=True)
        last_write = time.time()
        while not lost.is_set() and time.time() - state["wanted"] < IDLE:
            with qlock:
                job = queue[0] if queue else None
            if job and time.time() - last_write >= 0.5:
                await asyncio.wait_for(client.write_gatt_char(TX, bytes([job[0], job[1], 0]), response=True), 5)
                last_write = time.time()
                with qlock:
                    if queue and queue[0] == job:
                        queue.pop(0)
                log(f"sent {job[0]}={job[1]}")
                continue
            try:
                await asyncio.wait_for(lost.wait(), 0.1)
            except asyncio.TimeoutError:
                pass
        if not lost.is_set():
            try:
                await client.write_gatt_char(TX, bytes([99, 0, 0]), response=True)   # stop the stream before letting go
            except Exception:
                pass
    finally:
        state["connected"] = False
        try:
            await client.disconnect()
        except Exception:
            pass


async def run():
    while True:
        busy = time.time() - state["wanted"] < IDLE or queue
        if not busy:
            await asyncio.sleep(0.5)
            continue
        try:
            dev = await find()
            if dev is None:
                state["error"] = "Waxwing not found (off, or out of range)"
                await asyncio.sleep(3)
                continue
            await session(dev)
        except Exception as e:
            state["error"] = f"{type(e).__name__}: {e}"[:160]
            log("session ended: " + state["error"])
            if "not found" in str(e).lower() and state["address"]:
                # A leftover link inside BlueZ hides the Waxwing (it stops advertising while linked): drop it.
                import subprocess
                subprocess.run(["sudo", "bluetoothctl", "disconnect", state["address"]], capture_output=True, timeout=10)
            state["connected"] = False
            await asyncio.sleep(1)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def ok_client(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return False

    def send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self.ok_client():
            return self.send(403, {"error": "private network only"})
        if self.path != "/status":
            return self.send(404, {"error": "not found"})
        state["wanted"] = time.time()
        p = state["packet"]
        self.send(200, {"connected": state["connected"], "age": round(time.time() - state["when"], 1) if state["when"] else None,
                        "packet": list(p) if p else None, "error": state["error"]})

    def do_POST(self):
        if not self.ok_client():
            return self.send(403, {"error": "private network only"})
        if self.path != "/set":
            return self.send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(n, 200)) or b"{}")
            fn, value = int(req["fn"]), int(req["value"])
        except Exception:
            return self.send(400, {"error": "bad request"})
        if fn not in ALLOWED or not ALLOWED[fn][0] <= value <= ALLOWED[fn][1]:
            return self.send(400, {"error": "setting or value not allowed"})
        state["wanted"] = time.time()
        with qlock:
            queue[:] = [j for j in queue if j[0] != fn] + [(fn, value)]   # newest value per setting wins
        self.send(200, {"queued": True})


if __name__ == "__main__":
    log("waxbridge starting")
    threading.Thread(target=lambda: ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever(), daemon=True).start()
    asyncio.run(run())
