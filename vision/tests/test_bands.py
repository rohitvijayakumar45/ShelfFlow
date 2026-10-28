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


def two_shoppers(stripe_box, circle_box, steps=8):
    """Two items on one tray, each lifted by a different bracelet-wearing hand in the same window."""
    from vision.hands import MotionProof
    proof = MotionProof((0, 0, 320, 240), start=0.0)
    for k in range(steps):
        items = [Detection("sanitizer", 0.9, (40, 80, 80, 150), 3), Detection("vicks", 0.9, (220, 80, 260, 150), 4)] if k < steps - 3 else []
        products, bands = split_bands(items + [Detection("band-stripe", 0.8, stripe_box, 5), Detection("band-circle", 0.8, circle_box, 6)])
        markers, hands = band_evidence(bands, (0, 0))
        proof.observe(k * 0.2, [(d.track_id, d.box) for d in products], hands, markers, f"f{k}")
    return proof.summary()


def test_two_shoppers_get_one_candidate_each_with_their_own_bracelet():
    from vision.hands import track_candidates
    summary = two_shoppers((75, 120, 105, 135), (255, 120, 285, 135))
    found = track_candidates(summary, {3: "sanitizer", 4: "vicks"}, {}, "clip", "PICKUP")
    assert [(c.product_id, c.marker_ids, c.identity) for c in found] == [("sanitizer", [200], True), ("vicks", [201], True)]


def test_hands_together_leave_both_items_unproven():
    from vision.hands import track_candidates
    summary = two_shoppers((120, 100, 180, 130), (125, 105, 185, 135))   # both hands reach over both items
    found = track_candidates(summary, {3: "sanitizer", 4: "vicks"}, {}, "clip", "PICKUP")
    assert found and not any(c.identity for c in found)


def test_per_item_split_needs_every_moved_item_named():
    from vision.hands import track_candidates
    summary = two_shoppers((75, 120, 105, 135), (255, 120, 285, 135))
    assert track_candidates(summary, {3: "sanitizer"}, {}, "clip", "PICKUP") is None


def test_one_bracelet_named_as_both_patterns_keeps_the_more_confident_one():
    from vision.detector import Detection
    from vision.hands import split_bands
    both = [Detection("band-circle", 0.81, (100, 100, 140, 130), 1), Detection("band-stripe", 0.42, (102, 101, 141, 131), 2),
            Detection("vicks", 0.95, (200, 40, 260, 120), 3), Detection("band-stripe", 0.77, (10, 10, 40, 40), 4)]
    products, bands = split_bands(both)
    assert [d.sku for d in products] == ["vicks"]
    assert sorted(d.sku for d in bands) == ["band-circle", "band-stripe"] and all(d.confidence > 0.7 for d in bands)


def one_hand_pickup(labels):
    """One hand takes the sanitizer; the model names its band `labels[k]` in frame k."""
    from vision.hands import MotionProof
    proof = MotionProof((0, 0, 320, 240), start=0.0)
    for k, sku in enumerate(labels):
        items = [Detection("sanitizer", 0.9, (40, 80, 80, 150), 3)] if k < len(labels) - 3 else []
        products, bands = split_bands(items + [Detection(sku, 0.8, (55, 120, 85, 135), 5)])
        markers, hands = band_evidence(bands, (0, 0))
        proof.observe(k * 0.2, [(d.track_id, d.box) for d in products], hands, markers, f"f{k}")
    return proof.summary()


def test_one_bracelet_named_both_ways_is_not_trusted():
    from vision.hands import annotate
    from backend.schemas import Candidate
    flip = ["band-circle", "band-stripe"] * 6                        # 50/50 on the same wrist
    c = Candidate(product_id="sanitizer", product_track="clip:3", hand_track="h", action="PICKUP", quantity=1)
    [c] = annotate([c], one_hand_pickup(flip), "PICKUP")
    assert not c.identity and sorted(c.marker_ids) == [200, 201]
    steady = ["band-stripe"] * 10 + ["band-circle"]                   # one stray frame
    c = Candidate(product_id="sanitizer", product_track="clip:3", hand_track="h", action="PICKUP", quantity=1)
    [c] = annotate([c], one_hand_pickup(steady), "PICKUP")
    assert c.identity and c.marker_ids == [200]


def test_bracelet_confirmed_after_n_frames_in_a_row_once_per_appearance():
    from vision.hands import BandConfirmer
    c = BandConfirmer(frames=5, gap=10)
    assert [c.update([200]) for _ in range(4)] == [[], [], [], []]
    assert c.update([200]) == [200]                          # 5th frame in a row
    assert all(c.update([200]) == [] for _ in range(20))     # still in view: not again
    c.update([201]); c.update([200])                         # a stray frame of the other pattern breaks a run
    assert [c.update([201]) for _ in range(4)] == [[], [], [], []] and c.update([201]) == [201]
    for _ in range(10):
        c.update([])                                         # out of view long enough: re-armed
    assert [c.update([200]) for _ in range(5)][-1] == [200]
