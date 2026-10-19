"""Builds MoBetter's list of settings from the receiver's OWN Web Setup page (its "function tree").
Nothing from Yamaha is stored in this repository: the page is downloaded from your own receiver the first time MoBetter starts,
and the result is kept in the local data/ folder (never published).

    python schema_builder.py 192.168.0.50      # build data/schema.json for that receiver
"""
import gzip, json, os, re, sys, urllib.request
from html.parser import HTMLParser


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.nodes, self.sel, self.in_un = [], [], None, False

    def _cur(self):
        for n in reversed(self.stack):
            if n is not None:
                return n
        return None

    def _path(self):
        return [n["name"] for n in self.stack if n is not None and n["name"] != "root"]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            if "nn" in a:
                n = {"name": a["nn"], "path": None, "leaf": a.get("y_lf") == "1", "group_get": a.get("y_g") is not None,
                     "cat": a.get("cat"), "tp": a.get("tp"), "rw": "R" if a.get("y_rw") == "R" else "RW", "control": {"type": None}}
                self.stack.append(n)
                n["path"] = self._path()
                self.nodes.append(n)
            else:
                self.stack.append(None)
        elif tag == "form":
            n = self._cur()
            if n is not None and a.get("value") is not None and a.get("y_vt") == "MinMax":
                p = a["value"].split(",")
                if len(p) == 3 and re.fullmatch(r"-?\d+(\.\d+)?", p[2] or "x"):
                    n["control"] = {"type": "number", "min": float(p[0]), "max": float(p[1]), "step": float(p[2])}
                else:
                    n["control"] = {"type": "text", "min": int(p[0]), "max": int(p[1]), "charset": p[2]}
        elif tag == "input":
            n = self._cur()
            if n is not None and (a.get("type") or "").lower() == "button" and a.get("y_v") is not None:
                c = n["control"]
                if c["type"] in (None, "buttons"):
                    c["type"] = "buttons"
                    c.setdefault("options", []).append(a["y_v"])
        elif tag == "select":
            n = self._cur()
            if n is not None:
                n["control"] = {"type": "select", "options": []}
                self.sel = n
        elif tag == "option" and self.sel is not None:
            self.sel["control"]["options"].append(a.get("value"))
        elif tag == "font" and a.get("class") == "un":
            self.in_un = True

    def handle_endtag(self, tag):
        if tag == "div" and self.stack:
            self.stack.pop()
        elif tag == "select":
            self.sel = None
        elif tag == "font":
            self.in_un = False

    def handle_data(self, data):
        if self.in_un and self._cur() is not None:
            self._cur()["control"]["hint"] = data.strip()


def build(html_text):
    """Parse a receiver's Web Setup page into a list of setting nodes."""
    t = _Tree()
    t.feed(html_text)
    return [n for n in t.nodes if n["name"] != "root"]


def fetch_setup_page(ip, timeout=15):
    # the receiver refuses a plain request (HTTP 406) and only serves this page compressed, so ask for gzip like a browser does
    req = urllib.request.Request(f"http://{ip}/Setup/", headers={"Accept-Encoding": "gzip", "User-Agent": "MoBetter"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":  # some receivers send it compressed whatever was asked
        raw = gzip.decompress(raw)
    return raw.decode("utf8", "replace")


def build_for_receiver(ip, out_path):
    nodes = build(fetch_setup_page(ip))
    if not nodes:
        raise RuntimeError("the receiver's Web Setup page did not contain a settings tree")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf8") as f:
        json.dump(nodes, f, ensure_ascii=False)
    return len(nodes)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    print(build_for_receiver(sys.argv[1], os.path.join(here, "data", "schema.json")), "nodes written to data/schema.json")
