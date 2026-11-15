import io
import os
import re
import zipfile

import pytest
from PIL import Image

import app as A
import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def jpeg(color=(250, 210, 140), size=(640, 480)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    b.seek(0)
    return b


def upload(client, n=1, guest="Tester", files=None):
    token = client.post("/api/token").get_json()["token"]
    files = files or [(jpeg(), f"p{i}.jpg") for i in range(n)]
    return client.post("/api/upload", data={"token": token, "guest": guest,
                                            "message": "hi", "photos": files},
                       content_type="multipart/form-data")


def all_ids(status=None):
    with A.app.app_context():
        return [p["id"] for p in A.photos(status)]


# ---------------------------------------------------------------- pages
@pytest.mark.parametrize("path", ["/", "/photos", "/upload", "/host", "/sign"])
def test_pages_return_200(client, path):
    assert client.get(path).status_code == 200


def test_invite_shows_party_facts(client):
    html = client.get("/").get_data(as_text=True)
    assert "Daelin &amp; Nikita" in html or "Daelin & Nikita" in html
    assert "5 December 2026" in html


def test_placeholders_are_highlighted():
    assert str(A.ph("At [Venue name] today")) == 'At <span class="ph">Venue name</span> today'


def test_placeholder_on_page_is_highlighted(client, monkeypatch):
    monkeypatch.setitem(config.EVENT, "hosts", "[Host name(s)]")
    assert '<span class="ph">Host name(s)</span>' in client.get("/").get_data(as_text=True)


def test_rsvp_hidden_when_empty(client, monkeypatch):
    monkeypatch.setitem(config.EVENT, "rsvp", "")
    monkeypatch.setitem(config.EVENT, "rsvp_by", "")
    html = client.get("/").get_data(as_text=True)
    assert "Kindly reply to" not in html and "Please reply by" not in html
    monkeypatch.setitem(config.EVENT, "rsvp", "Thandi 082 000 0000")
    assert "Kindly reply to" in client.get("/").get_data(as_text=True)


def test_dress_code_shows(client):
    assert "expotition" in client.get("/").get_data(as_text=True)


def test_footer_credit_is_quiet_link(client):
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'Website by <a class="credit" href="https://ace-ops\.aceops\.workers\.dev/"[^>]*>Ace Khan</a>', html)


def test_hosts_filled(client):
    assert "Caitlin Caleb" in client.get("/").get_data(as_text=True)


def test_robots_blocks_indexing(client):
    assert "Disallow: /" in client.get("/robots.txt").get_data(as_text=True)


def test_upload_page_keeps_drive_fallback(client):
    assert config.EVENT["drive_url"] in client.get("/upload").get_data(as_text=True)


# ---------------------------------------------------------------- QR
def test_qr_svg(client):
    r = client.get("/qr.svg")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"


def test_qr_png(client):
    r = client.get("/qr.png")
    assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_follows_host(client):
    url = client.get("/api/qr-target", base_url="https://party.example").get_json()["url"]
    assert url == "https://party.example/upload"


def test_qr_respects_forwarded_https(client):
    url = client.get("/api/qr-target", headers={"X-Forwarded-Proto": "https",
                                                "X-Forwarded-Host": "shower.example"}).get_json()["url"]
    assert url == "https://shower.example/upload"


def test_public_url_override(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://final.example")
    assert client.get("/api/qr-target").get_json()["url"] == "https://final.example/upload"
    assert "https://final.example/upload" in client.get("/sign").get_data(as_text=True)


# ---------------------------------------------------------------- uploads
def test_upload_needs_token(client):
    r = client.post("/api/upload", data={"photos": (jpeg(), "a.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_upload_needs_files(client):
    token = client.post("/api/token").get_json()["token"]
    r = client.post("/api/upload", data={"token": token}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_upload_goes_to_pending_not_album(client):
    r = upload(client, n=2)
    assert r.get_json() == {"ok": True, "count": 2, "failed": 0, "status": "pending"}
    assert len(all_ids("pending")) == 2
    assert all_ids("approved") == []


def test_batch_is_capped(client):
    cap = config.EVENT["max_photos_per_guest"]
    r = upload(client, n=cap + 3)
    assert r.get_json()["count"] == cap


def test_unreadable_file_is_skipped(client):
    r = upload(client, files=[(io.BytesIO(b"not an image"), "x.jpg"), (jpeg(), "ok.jpg")])
    assert r.get_json()["count"] == 1 and r.get_json()["failed"] == 1


def test_heic_upload(client):
    pillow_heif = pytest.importorskip("pillow_heif")
    b = io.BytesIO()
    try:
        pillow_heif.from_pillow(Image.new("RGB", (320, 240), (200, 230, 250))).save(b, format="HEIF")
    except Exception as e:  # encoder missing in this build
        pytest.skip(f"no HEIF encoder: {e}")
    b.seek(0)
    r = upload(client, files=[(b, "IMG_0001.HEIC")])
    assert r.get_json()["count"] == 1


def test_pending_media_is_private(client):
    upload(client)
    pid = all_ids("pending")[0]
    assert client.get(f"/media/thumb/{pid}.jpg").status_code == 404
    assert client.get(f"/media/full/{pid}.jpg").status_code == 404


# ---------------------------------------------------------------- host desk
def test_wrong_pin_rejected(client):
    r = client.post("/host", data={"pin": "nope"})
    assert r.status_code == 200 and "isn" in r.get_data(as_text=True)
    assert client.post("/host/action", data={"id": "x", "action": "approve"}).status_code == 403


def test_host_routes_need_login(client):
    assert client.get("/host/zip").status_code == 403


def test_approve_publishes_photo(host):
    upload(host)
    pid = all_ids()[0]
    host.post("/host/action", data={"id": pid, "action": "approve"})
    public = A.app.test_client()
    assert pid in public.get("/photos").get_data(as_text=True)
    assert public.get(f"/media/thumb/{pid}.jpg").status_code == 200
    assert public.get(f"/media/full/{pid}.jpg").status_code == 200


def test_decline_keeps_photo_out(host):
    upload(host)
    pid = all_ids()[0]
    host.post("/host/action", data={"id": pid, "action": "reject"})
    assert all_ids("rejected") == [pid]
    assert pid not in A.app.test_client().get("/photos").get_data(as_text=True)


def test_delete_removes_files(host):
    upload(host)
    pid = all_ids()[0]
    host.post("/host/action", data={"id": pid, "action": "delete"})
    assert all_ids() == []
    assert not os.path.exists(os.path.join(A.FULL_DIR, f"{pid}.jpg"))
    assert not os.path.exists(os.path.join(A.THUMB_DIR, f"{pid}.jpg"))


def test_zip_has_only_approved(host):
    upload(host, n=2)
    a, b = all_ids()
    host.post("/host/action", data={"id": a, "action": "approve"})
    z = zipfile.ZipFile(io.BytesIO(host.get("/host/zip").data))
    names = z.namelist()
    assert len(names) == 1 and a[:6] in names[0]


def test_back_redirect_stays_on_site(host):
    upload(host)
    pid = all_ids()[0]
    r = host.post("/host/action", data={"id": pid, "action": "approve",
                                        "back": "https://evil.example/"})
    assert r.headers["Location"].endswith("/host")


def test_production_refuses_default_pin(client, monkeypatch):
    monkeypatch.setattr(config, "PRODUCTION", True)
    monkeypatch.setattr(config, "HOST_PIN", config.DEFAULT_PIN)
    r = client.post("/host", data={"pin": config.DEFAULT_PIN})
    assert r.status_code == 200 and "HOST_PIN" in r.get_data(as_text=True)


def test_pin_comes_from_env(monkeypatch):
    import importlib
    monkeypatch.setenv("HOST_PIN", "tigger-2026")
    cfg = importlib.reload(config)
    try:
        assert cfg.HOST_PIN == "tigger-2026"
    finally:
        monkeypatch.delenv("HOST_PIN")
        importlib.reload(config)


# ---------------------------------------------------------------- offline-first
# assets = anything a page LOADS: src="..." and <link href="..."> (plain <a> links are fine)
EXTERNAL = re.compile(r"""(?:\bsrc\s*=\s*["']|<link\b[^>]*\bhref\s*=\s*["'])(https?:)?//""", re.I)


TEXT_EXT = (".html", ".css", ".js", ".txt", ".svg")


def text_files():
    for folder in ("templates", "static"):
        for dirpath, _, names in os.walk(os.path.join(ROOT, folder)):
            for name in names:
                if name.endswith(TEXT_EXT) and not name.startswith("OFL"):
                    yield os.path.join(dirpath, name)


def test_no_external_assets_in_source():
    """No CDNs, web fonts or trackers: the venue wifi may be flaky."""
    files = list(text_files())
    assert files
    for path in files:
        text = open(path, encoding="utf-8").read()
        assert not EXTERNAL.search(text), f"external asset in {path}"
        assert "@import url(" not in text and "googleapis" not in text
        for m in re.finditer(r"url\(([^)]*)\)", text):
            target = m.group(1).strip("'\" ")
            assert not re.match(r"(https?:)?//", target), f"external url() in {path}"


@pytest.mark.parametrize("path", ["/", "/photos", "/upload", "/sign", "/host"])
def test_every_local_asset_resolves(client, path):
    """Every image, font and stylesheet a page uses is served by the app itself."""
    html = client.get(path).get_data(as_text=True)
    assets = set(re.findall(r'(?:src|href)="(/static/[^"]+|/qr\.(?:svg|png))"', html))
    css = client.get("/static/style.css").get_data(as_text=True)
    assets |= {"/static/" + u.strip("'\"") for u in re.findall(r"url\(((?:fonts|img)/[^)]+)\)", css)}
    assert assets
    for a in assets:
        assert client.get(a).status_code == 200, a


def test_storybook_illustrations_present(client):
    html = client.get("/").get_data(as_text=True)
    for img in ("hero.webp", "map.webp", "balloon.webp", "friends.webp"):
        assert f"/static/img/{img}" in html
    assert "In Which" in html and "100 Aker Wood" in html


@pytest.mark.parametrize("path", ["/", "/photos", "/upload", "/sign"])
def test_rendered_pages_have_no_trackers(client, path):
    html = client.get(path).get_data(as_text=True).lower()
    for bad in ("google-analytics", "gtag(", "googletagmanager", "fonts.googleapis",
                "cdn.jsdelivr", "unpkg.com", "cdnjs", "facebook.net"):
        assert bad not in html
