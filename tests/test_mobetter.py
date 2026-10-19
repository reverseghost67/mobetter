"""Tests that need no receiver and no Yamaha files: a tiny fake receiver stands in. Run:  python -m unittest discover -s tests -v"""
import http.server, json, os, sys, threading, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import schema_builder, server  # noqa: E402

SAMPLE = """<div id='root' nn='root'>
 <div nn='Main_Zone'><div nn='Volume'>
  <div nn='Mute' y_lf='1' y_g=true cat='Volume'><form class='oo'><input type='button' value='On' y_v='On'><input type='button' value='Off' y_v='Off'></form></div>
  <div nn='Balance' y_lf='1' cat='Volume'><form value="-5,5,1" y_vt='MinMax' class='oo'><input type='text' name='write' y_v='-5,5,1'><font class="un">-5 to 5 / 1step</font></form></div>
  <div nn='Mode' y_lf='1' y_rw='R' cat='Volume'><select><option value='A'>A</option><option value='B'>B</option></select></div>
 </div></div></div>"""


class FakeReceiver(http.server.BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *a): pass
    def do_GET(self):
        FakeReceiver.seen.append(("GET", self.path))
        if self.path == "/Setup/":   # behaves like a real receiver: 406 unless the client accepts gzip, then sends the page compressed
            if "gzip" not in (self.headers.get("Accept-Encoding") or ""):
                self.send_response(406); self.end_headers(); return
            import gzip
            self.send_response(200); self.end_headers(); self.wfile.write(gzip.compress(SAMPLE.encode())); return
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"response_code":0}')
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        FakeReceiver.seen.append(("POST", body)); self.send_response(200); self.end_headers()
        self.wfile.write(b'<YAMAHA_AV rsp="GET" RC="0"><System><Config><Model_Name>FAKE-1</Model_Name><Version>1.0</Version></Config></System></YAMAHA_AV>')


class SchemaTests(unittest.TestCase):
    def test_parses_choices_numbers_and_selects(self):
        n = {x["name"]: x for x in schema_builder.build(SAMPLE)}
        self.assertEqual(n["Mute"]["control"]["options"], ["On", "Off"])
        self.assertTrue(n["Mute"]["group_get"] and n["Mute"]["cat"] == "Volume")
        self.assertEqual(n["Balance"]["control"]["hint"], "-5 to 5 / 1step")
        self.assertEqual(n["Mode"]["control"]["options"], ["A", "B"])
        self.assertEqual(n["Mode"]["rw"], "R")
        self.assertEqual(n["Mute"]["path"], ["Main_Zone", "Volume", "Mute"])


class FetchTests(unittest.TestCase):
    def test_fetches_a_gzip_only_setup_page(self):
        fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeReceiver)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        try:
            nodes = schema_builder.build(schema_builder.fetch_setup_page(f"127.0.0.1:{fake.server_address[1]}"))
            self.assertIn("Mute", [n["name"] for n in nodes])
        finally:
            fake.shutdown(); fake.server_close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeReceiver)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        server.CFG.update({"receiver": f"127.0.0.1:{cls.fake.server_address[1]}", "model": "FAKE-1", "firmware": "1.0"})
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.fake.shutdown(); cls.srv.server_close(); cls.fake.server_close()

    def call(self, method, path, headers=None, data=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_page_and_read_endpoints_work(self):
        self.assertEqual(self.call("GET", "/"), 200)
        self.assertEqual(self.call("GET", "/api/config"), 200)
        self.assertEqual(self.call("GET", "/api/yxc/system/getDeviceInfo"), 200)

    def test_setters_via_get_are_refused(self):
        FakeReceiver.seen.clear()
        self.assertEqual(self.call("GET", "/api/yxc/main/setPower?power=standby"), 405)
        self.assertEqual(FakeReceiver.seen, [])  # nothing reached the receiver

    def test_post_needs_the_page_header(self):
        body = b'<YAMAHA_AV cmd="GET"><System><Config>GetParam</Config></System></YAMAHA_AV>'
        self.assertEqual(self.call("POST", "/api/xml", {"Content-Type": "text/xml"}, body), 403)
        self.assertEqual(self.call("POST", "/api/xml", {"Content-Type": "text/xml", "X-RX-UI": "1"}, body), 200)

    def test_foreign_host_name_is_refused(self):
        self.assertEqual(self.call("GET", "/api/config", {"Host": "evil.example.com"}), 403)

    def test_risky_command_needs_confirmation(self):
        FakeReceiver.seen.clear()
        body = b'<YAMAHA_AV cmd="PUT"><System><Misc><Update><Yamaha_Network_Site><Control>Start</Control></Yamaha_Network_Site></Update></Misc></System></YAMAHA_AV>'
        h = {"Content-Type": "text/xml", "X-RX-UI": "1"}
        self.assertEqual(self.call("POST", "/api/xml", h, body), 409)
        self.assertEqual(FakeReceiver.seen, [])
        self.assertEqual(self.call("POST", "/api/xml", {**h, "X-Confirm": "yes"}, body), 200)


if __name__ == "__main__":
    unittest.main()
