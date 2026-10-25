"""Staff app support: review items carry their capture time; the live camera stream fails cleanly."""
from fastapi.testclient import TestClient
from backend import service as s
from backend.auth import issue
from backend.main import app
from backend.tests.test_store import enter, interaction

STAFF = {"Authorization": "Bearer " + issue("staff", "tester")}


def test_review_items_carry_capture_time():
    _, marker, _ = enter()
    event = interaction(marker=marker)
    event.weight.post_g += 300  # weight mismatch: goes to review
    s.ingest(event)
    with TestClient(app) as c:
        pending = [h for h in c.get("/staff/state", headers=STAFF).json()["hypotheses"] if h["status"] == "PENDING_REVIEW"]
    assert pending and pending[0]["captured"] == event.capture_end


def test_camera_stream_needs_staff_and_reports_offline_camera(monkeypatch):
    monkeypatch.setenv("CAMERA_URL", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setenv("RAW_CAMERA_URL", "http://127.0.0.1:9")
    with TestClient(app) as c:
        assert c.get("/staff/camera/stream").status_code == 403
        for view in ("detections", "raw"):
            r = c.get(f"/staff/camera/stream?view={view}", headers=STAFF)
            assert r.status_code == 503 and "Camera stream unavailable" in r.json()["detail"]
        assert c.get("/staff/camera/stream?view=other", headers=STAFF).status_code == 422
