"""Few-shot learning helpers: placement crops, augmentation, diversity, look-alike probe."""
import numpy as np
import pytest
from vision.fewshot import change_box, crop_box, augment, DiversityFilter, LookalikeGuard
from vision.identify import Gallery, UNKNOWN

def scene(seed=0):
    rng = np.random.default_rng(seed)
    import cv2
    img = rng.integers(70, 120, (240, 320, 3)).astype(np.uint8)
    return cv2.GaussianBlur(img, (9, 9), 0)

def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)

def test_change_box_finds_newly_placed_item_despite_exposure_shift():
    before = scene()
    after = before.copy()
    after[60:150, 100:170] = (30, 140, 220)          # the new item
    after[60:150, 120:125] = (250, 250, 250)         # label stripe splits the blob
    after = np.clip(after.astype(np.float32) * 1.15, 0, 255).astype(np.uint8)  # auto-exposure brightened
    found = change_box(before, after, roi=(0, 0, 320, 240))
    assert found["reason"] == "OK" and iou(found["box"], (100, 60, 170, 150)) > 0.8
    crop = crop_box(after, found["box"])
    assert crop.shape[0] > 90 and crop.shape[1] > 70

def test_change_box_respects_roi_and_reports_no_change():
    before = scene(); after = before.copy(); after[10:60, 10:60] = 255  # change outside the tray ROI
    assert change_box(before, after, roi=(150, 100, 320, 240))["reason"] == "NO_CHANGE"

def test_change_box_refuses_whole_view_change():
    before = scene(1); moved = before.copy()
    moved[:, :160] = 220; moved[:, 160:] = 15  # camera bumped / view replaced: most of the tray differs
    assert change_box(before, moved)["reason"] == "GLOBAL_CHANGE"

def test_augment_makes_small_variants():
    views = augment(scene()[50:150, 50:150])
    assert [n for n, _ in views] == ["orig", "flip", "tight", "bright", "dark", "rot-8", "rot8"]
    assert all(v.ndim == 3 and v.size for _, v in views)

def test_diversity_filter_drops_near_duplicates_and_caps():
    f = DiversityFilter(max_refs=3, max_similarity=0.96)
    a = np.array([1, 0, 0], np.float32)
    assert f.accept("p", a) and not f.accept("p", a)
    assert f.accept("p", np.array([0, 1, 0], np.float32)) and f.accept("p", np.array([0, 0, 1], np.float32))
    assert not f.accept("p", np.array([0.6, 0.6, 0.53], np.float32))  # cap reached

def lookalike_gallery(separable=True, seed=0):
    """Two products sharing most appearance (same brand), one distinct product."""
    rng = np.random.default_rng(seed)
    dim = 32
    base, distinct = rng.normal(size=dim), rng.normal(size=dim)
    flavour = rng.normal(size=dim)
    g = Gallery("test")
    def vecs(center, n, tag):
        out = center + rng.normal(scale=0.08, size=(n, dim))
        return out / np.linalg.norm(out, axis=1, keepdims=True), [f"live:{tag}{i // 3}:{i % 3}" for i in range(n)]
    for product, center in (("lime", base + 0.25 * flavour), ("mint", base + (0.25 if not separable else -0.25) * flavour), ("soap", distinct)):
        v, src = vecs(center, 24, product)
        g.add(product, v, src)
    return g, base, flavour

def test_lookalike_guard_finds_pairs_and_resolves_with_validated_probe():
    g, base, flavour = lookalike_gallery()
    guard = LookalikeGuard(pair_threshold=0.8).fit(g)
    assert [(p["a"], p["b"]) for p in guard.pairs] == [("lime", "mint")]
    group = guard.group_of("lime")
    assert group["usable"] and group["cv_accuracy"] >= 0.9
    q = base + 0.25 * flavour; q /= np.linalg.norm(q)
    match = g.identify(q, threshold=0.5, margin=0.2)  # the gallery alone refuses: too close to mint
    assert match.product_id == UNKNOWN and {match.top, match.second} == {"lime", "mint"}
    refined = guard.refine(q, match, threshold=0.5)
    assert refined.product_id == "lime" and refined.via == "lookalike-probe"

def test_lookalike_guard_stays_unknown_when_probe_cannot_separate():
    g, base, flavour = lookalike_gallery(separable=False)
    guard = LookalikeGuard(pair_threshold=0.8).fit(g)
    group = guard.group_of("lime")
    assert group and not group["usable"] and "different trays" in group["advice"]
    q = base + 0.25 * flavour; q /= np.linalg.norm(q)
    match = g.identify(q, threshold=0.5, margin=0.2)
    assert guard.refine(q, match, threshold=0.5).product_id == UNKNOWN
    assert "weights" not in str(guard.report())

def test_probe_never_overrides_low_similarity_or_unrelated_products():
    g, base, flavour = lookalike_gallery()
    guard = LookalikeGuard(pair_threshold=0.8).fit(g)
    q = base + 0.25 * flavour; q /= np.linalg.norm(q)
    match = g.identify(q, threshold=0.999, margin=0.2)  # rejected for low similarity, not ambiguity
    assert guard.refine(q, match, threshold=0.999).product_id == UNKNOWN

def test_gallery_remove_sources_drops_one_reference_and_its_views():
    g = Gallery("t")
    g.add("p", np.eye(3, dtype=np.float32), ["live:r1:orig", "live:r1:flip", "live:r2:orig"])
    assert g.remove_sources("live:r1:") == 2 and g.sources == ["live:r2:orig"]
