"""The static pages for the live Cloudflare Worker (tools/build_worker.py)."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import build_worker
    out = tmp_path_factory.mktemp("public")
    build_worker.OUT = str(out)
    build_worker.main("https://example-shower.workers.dev")
    return out


def read(built, name):
    return (built / name).read_text(encoding="utf-8")


def test_all_pages_built(built):
    for name in ("index.html", "photos.html", "upload.html", "host.html", "sign.html", "404.html",
                 "qr-hunny.svg", "robots.txt", "_headers", "static/style.css", "static/scrapbook.css",
                 "static/img/pot.webp"):
        assert (built / name).exists(), name


def test_no_external_resources(built):
    for name in ("index.html", "photos.html", "upload.html", "host.html", "sign.html"):
        html = read(built, name)
        assert not re.search(r'(src|<link[^>]+href)="https?://', html), name


def test_upload_page_takes_photos_and_videos(built):
    html = read(built, "upload.html")
    assert 'accept="image/*,video/*"' in html
    assert "/api/upload/start" in html and "drive.google.com" in html   # Drive fallback stays


def test_scrapbook_and_host_desk_are_api_driven(built):
    assert "/api/photos" in read(built, "photos.html")
    host = read(built, "host.html")
    assert "/api/host/login" in host and "/auth/google" in host


def test_sign_qr_points_at_live_upload_page(built):
    sign = read(built, "sign.html")
    assert "https://example-shower.workers.dev/upload" in sign
    assert "qr-hunny.svg" in sign


def test_setup_code_never_committed():
    toml = open(os.path.join(ROOT, "worker", "wrangler.toml")).read()
    assert re.search(r'SETUP_HASH = "[a-f0-9]{64}"', toml)
    assert "hunny-" not in toml


def test_uploads_work_before_google_is_connected(built):
    """Guests can upload straight away: the site keeps files until Google Photos is connected."""
    up = read(built, "upload.html")
    assert "isn't open" not in up
    assert "makeThumb" in up and "shrink(" in up
    host = read(built, "host.html")
    assert 'id="moveBtn"' in host and "/api/host/migrate" in host
    toml = open(os.path.join(ROOT, "worker", "wrangler.toml"), encoding="utf-8").read()
    assert 'binding = "MEDIA"' in toml and "REPLACE_WITH_KV_ID" in toml
