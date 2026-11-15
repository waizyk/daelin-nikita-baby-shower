"""A tiny stand-in for Google's OAuth + Photos Library API, for local end-to-end tests.

    python worker/test/e2e/mock_google.py            # listens on 127.0.0.1:9999
Point the Worker at it with worker/.dev.vars (see e2e.py). Drops one upload chunk on purpose.
"""
import io, json, re, sys, threading, time, urllib.parse as up
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image

S = {"sessions": {}, "items": {}, "n": 0, "flaked": False}
L = threading.Lock()
P = "http://127.0.0.1:9999"
STATE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mock_google_state.json"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass

    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        if isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("content-type", ctype); self.send_header("content-length", str(len(body)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def body(self):
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = up.urlparse(self.path); q = up.parse_qs(u.query)
        if u.path == "/o/oauth2/v2/auth":
            assert "photoslibrary.appendonly" in q["scope"][0] and q["access_type"][0] == "offline"
            return self.send(302, headers={"location": q["redirect_uri"][0] + "?" + up.urlencode({"code": "authcode1", "state": q["state"][0]})})
        if u.path == "/v1/mediaItems:batchGet":
            res = []
            for i in q.get("mediaItemIds", []):
                it = S["items"][i]; it["gets"] = it.get("gets", 0) + 1
                md = {"width": str(it["w"]), "height": str(it["h"])}
                if it["video"]: md["video"] = {"status": "READY" if it["gets"] > 1 else "PROCESSING"}
                res.append({"mediaItem": {"id": i, "baseUrl": f"{P}/media/{i}", "mediaMetadata": md}})
            return self.send(200, {"mediaItemResults": res})
        m = re.match(r"/media/([^=]+)=(.*)", u.path)
        if m:
            it = S["items"][m.group(1)]
            if it["video"] and m.group(2) == "dv": return self.send(200, it["data"], "video/mp4")
            if it["video"] or not it["img"]:
                im = Image.new("RGB", (640, 360), (60, 45, 30)); b = io.BytesIO(); im.save(b, "JPEG")
                return self.send(200, b.getvalue(), "image/jpeg")
            return self.send(200, it["data"], it["mime"])
        self.send(404, {"error": {"message": "nope " + u.path}})

    def do_POST(self):
        u = up.urlparse(self.path); data = self.body()
        if u.path == "/token":
            f = up.parse_qs(data.decode())
            assert f["client_secret"][0] == "secretsecret1"
            if f["grant_type"][0] == "authorization_code":
                return self.send(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3599,
                                       "scope": "https://www.googleapis.com/auth/photoslibrary.appendonly https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata"})
            return self.send(200, {"access_token": "at-2", "expires_in": 3599})
        if u.path == "/v1/albums":
            return self.send(200, {"id": "alb1", "title": json.loads(data)["album"]["title"], "productUrl": "https://photos.google.com/lr/album/alb1"})
        if u.path == "/v1/uploads":
            with L:
                S["n"] += 1; sid = f"s{S['n']}"
                S["sessions"][sid] = {"size": int(self.headers["x-goog-upload-raw-size"]), "mime": self.headers["x-goog-upload-content-type"], "buf": bytearray()}
            return self.send(200, headers={"x-goog-upload-url": f"{P}/up/{sid}", "x-goog-upload-chunk-granularity": "262144"})
        m = re.match(r"/up/(s\d+)", u.path)
        if m:
            s = S["sessions"][m.group(1)]; cmd = self.headers["x-goog-upload-command"]
            if cmd == "query":
                return self.send(200, headers={"x-goog-upload-size-received": str(len(s["buf"])), "x-goog-upload-status": "active"})
            off = int(self.headers["x-goog-upload-offset"])
            if off > 0 and not S["flaked"]:
                S["flaked"] = True; return self.send(503, "flaky", "text/plain")
            if off != len(s["buf"]): return self.send(400, "bad offset", "text/plain")
            s["buf"] += data
            if "finalize" in cmd:
                assert len(s["buf"]) == s["size"]
                return self.send(200, "tok-" + m.group(1), "text/plain")
            return self.send(200, b"", "text/plain")
        if u.path == "/v1/mediaItems:batchCreate":
            b = json.loads(data); assert b.get("albumId") == "alb1"
            nm = b["newMediaItems"][0]; sid = nm["simpleMediaItem"]["uploadToken"][4:]; s = S["sessions"][sid]
            video = s["mime"].startswith("video/"); w = h = 0; img = False
            if not video:
                try: im = Image.open(io.BytesIO(bytes(s["buf"]))); w, h = im.size; img = True
                except Exception: w, h = 800, 600
            iid = "gp_" + sid
            S["items"][iid] = {"data": bytes(s["buf"]), "mime": s["mime"], "video": video, "w": w or 1280, "h": h or 720,
                               "img": img, "desc": nm["description"], "name": nm["simpleMediaItem"]["fileName"]}
            md = {"width": str(w or 1280), "height": str(h or 720)}
            if video: md["video"] = {"status": "PROCESSING"}
            return self.send(200, {"newMediaItemResults": [{"status": {"message": "Success"}, "mediaItem": {"id": iid, "mimeType": s["mime"], "mediaMetadata": md}}]})
        self.send(404, {"error": {"message": "nope " + u.path}})


def dump():
    while True:
        time.sleep(1)
        snap = {k: {x: y for x, y in v.items() if x != "data"} | {"len": len(v["data"])} for k, v in list(S["items"].items())}
        with open(STATE, "w") as f: json.dump({"items": snap, "flaked": S["flaked"]}, f)


if __name__ == "__main__":
    threading.Thread(target=dump, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 9999), H).serve_forever()
