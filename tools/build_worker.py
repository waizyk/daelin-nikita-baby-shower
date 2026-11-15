"""Build the static pages for the live Cloudflare Worker into worker/public/.

    python tools/build_worker.py            # uses LIVE_URL below for the QR codes

The Worker (worker/src/index.js) serves these files and handles /api/*, /m/* and /auth/*.
Uploads go straight into the parents' Google Photos album; the scrapbook shows approved items.
The ACE OPS "Event sites" workflow runs this script, then `wrangler deploy` in worker/.
"""
import os
import shutil
import sys
import tempfile

LIVE_URL = "https://daelin-nikita.aceops.workers.dev"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "worker", "public")


def main(live=LIVE_URL):
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="worker-build-")
    for k in ("HOST_PIN", "SECRET_KEY"):
        os.environ.pop(k, None)
    os.environ["PUBLIC_URL"] = live
    sys.path.insert(0, ROOT)
    import config
    config.PUBLIC_URL = live.rstrip("/")
    import app as A
    from flask import render_template

    ev = config.EVENT
    pages = {
        "index.html": ("index.html", dict(page="home")),
        "photos.html": ("w_album.html", dict(page="album")),
        "upload.html": ("w_upload.html", dict(page="upload", drive_url=ev["drive_url"], max_files=30)),
        "host.html": ("w_host.html", dict(page="host", drive_url=ev["drive_url"])),
        "sign.html": ("sign.html", dict(page="sign", upload_url=live.rstrip("/") + "/upload")),
        "404.html": ("w_404.html", dict(page="404")),
    }
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    with A.app.test_request_context("/", base_url=live):
        for out, (tpl, ctx) in pages.items():
            html = render_template(tpl, **ctx)
            with open(os.path.join(OUT, out), "w", encoding="utf-8") as f:
                f.write(html)
        qr = A.hunny_qr_svg(A.upload_url())
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(OUT, "static"))
    with open(os.path.join(OUT, "qr-hunny.svg"), "w", encoding="utf-8") as f:
        f.write(qr)
    with open(os.path.join(OUT, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")
    with open(os.path.join(OUT, "_headers"), "w") as f:
        f.write("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: same-origin\n"
                "  Permissions-Policy: camera=(), microphone=(), geolocation=()\n"
                "/host\n  X-Frame-Options: DENY\n  Cache-Control: no-store\n"
                "/static/*\n  Cache-Control: public, max-age=86400\n")
    print("worker/public built:", ", ".join(sorted(os.listdir(OUT))), "| QR ->", live.rstrip("/") + "/upload")


if __name__ == "__main__":
    main(*(sys.argv[1:2]))
