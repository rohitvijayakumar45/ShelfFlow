"""Tray loading helpers: LCD guidance relayed to the shelf controller, and staff-chosen catalogue photos."""
import base64
import json
import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from backend import main, references as refs
from backend.db import SessionLocal
from tools.usb_bridge import consume_lcd_request, lcd_command


def test_lcd_command_is_sanitised_and_bounded():
    assert lcd_command({"seconds": 30, "lines": ["PLACE 2/6", "Vicks #"]}) == "lcd 30 PLACE 2/6|Vicks #"
    # A separator, control characters or overlong text can never become a second controller command.
    assert lcd_command({"seconds": 5, "lines": ["a|b\ntareempty", "x" * 40]}) == "lcd 5 abtareempty|" + "x" * 16
    assert lcd_command({"seconds": 0}) == "lcd off"
    assert lcd_command({"seconds": 9999, "lines": ["x"]}) is None and lcd_command({"seconds": "5"}) is None


class Port:
    def __init__(self):
        self.sent = b""

    def write(self, data):
        self.sent += data


def test_bridge_relays_fresh_requests_once_and_drops_stale_ones(tmp_path):
    import time
    port = Port()
    (tmp_path / "lcd-request.json").write_text(json.dumps({"seconds": 20, "lines": ["PLACE 1/3", "Sanitizer"], "created_s": time.time()}))
    consume_lcd_request(tmp_path, port)
    consume_lcd_request(tmp_path, port)                       # consumed: never replayed
    assert port.sent == b"\nlcd 20 PLACE 1/3|Sanitizer\n"
    (tmp_path / "lcd-request.json").write_text(json.dumps({"seconds": 20, "lines": ["old"], "created_s": time.time() - 60}))
    consume_lcd_request(tmp_path, port)
    assert port.sent == b"\nlcd 20 PLACE 1/3|Sanitizer\n"


def test_backend_writes_the_request_for_the_bridge_in_hardware_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MODE", "hardware")
    monkeypatch.setenv("USB_EVENT_ROOT", str(tmp_path))
    r = main.tray_lcd("tray-a", main.LcdGuide(lines=["PLACE 1/2", "Oat milk on #|x"], seconds=60), user={"sub": "staff"})
    written = json.loads((tmp_path / "lcd-request.json").read_text())
    assert r["sent_to_shelf"] and written["lines"] == ["PLACE 1/2", "Oat milk on #x"] and written["seconds"] == 60


def photo(width=1600, height=1200):
    ok, data = cv2.imencode(".png", np.full((height, width, 3), 200, np.uint8))
    return data.tobytes()


def test_thumbnail_is_shrunk_saved_and_preferred(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ROOT", str(tmp_path))
    r = main.product_thumbnail("oat", main.Thumbnail(image_base64="data:image/png;base64," + base64.b64encode(photo()).decode()), user={"sub": "staff"})
    assert r["thumbnail"] and refs.has_thumbnail("oat")
    saved = cv2.imread(str(tmp_path / "catalogue" / "thumb-oat.jpg"))
    assert max(saved.shape[:2]) == 640
    with SessionLocal() as db:
        assert refs.best_image(db, "oat") == "thumb-oat.jpg"
    main.product_thumbnail_remove("oat", user={"sub": "staff"})
    assert not refs.has_thumbnail("oat")


def test_thumbnail_refuses_non_images_and_unknown_products(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ROOT", str(tmp_path))
    with pytest.raises(HTTPException):
        main.product_thumbnail("oat", main.Thumbnail(image_base64=base64.b64encode(b"not an image at all").decode()), user={"sub": "staff"})
    with pytest.raises(HTTPException):
        main.product_thumbnail("no-such-product", main.Thumbnail(image_base64=base64.b64encode(photo(20, 20)).decode()), user={"sub": "staff"})
