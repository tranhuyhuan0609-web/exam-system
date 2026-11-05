from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_server_time_is_utc_iso():
    r = client.get("/api/time")
    assert r.status_code == 200
    ts = datetime.fromisoformat(r.json()["server_time"])
    assert ts.tzinfo is not None
