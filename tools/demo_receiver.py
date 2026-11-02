"""A pretend receiver, for trying MoBetter without hardware and for screenshots. It makes up everything; it is not a Yamaha product.

    python tools/demo_receiver.py                  # listens on 127.0.0.1:8790
    python server.py --receiver 127.0.0.1:8790     # in another window, then open http://localhost:8780

Changes you make in the page are kept in memory only, so restarting this resets the demo.
"""
import gzip, http.server, re, sys

STATE = {"Power": "On", "Vol": -420, "Mute": "Off", "Input": "HDMI1", "Program": "Straight", "Straight": "On", "Enhancer": "Off", "Pure": "Off",
         "Bass": 20, "Treble": 0, "Sub": -10, "YPAO": "Auto", "XBass": "Off", "DRC": "Off"}
INPUTS = [("TUNER", "Tuner"), ("HDMI1", "Disc Player"), ("HDMI2", "Game Console"), ("HDMI3", "Streamer"), ("HDMI4", "HDMI 4"), ("AV1", "TV Audio"),
          ("AUDIO1", "Turntable"), ("Spotify", "Spotify"), ("NET RADIO", "NET RADIO"), ("Bluetooth", "Bluetooth")]
SCENES = ["Movie night", "Music", "Late night", "Game"]
PROGRAMS = ["Straight", "2ch Stereo", "7ch Stereo", "Munich", "Vienna", "Chamber", "Action Game", "Sci-Fi", "Adventure"]
SETUP = """<div id='root' nn='root'><div nn='Main_Zone'>
 <div nn='Volume'><div nn='Subwoofer_Trim' y_lf='1' y_g=true cat='Volume'><form value="-6,6,0.5" y_vt='MinMax'><font class="un">dB(-6.0 to 6.0 / 0.5step)</font></form></div></div>
 <div nn='Sound_Video'><div nn='Tone'><div nn='Bass' y_lf='1' y_g=true cat='Tone'><form value="-6,6,0.5" y_vt='MinMax'><font class="un">dB(-6.0 to 6.0 / 0.5step)</font></form></div>
  <div nn='Treble' y_lf='1' y_g=true cat='Tone'><form value="-6,6,0.5" y_vt='MinMax'><font class="un">dB(-6.0 to 6.0 / 0.5step)</font></form></div></div>
  <div nn='YPAO_Volume' y_lf='1' y_g=true cat='Sound'><select><option value='Off'>Off</option><option value='Auto'>Auto</option></select></div>
  <div nn='Extra_Bass' y_lf='1' y_g=true cat='Sound'><select><option value='Off'>Off</option><option value='On'>On</option></select></div>
  <div nn='Adaptive_DRC' y_lf='1' y_g=true cat='Sound'><select><option value='Off'>Off</option><option value='Auto'>Auto</option></select></div></div>
 <div nn='Surround'><div nn='Program_Sel'><div nn='Current'><div nn='Sound_Program' y_lf='1' y_g=true cat='Sound'><select>%s</select></div></div></div></div>
</div></div>""" % "".join(f"<option value='{p}'>{p}</option>" for p in PROGRAMS)


def status():
    s = STATE
    return (f"<Main_Zone><Basic_Status><Power_Control><Power>{s['Power']}</Power></Power_Control>"
            f"<Volume><Lvl><Val>{s['Vol']}</Val><Exp>1</Exp><Unit>dB</Unit></Lvl><Mute>{s['Mute']}</Mute><Subwoofer_Trim><Val>{s['Sub']}</Val></Subwoofer_Trim></Volume>"
            f"<Input><Input_Sel>{s['Input']}</Input_Sel></Input><Surround><Program_Sel><Current><Sound_Program>{s['Program']}</Sound_Program>"
            f"<Straight>{s['Straight']}</Straight><Enhancer>{s['Enhancer']}</Enhancer></Current></Program_Sel></Surround>"
            f"<Sound_Video><Pure_Direct><Mode>{s['Pure']}</Mode></Pure_Direct><Tone><Bass><Val>{s['Bass']}</Val></Bass><Treble><Val>{s['Treble']}</Val></Treble></Tone>"
            f"<YPAO_Volume>{s['YPAO']}</YPAO_Volume><Extra_Bass>{s['XBass']}</Extra_Bass><Adaptive_DRC>{s['DRC']}</Adaptive_DRC></Sound_Video>"
            f"</Basic_Status></Main_Zone>")


def answer(body):
    if 'cmd="PUT"' in body:
        for key, tag in (("Power", "Power"), ("Mute", "Mute"), ("Input", "Input_Sel"), ("Program", "Sound_Program"), ("Straight", "Straight"), ("Enhancer", "Enhancer"),
                         ("Pure", "Mode"), ("YPAO", "YPAO_Volume"), ("XBass", "Extra_Bass"), ("DRC", "Adaptive_DRC")):
            m = re.search(rf"<{tag}>([^<]*)</{tag}>", body)
            if m:
                STATE[key] = m.group(1)
        for key, tag in (("Vol", "Lvl"), ("Bass", "Bass"), ("Treble", "Treble"), ("Sub", "Subwoofer_Trim")):
            m = re.search(rf"<{tag}><Val>(-?\d+)</Val>", body)
            if m:
                STATE[key] = int(m.group(1))
        return '<YAMAHA_AV rsp="PUT" RC="0"></YAMAHA_AV>'
    if "<System><Config>" in body:
        return ('<YAMAHA_AV rsp="GET" RC="0"><System><Config><Model_Name>DEMO-1</Model_Name><Version>1.00</Version><Feature_Existence><Main_Zone>1</Main_Zone>'
                '<Tuner>1</Tuner><Spotify>1</Spotify><Bluetooth>1</Bluetooth><DAB>0</DAB></Feature_Existence></Config></System></YAMAHA_AV>')
    if "Input_Sel_Item" in body:
        items = "".join(f"<Item_{i}><Param>{p}</Param><Title>{t}</Title><RW>R</RW></Item_{i}>" for i, (p, t) in enumerate(INPUTS, 1))
        return f'<YAMAHA_AV rsp="GET" RC="0"><Main_Zone><Input><Input_Sel_Item>{items}</Input_Sel_Item></Input></Main_Zone></YAMAHA_AV>'
    if "Scene_Sel_Item" in body:
        items = "".join(f"<Item_{i}><Param>Scene {i}</Param><Title>{t}</Title><RW>W</RW></Item_{i}>" for i, t in enumerate(SCENES, 1))
        return f'<YAMAHA_AV rsp="GET" RC="0"><Main_Zone><Scene><Scene_Sel_Item>{items}</Scene_Sel_Item></Scene></Main_Zone></YAMAHA_AV>'
    if "<Basic_Status>" in body:
        return f'<YAMAHA_AV rsp="GET" RC="0">{status()}</YAMAHA_AV>'
    return '<YAMAHA_AV rsp="GET" RC="2"></YAMAHA_AV>'   # anything else: "not supported", like a real receiver


class Demo(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="text/xml"):
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/Setup/":
            if "gzip" not in (self.headers.get("Accept-Encoding") or ""):   # real receivers answer 406 unless the client accepts gzip
                self.send_response(406); self.end_headers(); return
            return self.reply(gzip.compress(SETUP.encode()), "text/html")
        if self.path.endswith("/system/getDeviceInfo"):
            return self.reply(b'{"response_code":0,"model_name":"DEMO-1","api_version":2.08,"system_version":1.0}', "application/json")
        self.send_response(404); self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf8", "replace")
        self.reply(answer(body).encode())


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    print(f"Demo receiver on 127.0.0.1:{port} (pretend, in-memory). Ctrl+C to stop.")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Demo).serve_forever()
