"""End-to-end test of the live site, run locally against mock_google.py.

  1. worker/.dev.vars points GOOGLE_AUTH_URL / GOOGLE_TOKEN_URL / PHOTOS_API at http://127.0.0.1:9999
  2. python worker/test/e2e/mock_google.py &      3. (cd worker && npx wrangler dev --port 8790 --local) &
  4. python worker/test/e2e/e2e.py <setup-code>      (start from an empty worker/.wrangler/state each run)

Phase 1: Google NOT connected → guests upload into site storage, hosts approve, scrapbook shows them.
Phase 2: connect Google → host copies everything into Google Photos → scrapbook still shows them.
"""
import io, json, os, re, sys, time
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
B = "http://127.0.0.1:8790"
CODE = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/e2e-shots"
STATE = "/tmp/mock_google_state.json"
os.makedirs(OUT, exist_ok=True)
H = {"content-type": "application/json", "x-requested-with": "fetch"}


def jpg(src, w=None, crop=None, bg=(250, 240, 220)):
    im = Image.open(os.path.join(ROOT, "static", "img", src)).convert("RGBA")
    flat = Image.new("RGB", im.size, bg); flat.paste(im, mask=im.split()[3])
    if crop: flat = flat.crop(crop)
    if w: flat = flat.resize((w, int(flat.height * w / flat.width)))
    b = io.BytesIO(); flat.save(b, "JPEG", quality=92); return b.getvalue()


def big_jpg():                       # a "phone photo": 4000 px, ~3+ MB, gets shrunk in site-storage mode
    im = Image.open(os.path.join(ROOT, "static", "img", "hero.webp")).convert("RGB").resize((4000, 2668))
    noise = Image.effect_noise((4000, 2668), 40).convert("RGB")
    im = Image.blend(im, noise, .12)
    b = io.BytesIO(); im.save(b, "JPEG", quality=95); return b.getvalue()


def real_video():                    # ~48 MB playable WebM (Chromium can't decode H.264) → 3 storage pieces
    import subprocess, imageio_ffmpeg
    out = "/tmp/e2e-cake.webm"
    if not os.path.exists(out):
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=30,noise=alls=60:allf=t", "-t", "10", "-c:v", "libvpx",
                        "-deadline", "realtime", "-cpu-used", "8", "-b:v", "40M", "-minrate", "40M", "-maxrate", "40M",
                        "-bufsize", "8M", out], check=True)
    return open(out, "rb").read()


VIDEO = real_video()
VIDEO_LEN = len(VIDEO)
FILES = [
    {"name": "IMG_2041.jpg", "mimeType": "image/jpeg", "buffer": big_jpg()},
    {"name": "IMG_2042.jpg", "mimeType": "image/jpeg", "buffer": jpg("friends.webp", 1000)},
    {"name": "IMG_2043.jpg", "mimeType": "image/jpeg", "buffer": jpg("map.webp", 700, (100, 100, 700, 819))},
    {"name": "cake-smash.webm", "mimeType": "video/webm", "buffer": VIDEO},
    {"name": "IMG_2044.jpg", "mimeType": "image/jpeg", "buffer": jpg("balloon.webp", 600)},
    {"name": "menu.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"},
]
# Large buffers crash Chromium's set_input_files, so the test files go to disk first.
TMP = "/tmp/e2e-files"; os.makedirs(TMP, exist_ok=True)
for f in FILES:
    with open(os.path.join(TMP, f["name"]), "wb") as fh: fh.write(f["buffer"])
PATHS = [os.path.join(TMP, f["name"]) for f in FILES]
fails = []


def ok(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c: fails.append(m)


def mock():
    time.sleep(1.3)
    return json.load(open(STATE)) if os.path.exists(STATE) else {"items": {}, "flaked": False}


with sync_playwright() as p:
    b = p.chromium.launch()
    errs = []
    host = b.new_context(viewport={"width": 1280, "height": 900}); hp = host.new_page()
    hp.on("pageerror", lambda e: errs.append(str(e)))
    guest = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    gp = guest.new_page(); gp.on("pageerror", lambda e: errs.append(str(e)))

    # ---------- phase 1: no Google yet
    st = gp.request.get(B + "/api/status").json()
    ok(st["accepting"] and st["mode"] == "kv", f"uploads open without Google (mode={st['mode']})")
    gp.goto(B + "/upload"); gp.wait_for_timeout(700)
    ok(gp.locator("#closed").is_hidden(), "no 'album isn't open' note")
    gp.set_input_files("#fileInput", PATHS); gp.wait_for_timeout(800)
    ok(gp.locator("#strip .thumb").count() == 5, "5 media kept, PDF refused")
    gp.fill("#guest", "Auntie Zee"); gp.fill("#message", "So much love for the little one! 🍯")
    t = time.time(); gp.click("#submitBtn"); gp.wait_for_selector("#result .note", timeout=180000)
    res = gp.inner_text("#result")
    ok("5 files added" in res, f"guest upload done in {time.time()-t:.1f}s: {res.splitlines()[0]!r}")
    gp.screenshot(path=OUT + "/1_upload_done.png", full_page=True)
    ok(gp.request.get(B + "/api/photos").json()["items"] == [], "nothing public before approval")

    hp.goto(B + "/host"); hp.fill("#code", CODE); hp.click("#loginForm button"); hp.wait_for_selector("#desk:not([hidden])")
    hp.wait_for_selector(".desk-item"); hp.wait_for_timeout(1200)
    ok("Guests can upload" in hp.inner_text("#connState"), "desk says guests can upload")
    ok("5 items kept on the website" in hp.inner_text("#storeState"), "desk storage line: " + hp.inner_text("#storeState"))
    pend = hp.request.get(B + "/api/host/media?status=pending").json()["items"]
    ok(len(pend) == 5, "5 waiting for approval")
    big = [i for i in pend if i["name"] == "IMG_2041.jpg"][0]
    ok(big["size"] < len(FILES[0]["buffer"]) / 1.5, f"big phone photo shrunk {len(FILES[0]['buffer'])//1024} KB → {big['size']//1024} KB")
    vid = [i for i in pend if i["kind"] == "video"][0]
    ok(vid["size"] == VIDEO_LEN, "video stored at full size")
    r = gp.request.get(B + f"/m/{vid['id']}/v"); ok(r.status == 404, f"guest can't open pending video ({r.status})")
    hp.screenshot(path=OUT + "/2_host_pending.png", full_page=True)
    hp.click("#allBtn"); hp.wait_for_timeout(1000)
    items = gp.request.get(B + "/api/photos").json()["items"]
    ok(len(items) == 5, "all 5 approved")

    # serving from site storage
    r = gp.request.get(B + f"/m/{vid['id']}/v")
    ok(r.status == 200 and len(r.body()) == VIDEO_LEN, f"full video streams from storage ({len(r.body())} bytes)")
    ok(r.body() == FILES[3]["buffer"], "video bytes identical to what the guest sent")
    r = gp.request.get(B + f"/m/{vid['id']}/v", headers={"range": "bytes=0-1"})
    ok(r.status == 206 and r.headers["content-range"] == f"bytes 0-1/{VIDEO_LEN}", "byte ranges work (iPhone video playback)")
    r = gp.request.get(B + f"/m/{vid['id']}/v", headers={"range": f"bytes={20*1024*1024 - 5}-"})
    ok(r.status == 206 and len(r.body()) == 5, "range across a storage piece boundary is answered correctly")
    r = gp.request.get(B + f"/m/{items[0]['id']}/t")
    ok(r.status == 200 and r.headers["content-type"] == "image/jpeg", "thumbnails served")
    phot = [i for i in items if i["kind"] == "photo"][0]
    r = gp.request.get(B + f"/m/{phot['id']}/f"); im = Image.open(io.BytesIO(r.body()))
    ok(r.status == 200 and max(im.size) <= 2560, f"full photo served ({im.size})")

    gp.close(); gp = guest.new_page(); gp.on("pageerror", lambda e: errs.append(str(e)))
    gp.goto(B + "/photos"); gp.wait_for_selector(".snap"); gp.wait_for_timeout(1500)
    ok(gp.locator(".snap").count() == 5, "scrapbook shows 5 on phone")
    ok(gp.locator(".snap.developing").count() == 0, "every card has a picture")
    gp.screenshot(path=OUT + "/3_scrap_mobile.png", full_page=True)
    hp.goto(B + "/photos"); hp.wait_for_selector(".snap"); hp.wait_for_timeout(1500)
    hp.screenshot(path=OUT + "/4_scrap_desktop.png", full_page=True)
    hp.locator(".snap:not(.film) .pic").first.click(); hp.wait_for_timeout(1000)
    ok(hp.evaluate("document.querySelector('#lbStage img')?.naturalWidth > 0"), "lightbox shows the full photo")
    hp.keyboard.press("Escape")
    hp.locator(".snap.film .pic").click()
    try:
        hp.wait_for_function("(v => v && v.readyState >= 2 && v.videoWidth > 0)(document.querySelector('#lbStage video'))", timeout=20000)
        hp.evaluate("document.querySelector('#lbStage video').currentTime = 7"); hp.wait_for_timeout(1500)
        ok(True, "video plays in the browser (and seeks), streamed from site storage")
    except Exception as e:
        ok(False, "video plays: " + hp.inner_text("#lbStage")[:80])
    hp.screenshot(path=OUT + "/5_video.png")
    hp.keyboard.press("Escape")

    # ---------- phase 2: connect Google, copy everything across
    hp.goto(B + "/host"); hp.wait_for_selector("#desk:not([hidden])")
    hp.fill("#cid", "1234-abc.apps.googleusercontent.com"); hp.fill("#csec", "secretsecret1"); hp.click("#clientForm button")
    hp.wait_for_selector("#connectBtn:not([hidden])"); hp.click("#connectBtn"); hp.wait_for_url(re.compile(r"/host")); hp.wait_for_timeout(1500)
    ok("Connected" in hp.inner_text("#connState"), "Google Photos connected")
    ok(hp.locator("#moveBtn").is_visible() and "Copy 5" in hp.inner_text("#moveBtn"), "desk offers: " + hp.inner_text("#moveBtn"))
    ok(gp.request.get(B + "/api/status").json()["mode"] == "google", "new uploads now go to Google")
    hp.click("#moveBtn"); hp.wait_for_function("!document.querySelector('#moveBtn').disabled", timeout=120000); hp.wait_for_timeout(1200)
    if hp.locator("#moveBtn").is_visible():        # the mock drops one chunk on purpose: tap again
        hp.click("#moveBtn"); hp.wait_for_function("!document.querySelector('#moveBtn').disabled", timeout=120000); hp.wait_for_timeout(1200)
    st = hp.request.get(B + "/api/host/state").json()
    ok(st["stored"]["n"] == 0, f"site storage emptied ({st['stored']})")
    m = mock()
    ok(len(m["items"]) == 5, f"5 items now in the Google Photos album ({len(m['items'])})")
    gv = [v for v in m["items"].values() if v["video"]]
    ok(gv and gv[0]["len"] == VIDEO_LEN, "video copied to Google byte-perfect")
    ok(all("From Auntie Zee" in v["desc"] for v in m["items"].values()), "names + messages kept in Google descriptions")
    items2 = gp.request.get(B + "/api/photos").json()["items"]
    ok([i["id"] for i in items2] == [i["id"] for i in items], "scrapbook unchanged after the move")
    r = gp.request.get(B + f"/m/{phot['id']}/f", max_redirects=0)
    ok(r.status == 302 and "127.0.0.1:9999/media/" in r.headers["location"], "full photo now comes from Google Photos")
    r = gp.request.get(B + f"/m/{phot['id']}/t"); ok(r.status == 200 and r.headers["content-type"] == "image/jpeg", "thumbnail still served")

    # Google mode upload
    gp.goto(B + "/upload"); gp.wait_for_timeout(600)
    gp.set_input_files("#fileInput", [PATHS[1]]); gp.fill("#guest", "Uncle Bo"); gp.click("#submitBtn")
    gp.wait_for_selector("#result .note", timeout=60000)
    ok("1 file added" in gp.inner_text("#result"), "upload in Google mode works")
    m = mock(); ok(any("Uncle Bo" in v["desc"] for v in m["items"].values()), "it went straight to Google")
    ok(not errs, "no JS errors " + str(errs[:3]))
    b.close()
print("\nFAILURES:", fails or "none")
sys.exit(1 if fails else 0)
