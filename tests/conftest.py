import os
import sys
import tempfile

import pytest

# Isolate every test run in a throwaway data folder BEFORE the app is imported.
_TMP = tempfile.mkdtemp(prefix="shower-test-")
os.environ["DATA_DIR"] = _TMP
for k in ("HOST_PIN", "SECRET_KEY", "PUBLIC_URL"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import config  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    conn = app_module.sqlite3.connect(app_module.DB_PATH)
    conn.execute("DELETE FROM photos")
    conn.execute("DELETE FROM tokens")
    conn.commit()
    conn.close()
    for d in (app_module.FULL_DIR, app_module.THUMB_DIR):
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
    yield


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def host(client):
    r = client.post("/host", data={"pin": config.HOST_PIN})
    assert r.status_code == 302
    return client
