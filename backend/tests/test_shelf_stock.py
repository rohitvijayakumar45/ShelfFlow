"""Catalogue stock from the live camera: seen on the shelf = 1 in stock, otherwise out of stock."""
from backend import main


def stock(monkeypatch, predictions, known, available=True):
    monkeypatch.setattr(main, "preintegration_camera", lambda user, timeout=2: {"available": available, "predictions": predictions})
    monkeypatch.setattr(main, "recognised_products", lambda: known)
    return {i["product_id"]: i for i in main.shelf_stock(user={"sub": "staff"})["items"]}


def test_seen_products_are_in_stock_and_the_rest_out(monkeypatch):
    main._shelf_seen.clear()
    items = stock(monkeypatch, [{"product": "oat"}, {"product": "UNKNOWN"}], ["oat", "coffee", "band-stripe"])
    assert set(items) == {"oat", "coffee"}
    assert items["oat"]["stock"] == 1 and items["coffee"]["stock"] == 0
    assert "band-stripe" not in items                    # bracelet patterns are never products


def test_brief_occlusion_keeps_stock_and_camera_offline_keeps_last_known(monkeypatch):
    main._shelf_seen.clear()
    stock(monkeypatch, [{"product": "oat"}], ["oat"])
    assert stock(monkeypatch, [], ["oat"])["oat"]["stock"] == 1          # a hand covered it for a moment
    main._shelf_seen["oat"] -= main.SHELF_SEEN_HOLD_S + 1
    assert stock(monkeypatch, [], ["oat"], available=False)["oat"]["stock"] == 0


def test_without_a_product_model_the_whole_catalogue_is_listed(monkeypatch):
    main._shelf_seen.clear()
    items = stock(monkeypatch, [], None)
    assert {"oat", "coffee"} <= set(items) and all(i["stock"] == 0 for i in items.values())


def test_items_weighed_onto_the_tray_are_counted_exactly(monkeypatch):
    from backend.tests.test_units import mixed as _  # noqa: F401
    from backend.tests.test_units import load, unit_tray
    main._shelf_seen.clear()
    load_id = unit_tray()
    load(load_id, "coffee", 331.4), load(load_id, "coffee", 330.9)
    items = stock(monkeypatch, [], ["coffee", "oat"])
    assert items["coffee"]["stock"] == 2 and items["coffee"]["source"] == "scale"
    assert items["oat"]["stock"] == 0 and items["oat"]["source"] == "camera"
