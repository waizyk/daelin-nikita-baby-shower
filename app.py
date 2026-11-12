import io
import json
import re
import os
import secrets
import sqlite3
import time
import uuid

import qrcode
import qrcode.image.svg
from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, ImageOps

try:  # iPhone .HEIC uploads
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
FULL_DIR = os.path.join(UPLOAD_DIR, "full")
THUMB_DIR = os.path.join(UPLOAD_DIR, "thumb")
DB_PATH = os.path.join(DATA_DIR, "album.db")

for d in (DATA_DIR, FULL_DIR, THUMB_DIR):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024   # 60 MB per request
app.secret_key = config.SECRET_KEY
app.config["TEMPLATES_AUTO_RELOAD"] = True
# harden the host session cookie
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=config.PRODUCTION)   # HTTPS-only cookie in production
# Render / PythonAnywhere sit behind a proxy: trust its scheme + host headers so
# QR codes and links come out as https://<real domain>/...
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
           "image/gif", "image/bmp", "image/tiff", "image/jpg", ""}


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
def db():
    if "db" not in g:
        # timeout + WAL keeps things smooth when several guests upload at once
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=30000")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id          TEXT PRIMARY KEY,
            token       TEXT NOT NULL,
            guest       TEXT NOT NULL,
            message     TEXT,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  REAL NOT NULL,
            width       INTEGER,
            height      INTEGER,
            thumb_w     INTEGER,
            thumb_h     INTEGER
        )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            used  INTEGER NOT NULL DEFAULT 0
        )""")
    # lightweight migration for older databases
    cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    for col in ("thumb_w", "thumb_h"):
        if col not in cols:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} INTEGER")
    conn.commit()
    conn.close()


init_db()


def photos(status=None):
    q = "SELECT * FROM photos"
    args = ()
    if status:
        q += " WHERE status = ?"
        args = (status,)
    q += " ORDER BY created_at DESC"
    return [dict(r) for r in db().execute(q, args).fetchall()]


def counts():
    rows = db().execute("SELECT status, COUNT(*) c FROM photos GROUP BY status").fetchall()
    out = {"approved": 0, "pending": 0, "rejected": 0, "total": 0}
    for r in rows:
        out[r["status"]] = r["c"]
        out["total"] += r["c"]
    return out


# --------------------------------------------------------------------------
# template helpers
# --------------------------------------------------------------------------
_PH = re.compile(r"\[([^\]]+)\]")


@app.template_filter("datetimeformat")
def datetimeformat(epoch):
    txt = time.strftime("%a %d %b, %H:%M", time.localtime(int(epoch)))
    return re.sub(r" 0(\d)", r" \1", txt)


@app.template_filter("ph")
def ph(text):
    """Highlight [bracketed] placeholders so they're easy to spot before launch."""
    return Markup(_PH.sub(lambda m: f'<span class="ph">{m.group(1)}</span>', str(text)))


# --------------------------------------------------------------------------
# host auth
# --------------------------------------------------------------------------
def is_host():
    return session.get("host") is True


def pin_locked():
    """In production the well-known default PIN is never accepted."""
    return config.PRODUCTION and config.HOST_PIN == config.DEFAULT_PIN


@app.context_processor
def inject_globals():
    host = is_host()
    stats = None
    if host:
        try:
            stats = counts()
        except sqlite3.Error:
            stats = None
    return dict(EVENT=config.EVENT, SITE_NAME=config.SITE_NAME,
                is_host=host, stats=stats)


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", page="home")


@app.route("/photos")
def album():
    return render_template("album.html",
                           page="album",
                           photos=photos("approved"),
                           pending_count=counts()["pending"],
                           auto_approve=config.EVENT["auto_approve"])


@app.route("/upload")
def upload_page():
    return render_template("upload.html", page="upload",
                           drive_url=config.EVENT["drive_url"],
                           max_photos=config.EVENT["max_photos_per_guest"])


def upload_url():
    """Where every QR code points: PUBLIC_URL if configured, else this host."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL + url_for("upload_page")
    return url_for("upload_page", _external=True)


@app.route("/qr.svg")
def qr_svg():
    target = upload_url()
    img = qrcode.make(target, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return send_file(io.BytesIO(buf.getvalue()), mimetype="image/svg+xml")


@app.route("/qr.png")
def qr_png():
    """Raster QR for printing."""
    target = upload_url()
    img = qrcode.make(target, box_size=16, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/sign")
def sign():
    """Printable A5 'scan me' sign for the party table."""
    return render_template("sign.html", page="sign",
                           upload_url=upload_url())


@app.route("/robots.txt")
def robots():
    return app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.route("/api/qr-target")
def qr_target():
    return jsonify({"url": upload_url()})


# --------------------------------------------------------------------------
# upload API
# --------------------------------------------------------------------------
def guest_token():
    return session.get("guest_token")


@app.route("/api/token", methods=["POST"])
def new_token():
    token = secrets.token_urlsafe(6)
    while db().execute("SELECT 1 FROM tokens WHERE token = ?", (token,)).fetchone():
        token = secrets.token_urlsafe(6)
    db().execute("INSERT INTO tokens (token) VALUES (?)", (token,))
    db().commit()
    session["guest_token"] = token
    return jsonify({"token": token})


def make_images(raw, photo_id):
    """Save a web-friendly fullsize + a square-ish thumbnail. Returns (w, h)."""
    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGB")
    w, h = im.size

    full = im.copy()
    full.thumbnail((1800, 1800), Image.LANCZOS)
    full.save(os.path.join(FULL_DIR, f"{photo_id}.jpg"), "JPEG", quality=86,
              optimize=True, progressive=True)

    thumb = im.copy()
    thumb.thumbnail((760, 1000), Image.LANCZOS)
    thumb.save(os.path.join(THUMB_DIR, f"{photo_id}.jpg"), "JPEG", quality=80,
               optimize=True, progressive=True)
    return w, h, thumb.size[0], thumb.size[1]


@app.route("/api/upload", methods=["POST"])
def api_upload():
    token = request.form.get("token") or guest_token()
    if not token or not db().execute("SELECT 1 FROM tokens WHERE token = ?", (token,)).fetchone():
        return jsonify({"error": "Please refresh the page and try again."}), 400

    guest = (request.form.get("guest") or "").strip()[:60] or "A mystery guest"
    message = (request.form.get("message") or "").strip()[:300]
    files = request.files.getlist("photos")
    if not files:
        return jsonify({"error": "No photos received."}), 400
    if len(files) > config.EVENT["max_photos_per_guest"]:
        files = files[:config.EVENT["max_photos_per_guest"]]

    saved, failed = [], 0
    status = "approved" if config.EVENT["auto_approve"] else "pending"
    for f in files:
        raw = f.read()
        if not raw:
            continue
        pid = uuid.uuid4().hex[:16]
        try:
            w, h, tw, th = make_images(raw, pid)
        except Exception:
            failed += 1
            continue
        db().execute(
            "INSERT INTO photos (id, token, guest, message, status, created_at,"
            " width, height, thumb_w, thumb_h) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, token, guest, message, status, time.time(), w, h, tw, th))
        saved.append(pid)
    db().commit()
    return jsonify({"ok": True, "count": len(saved), "failed": failed,
                    "status": status})


@app.route("/media/thumb/<pid>.jpg")
def media_thumb(pid):
    if not is_host():
        row = db().execute("SELECT status FROM photos WHERE id = ?", (pid,)).fetchone()
        if not row or row["status"] != "approved":
            abort(404)
    return send_from_directory(THUMB_DIR, f"{pid}.jpg", max_age=86400)


@app.route("/media/full/<pid>.jpg")
def media_full(pid):
    if not is_host():
        row = db().execute("SELECT status FROM photos WHERE id = ?", (pid,)).fetchone()
        if not row or row["status"] != "approved":
            abort(404)
    return send_from_directory(FULL_DIR, f"{pid}.jpg", max_age=86400)


# --------------------------------------------------------------------------
# host area
# --------------------------------------------------------------------------
@app.route("/host", methods=["GET", "POST"])
def host():
    error = None
    if request.method == "POST":
        if pin_locked():
            error = ("The host PIN hasn't been set on the server yet "
                     "(HOST_PIN environment variable).")
        elif secrets.compare_digest(request.form.get("pin", "").encode(),
                                    config.HOST_PIN.encode()):
            session["host"] = True
            return redirect(url_for("host"))
        else:
            error = "That PIN isn't right — try again."
    if not is_host():
        return render_template("host_login.html", page="host", error=error)
    return render_template("host.html", page="host",
                           pending=photos("pending"),
                           approved=photos("approved"),
                           rejected=photos("rejected"),
                           drive_url=config.EVENT["drive_url"])


@app.route("/host/action", methods=["POST"])
def host_action():
    if not is_host():
        abort(403)
    pid = request.form.get("id")
    action = request.form.get("action")
    status_map = {"approve": "approved", "reject": "rejected", "pending": "pending"}
    if action in ("approve", "reject", "delete", "pending"):
        if action == "delete":
            db().execute("DELETE FROM photos WHERE id = ?", (pid,))
            for d in (FULL_DIR, THUMB_DIR):
                try:
                    os.remove(os.path.join(d, f"{pid}.jpg"))
                except OSError:
                    pass
        else:
            db().execute("UPDATE photos SET status = ? WHERE id = ?",
                         (status_map[action], pid))
        db().commit()
    back = request.form.get("back") or ""
    if not back.startswith("/") or back.startswith("//"):
        back = url_for("host")
    return redirect(back)


@app.route("/host/zip")
def host_zip():
    """Download every approved photo as one zip (for printing / archiving)."""
    if not is_host():
        abort(403)
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p in photos("approved"):
            path = os.path.join(FULL_DIR, f"{p['id']}.jpg")
            if os.path.exists(path):
                safe = "".join(c for c in p["guest"] if c.isalnum() or c in " -_") or "guest"
                z.write(path, f"{time.strftime('%Y-%m-%d', time.localtime(p['created_at']))} {safe} {p['id'][:6]}.jpg")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="baby-shower-photos.zip")


@app.route("/host/logout")
def host_logout():
    session.pop("host", None)
    return redirect(url_for("index"))


@app.errorhandler(413)
def too_big(e):
    return jsonify({"error": "That file is too big — try photos under 50 MB each."}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
