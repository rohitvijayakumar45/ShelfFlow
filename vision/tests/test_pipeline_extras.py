"""Motion proof, reference quality, colour cue, masking, face redaction and the uncertain-crop queue."""
import cv2
import numpy as np
import pytest
from backend.schemas import Candidate
from vision.capture import RollingBuffer, Frame
from vision.cues import reference_quality, ColourCue, colour_histogram
from vision.hands import MotionProof, annotate, overlap
from vision.identify import Match, UNKNOWN
from vision.privacy import FaceRedactor
from vision.references import UncertainQueue
from vision.segment import Masker, grabcut_mask

ROI = (100, 100, 300, 300)

def marker(mid, x, y):
    return {"id": mid, "corners": [[x - 5, y - 5], [x + 5, y - 5], [x + 5, y + 5], [x - 5, y + 5]]}

def pickup_story(markers_at_hand=(7,), stranger=(9,)):
    proof = MotionProof(ROI, start=0.0)
    item = (180, 180, 220, 230)
    for i in range(4):  # item sits on the tray, no hand
        proof.observe(i * 0.1, [(1, item)], [], [marker(m, 20, 20) for m in stranger])
    hand = (170, 170, 240, 250)
    for i in range(4, 8):  # hand grabs it; this hand's bracelet marker is next to the hand
        proof.observe(i * 0.1, [(1, item)], [hand], [marker(m, 245, 255) for m in markers_at_hand] + [marker(m, 20, 20) for m in stranger])
    for i in range(8, 20):  # item gone from the tray view
        proof.observe(i * 0.1, [], [], [marker(m, 20, 20) for m in stranger])
    return proof.summary()

def test_pickup_with_hand_contact_and_exit_sets_chain_flags():
    summary = pickup_story()
    track = summary["tracks"][1]
    assert summary["hand_seen"] and track["exited"] and not track["entered"] and track["contact_frames"] == 4
    assert track["markers"] == {7: 4}  # the stranger's marker far away is not linked to the hand
    c = Candidate(product_id="UNKNOWN", product_track="clip:1", hand_track="UNPROVEN", action="PICKUP", quantity=1, marker_ids=[7, 9])
    annotate([c], summary, "PICKUP")
    assert c.hand_product and c.trajectory and c.bracelet_hand and c.identity and c.marker_ids == [7] and c.temporal

def test_two_markers_at_one_hand_leave_identity_unproven():
    summary = pickup_story(markers_at_hand=(7, 8))
    c = Candidate(product_id="UNKNOWN", product_track="clip:x:coffee", hand_track="UNPROVEN", action="PICKUP", quantity=1)
    annotate([c], summary, "PICKUP")  # weight-driven candidate: the only moved track is used
    assert c.marker_ids == [7, 8] and not c.identity and c.trajectory

def test_item_sitting_still_is_not_a_pickup():
    proof = MotionProof(ROI, start=0.0)
    for i in range(10):
        proof.observe(i * 0.1, [(3, (180, 180, 220, 230))], [], [])
    track = proof.summary()["tracks"][3]
    assert not track["exited"] and not track["entered"] and not proof.summary()["hand_seen"]

def test_return_is_an_item_arriving_under_a_hand():
    proof = MotionProof(ROI, start=0.0)
    for i in range(5):
        proof.observe(i * 0.1, [], [], [])
    for i in range(5, 15):
        hand = [(150, 150, 240, 240)] if i < 9 else []
        proof.observe(i * 0.1, [(4, (170, 170, 210, 210))], hand, [])
    assert proof.summary()["tracks"][4]["entered"]

def test_overlap_is_relative_to_the_smaller_box():
    assert overlap((0, 0, 10, 10), (0, 0, 100, 100)) == 1.0 and overlap((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0

def textured_object(size=(120, 90), colour=(40, 60, 200)):
    rng = np.random.default_rng(1)
    image = rng.integers(90, 110, (160, 160, 3)).astype(np.uint8)
    h, w = size
    patch = np.full((h, w, 3), colour, np.uint8)
    patch[20:40] = (230, 230, 230)
    image[20:20 + h, 35:35 + w] = patch
    return image

def test_reference_quality_flags_blur_hand_and_edge():
    sharp = textured_object()
    good = reference_quality(sharp, "RESTOCK_AUTO", (40, 40, 120, 140), (0, 0, 300, 300))
    assert good["ok"] and good["score"] > 0
    assert "BLURRED" in reference_quality(cv2.GaussianBlur(sharp, (31, 31), 0))["reasons"]
    skin = np.full((80, 80, 3), (120, 150, 200), np.uint8)  # BGR skin tone
    skin[::8] = (20, 20, 20)
    assert "HAND_COVERED" in reference_quality(skin)["reasons"]
    assert "CUT_OFF_AT_TRAY_EDGE" in reference_quality(sharp, "RESTOCK_AUTO", (0, 40, 120, 140), (0, 0, 300, 300))["reasons"]
    assert "CUT_OFF_AT_TRAY_EDGE" not in reference_quality(sharp, "TEACH", (0, 40, 120, 140), (0, 0, 300, 300))["reasons"]

def test_colour_breaks_only_a_near_tie():
    red, blue = np.full((40, 40, 3), (30, 30, 200), np.uint8), np.full((40, 40, 3), (200, 40, 30), np.uint8)
    cue = ColourCue()
    cue.add("mint", "live:a:orig", blue)
    cue.add("lime", "live:b:orig", red)
    tie = Match(UNKNOWN, 0.8, 0.01, "lime", "mint", "lime")
    resolved = cue.refine(red, tie, 0.7)
    assert resolved.product_id == "lime" and resolved.via == "colour"
    assert cue.refine(red, Match(UNKNOWN, 0.5, 0.2, "x", "mint", "lime"), 0.7).product_id == UNKNOWN  # too dissimilar: stays unknown
    cue.remove_sources("live:b")
    assert cue.refine(red, tie, 0.7).product_id == UNKNOWN
    assert abs(colour_histogram(red).sum() - 1) < 1e-5

def test_grabcut_masks_background_to_grey():
    image = textured_object()
    mask = grabcut_mask(image)
    assert mask is not None and 0.2 < np.count_nonzero(mask) / mask.size < 0.9
    out, info = Masker("grabcut")(image)
    assert info["mask"] == "grabcut" and out.shape[0] <= image.shape[0]
    flat = np.full((60, 60, 3), 100, np.uint8)
    assert Masker("grabcut")(flat)[1]["mask"] in ("rejected", "failed")  # nothing to segment: plain crop kept
    assert Masker("none")(flat)[0] is flat

def frame(jpeg, seq):
    return Frame(jpeg, seq, "boot", seq, 0.0, float(seq), 1.0, 10, 10, 5.0, 100.0 + seq, annotations={"trays": {}})

def test_evidence_without_faces_stays_byte_identical(tmp_path):
    ok, buf = cv2.imencode(".jpg", textured_object())
    jpeg = buf.tobytes()
    buffer = RollingBuffer()
    buffer.append(frame(jpeg, 1))
    refs = buffer.preserve(tmp_path, "ev", 0, 5, redact=FaceRedactor())
    assert (tmp_path / refs[0]).read_bytes() == jpeg
    meta = (tmp_path / refs[0].replace(".jpg", ".json")).read_text()
    assert '"faces_redacted": 0' in meta and '"original_bytes": true' in meta and '"annotations"' in meta

class FakeApi:
    def __init__(self, status=200):
        self.posts, self.status = [], status
    def post(self, path, json):
        self.posts.append((path, json))
        return type("R", (), {"status_code": self.status})()

def test_uncertain_queue_asks_once_per_track_and_rate_limits():
    api = FakeApi()
    queue = UncertainQueue(api, threshold=0.7, margin=0.05, per_minute=2)
    crop = textured_object()
    near_tie = Match(UNKNOWN, 0.75, 0.01, "lime", "mint", "lime")
    assert queue.observe("c:1", crop, near_tie, "tray-a", now=0)
    assert not queue.observe("c:1", crop, near_tie, now=1)  # same track
    assert not queue.observe("c:2", crop, Match("mint", 0.9, 0.2, "lime", "mint", "lime"), now=1)  # confident
    assert not queue.observe("c:3", crop, Match(UNKNOWN, 0.3, 0.0, None, "mint", None), now=1)  # far from anything
    assert queue.observe("c:4", crop, Match(UNKNOWN, 0.65, 0.1, "lime", "mint", "lime"), now=2)  # just under threshold
    assert not queue.observe("c:5", crop, near_tie, now=3)  # rate limit
    assert queue.flush() == 2 and api.posts[0][0] == "/devices/vision-1/uncertain" and api.posts[0][1]["tray_id"] == "tray-a"
    down = UncertainQueue(FakeApi(503), 0.7, 0.05)
    down.observe("c:9", crop, near_tie)
    assert down.flush() == 0 and len(down.pending) == 1  # kept for retry

def test_unknown_embedder_is_rejected_without_download():
    from vision.embedders import make_embedder
    with pytest.raises(ValueError):
        make_embedder("not-a-model")

def test_hand_labels_become_class_one_and_are_never_products(tmp_path):
    from tools.dataset.recognition import crop_dataset, object_crops, autolabel_hands, make_yaml
    root = tmp_path / "ds"
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    cv2.imwrite(str(root / "images" / "train" / "f1.jpg"), textured_object())
    # product 0 inside the left tray; hand (source id 3) reaching in across the tray edge
    (root / "labels" / "train" / "f1.txt").write_text("0 0.3 0.4 0.2 0.3\n3 0.55 0.5 0.3 0.3\n")
    m = crop_dataset(root, {"tray-a": [0, 0, 0.6, 1]}, tmp_path / "crops", single_class=True, hand_class=3)
    rows = (tmp_path / "crops" / "labels" / "train" / "f1__tray-a.txt").read_text().split("\n")
    assert m["skipped_partial"] == 0 and {r.split()[0] for r in rows if r} == {"0", "1"}
    counts = object_crops(root, ["sanitizer", "vicks", "sunscreen", "hand"], tmp_path / "objects")
    assert counts == {"train": {"sanitizer": 1}}

    class Finder:
        def detect(self, image):
            return [(10, 10, 60, 70), (0, 0, 3, 3)]  # second box too small
    out = autolabel_hands(root, tmp_path / "auto", 3, finder=Finder())
    labels = (tmp_path / "auto" / "labels" / "train" / "f1.txt").read_text().split("\n")
    assert out == {"images": 1, "hands": 1} and labels[0].startswith("0 ") and labels[1].startswith("3 ")
    assert (root / "labels" / "train" / "f1.txt").read_text().count("\n") == 2  # originals untouched
    make_yaml(tmp_path / "crops", [], tmp_path / "d.yaml", True, with_hands=True)
    assert "1: hand" in (tmp_path / "d.yaml").read_text()
