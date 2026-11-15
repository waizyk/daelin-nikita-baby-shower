"""Build the static GitHub Pages preview into docs/.

    python tools/build_preview.py

GitHub Pages can't run Python, so this renders the invitation, album and upload
pages to plain HTML with relative links. Uploads and the host desk only work on
the live app. Re-run (and commit docs/) after editing config.py or templates;
CI fails if docs/ is stale.
"""
import io
import os
import re
import shutil
import sys
import tempfile

# Set once the real app is live, e.g. "https://daelinnikita.pythonanywhere.com".
# The preview's buttons and QR then point there. Leave "" until then.
LIVE_URL = ""

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
PAGES = {"/": "index.html", "/photos": "photos.html", "/upload": "upload.html"}


def main():
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="preview-")   # empty album
    for k in ("HOST_PIN", "SECRET_KEY", "PUBLIC_URL"):
        os.environ.pop(k, None)
    sys.path.insert(0, ROOT)
    import app as A
    import config
    import qrcode
    import qrcode.image.svg

    live = LIVE_URL.rstrip("/")
    qr_target = live + "/upload" if live else config.EVENT["drive_url"]
    links = {"/": "./", "/photos": "photos.html", "/upload": "upload.html",
             "/static/style.css": "style.css", "/qr.svg": "qr.svg",
             "/qr-hunny.svg": "qr-hunny.svg"}

    def fix(match):
        attr, path = match.group(1), match.group(2)
        if path in links:
            return f'{attr}="{links[path]}"'
        if path.startswith("/static/"):
            return f'{attr}="{path[len("/static/"):]}"'
        return f'{attr}="{live + path if live else "./"}"'   # /host, /sign, ...

    note = (f'Preview only: to add photos, open <a href="{live}/upload">the live site</a>.'
            if live else
            'Preview only: the live photo site isn\'t up yet. Photos can go in the '
            f'<a href="{config.EVENT["drive_url"]}" target="_blank" rel="noopener">shared Drive folder</a> for now.')
    banner = ('<div style="background:#fff6d6;border-bottom:1px solid #f1dc9a;color:#6b4e16;'
              'font:600 14px/1.4 system-ui,sans-serif;padding:.6rem 1rem;text-align:center">'
              + note + '</div>')
    guard = ('<script>document.addEventListener("submit",function(e){e.preventDefault();'
             'e.stopImmediatePropagation();window.toast&&window.toast('
             + repr("This is the preview. Uploads work on the live site.") + ');},true);</script>')

    if os.path.isdir(DOCS):
        shutil.rmtree(DOCS)
    os.makedirs(DOCS)
    client = A.app.test_client()
    for route, name in PAGES.items():
        html = client.get(route).get_data(as_text=True)
        html = re.sub(r'(href|src)="(/[^"]*)"', fix, html)
        html = re.sub(r'window\.location\.href = \'/[^\']*\'', "window.location.href = 'photos.html'", html)
        html = re.sub(r'(<body[^>]*>)', r'\1' + banner.replace("\\", r"\\"), html, count=1)
        html = html.replace('<span class="script">/upload</span> — it always follows wherever this site is hosted.',
                            '<span class="script">the photo upload page</span> on the live site.')
        html = html.replace("</body>", guard + "</body>")
        with open(os.path.join(DOCS, name), "w", encoding="utf-8") as f:
            f.write(html)

    # style.css, fonts/ and img/ (robots.txt is written below)
    shutil.copytree(os.path.join(ROOT, "static"), DOCS, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("robots.txt"))
    buf = io.BytesIO()
    qrcode.make(qr_target, image_factory=qrcode.image.svg.SvgPathImage,
                box_size=12, border=2).save(buf)
    with open(os.path.join(DOCS, "qr.svg"), "wb") as f:
        f.write(buf.getvalue())
    with open(os.path.join(DOCS, "qr-hunny.svg"), "w", encoding="utf-8") as f:
        f.write(A.hunny_qr_svg(qr_target))
    with open(os.path.join(DOCS, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    print("docs/ built:", ", ".join(sorted(os.listdir(DOCS))), "| QR ->", qr_target)


if __name__ == "__main__":
    main()
