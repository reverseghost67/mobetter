"""HDMI audio watchdog (optional, Windows only).

Some receivers lose the audio from a PC's HDMI output after the PC's display resets the link (a reboot, a full-screen app
opening or closing): the PC keeps playing, the receiver shows no signal, and only re-selecting the input brings the sound back.

This watches for exactly that and fixes it: when the PC is playing sound (Windows' own level meter on the default output)
AND the receiver is on the watched input AND it reports no audio signal, for two checks in a row, it switches to another
input and back. Each fix is written to data/audit.log. It backs off after repeated tries so it can never flap.

Enable in config.json:   "hdmi_watch": {"input": "av4", "bounce_input": "av1"}
"""
import json, os, subprocess, sys, threading, time

# A long-lived PowerShell that prints the default output's peak level once a second (max of 10 samples).
# It re-finds the default output each second, because Windows can swap the HDMI endpoint after a link reset.
METER_PS = r'''
Add-Type @"
using System; using System.Runtime.InteropServices;
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice dev); }
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { [PreserveSig] int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[ComImport, Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioMeterInformation { int GetPeakValue(out float p); }
public static class Meter {
  public static float Peak() {
    try {
      var e = (IMMDeviceEnumerator)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")));
      IMMDevice d; if (e.GetDefaultAudioEndpoint(0, 1, out d) != 0) return -1;
      Guid g = typeof(IAudioMeterInformation).GUID; object o; d.Activate(ref g, 23, IntPtr.Zero, out o);
      var m = (IAudioMeterInformation)o; float pk = 0, p;
      for (int i = 0; i < 10; i++) { m.GetPeakValue(out p); if (p > pk) pk = p; System.Threading.Thread.Sleep(90); }
      return pk;
    } catch { return -1; }
  }
}
"@
while ($true) { [Console]::Out.WriteLine([Meter]::Peak().ToString([Globalization.CultureInfo]::InvariantCulture)); [Console]::Out.Flush() }
'''


class HdmiWatch:
    def __init__(self, cfg, receiver_call, audit):
        self.input = str(cfg.get("input", "av4")).lower()
        self.bounce = str(cfg.get("bounce_input", "av1")).lower()
        self.call, self.audit = receiver_call, audit
        self.peak = 0.0
        self.peak_at = 0.0
        self.fixes = []          # times of recent fixes (for back-off)
        self.last = "starting"   # last state, for /api/hdmiwatch

    def start(self):
        threading.Thread(target=self._meter, daemon=True, name="hdmi-meter").start()
        threading.Thread(target=self._loop, daemon=True, name="hdmi-watch").start()

    def _meter(self):
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        while True:
            try:
                p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", METER_PS],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=flags)
                for line in p.stdout:
                    try:
                        self.peak, self.peak_at = float(line.strip()), time.time()
                    except ValueError:
                        pass
            except Exception as e:
                self.last = f"meter failed: {e}"
            time.sleep(10)  # the meter process ended; start it again

    def _get(self, path):
        st, body, _ = self.call("GET", "/YamahaExtendedControl/v1/" + path, timeout=5)
        return json.loads(body.decode("utf8")) if st == 200 else {}

    def _loop(self):
        misses = 0
        while True:
            time.sleep(4)
            try:
                playing = self.peak > 0.005 and time.time() - self.peak_at < 5
                status = self._get("main/getStatus")
                if status.get("power") != "on" or status.get("input") != self.input:
                    misses, self.last = 0, "idle (receiver off or on another input)"
                    continue
                fmt = self._get("main/getSignalInfo").get("audio", {}).get("format", "")
                if not playing or fmt not in ("---", ""):
                    misses, self.last = 0, f"ok (signal {fmt or '?'}, PC {'playing' if playing else 'quiet'})"
                    continue
                misses += 1
                self.last = f"PC playing but receiver shows no signal ({misses})"
                if misses < 2:
                    continue
                now = time.time()
                self.fixes = [t for t in self.fixes if now - t < 600]
                if len(self.fixes) >= 3 or (self.fixes and now - self.fixes[-1] < 60):
                    self.last = "no signal, backing off (3 tries in 10 minutes)"
                    continue
                self.fixes.append(now)
                self.call("GET", f"/YamahaExtendedControl/v1/main/setInput?input={self.bounce}", timeout=5)
                time.sleep(3)
                self.call("GET", f"/YamahaExtendedControl/v1/main/setInput?input={self.input}", timeout=5)
                time.sleep(5)
                after = self._get("main/getSignalInfo").get("audio", {}).get("format", "?")
                self.audit(f"HDMI WATCH  no audio on {self.input} while the PC was playing: re-selected it (via {self.bounce}); signal now {after}")
                self.last = f"fixed (signal now {after})"
                misses = 0
            except Exception as e:
                self.last = f"error: {type(e).__name__}: {e}"
