"""Bracelet bands from a Train-products model: identity evidence, never products."""
from vision.detector import Detection
from vision.hands import MotionProof, annotate, band_evidence, split_bands
from vision.association import review_weight_candidates


def test_bands_leave_the_product_list_and_become_markers_with_a_hand_region():
    found = [Detection("sanitizer", 0.9, (100, 60, 140, 150), 1), Detection("band-stripe", 0.8, (150, 120, 180, 135), 7),
             Detection("band-circle", 0.7, (20, 20, 40, 30), 8)]
    products, bands = split_bands(found)
    assert [d.sku for d in products] == ["sanitizer"] and len(bands) == 2
    markers, hands = band_evidence(bands, (10, 5))
    assert [m["id"] for m in markers] == [200, 201]
    assert markers[0]["corners"][0] == (160, 125)          # crop -> full-frame
    x1, y1, x2, y2 = hands[0]
    assert x1 < 160 and x2 > 190 and y1 < 125 and y2 > 140   # the hand region surrounds the band


def pickup(band, steps=8):
    """Item at the band's hand, then lifted out of view while the band stays visible."""
    proof = MotionProof((0, 0, 320, 240), start=0.0)
    for k in range(steps):
        products, bands = split_bands(([Detection("sanitizer", 0.9, (130, 80, 170, 150), 3)] if k < steps - 3 else []) + [band])
        markers, hands = band_evidence(bands, (0, 0))
        proof.observe(k * 0.2, [(d.track_id, d.box) for d in products], hands, markers, f"f{k}")
    return proof.summary()


def test_band_at_the_item_names_the_bracelet_of_a_pickup():
    summary = pickup(Detection("band-circle", 0.8, (165, 120, 195, 135), 5))
    candidates = review_weight_candidates(["sanitizer"], [], "clip", "PICKUP")
    annotate(candidates, summary, "PICKUP")
    c = candidates[0]
    assert c.product_id == "sanitizer" and c.marker_ids == [201] and c.identity and c.bracelet_hand and c.trajectory


def test_band_far_from_the_item_proves_nothing():
    summary = pickup(Detection("band-circle", 0.8, (5, 200, 25, 210), 5))
    candidates = review_weight_candidates(["sanitizer"], [], "clip", "PICKUP")
    annotate(candidates, summary, "PICKUP")
    assert candidates[0].marker_ids == [] and not candidates[0].identity
