"""Video-to-detector training: record a clip per product -> SAM 2.1 boxes -> blur/outlier filter -> YOLO26n.

One recording session holds the raw JPEG frames of one clip (original camera bytes, never
re-encoded) plus frames.json with, per frame, its sharpness, SAM box and review flags.
A product session's object is marked once (a click or a box on any frame); SAM 2.1 tracks
it forwards and backwards through the clip. Background sessions (empty tray) become
negative images. Training builds a YOLO dataset from all included frames and fine-tunes
COCO-pretrained YOLO26n with one class per product.

`python -m vision.training train ...` is the training subprocess started by tools.trainer;
it reports progress to progress.json and stops cleanly between batches when STOP exists.
"""
import argparse
import json
import math
import os
import shutil
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "training"
MODELS = ROOT / "models"
YOLO_BASE = "yolo26n.pt"
SAM_MODELS = {"fast": "sam2.1_t.pt", "precise": "sam2.1_b.pt"}
MAX_LABEL_FRAMES = 900   # frames SAM tracks per session; longer clips are strided
CHUNK = 15               # consecutive frames per split chunk (~1 s), so val never neighbours train
VAL_EVERY = 5            # every 5th chunk is validation (20 %)


def write_json(path, value):
    """Atomic JSON write: readers (the web page, the service) never see half a file."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------- Frame quality ----------------

def sharpness(jpeg):
    """Variance of the Laplacian on a half-size grayscale decode (~2 ms for 640x480). Motion blur lowers it."""
    import cv2
    gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_2)
    if gray is None:
        return 0.0
    return round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1)


def auto_blur_threshold(scores):
    """Relative threshold: a clip's sharp frames set the bar, so lighting and scene do not matter.
    Frames below 35 % of the 90th-percentile sharpness are motion-blurred in practice."""
    scores = [s for s in scores if s is not None]
    if not scores:
        return 0.0
    return round(max(8.0, 0.35 * float(np.percentile(scores, 90))), 1)


BOX_FLAGS = ("no_object", "tiny", "huge", "jump", "skipped")


def flag_boxes(frames, width, height):
    """Flags from SAM boxes. jump = area far from its neighbours' median (SAM briefly grabbed a hand
    or the background); tiny/huge = implausible size; no_object = SAM lost the item."""
    area = []
    for f in frames:
        b = f.get("box")
        area.append(((b[2] - b[0]) * (b[3] - b[1]) / (width * height)) if b else None)
    for k, f in enumerate(frames):
        flags = [x for x in f.get("flags", []) if x not in BOX_FLAGS]
        if f.get("skipped"):
            flags.append("skipped")
        elif area[k] is None:
            flags.append("no_object")
        else:
            if area[k] < 0.002:
                flags.append("tiny")
            if area[k] > 0.85:
                flags.append("huge")
            near = [a for a in area[max(0, k - 6):k + 7] if a]
            median = float(np.median(near)) if near else area[k]
            if median and not (1 / 2.2 <= area[k] / median <= 2.2):
                flags.append("jump")
        f["flags"] = flags
    return frames


def included(frame, threshold, kind):
    """Manual choice wins; otherwise sharp enough and (for products) a plausible SAM box.
    Mirrored in apps/shared/Training.tsx so the review page updates without a round trip."""
    if frame.get("manual") is not None:
        return bool(frame["manual"])
    if frame.get("sharp", 0) < threshold:
        return False
    if kind == "background":
        return True
    return not any(flag in BOX_FLAGS for flag in frame.get("flags", []))


# ---------------- Sessions ----------------

class Session:
    def __init__(self, path):
        self.path = Path(path)
        self.meta = read_json(self.path / "meta.json", {})
        self.frames = read_json(self.path / "frames.json", [])

    @property
    def id(self):
        return self.path.name

    def frame_path(self, index):
        return self.path / "frames" / f"{index:06d}.jpg"

    def save(self):
        write_json(self.path / "frames.json", self.frames)
        write_json(self.path / "meta.json", self.meta)

    def threshold(self):
        value = self.meta.get("blur_threshold")
        return auto_blur_threshold([f.get("sharp") for f in self.frames]) if value is None else value

    def included(self):
        threshold, kind = self.threshold(), self.meta.get("kind", "product")
        return [f for f in self.frames if included(f, threshold, kind)]

    def summary(self):
        threshold = self.threshold()
        blurry = sum(1 for f in self.frames if f.get("sharp", 0) < threshold)
        return {**self.meta, "id": self.id, "frame_count": len(self.frames), "blurry": blurry,
                "flagged": sum(1 for f in self.frames if any(x in BOX_FLAGS for x in f.get("flags", []))),
                "included": len(self.included()), "threshold": threshold,
                "auto_threshold": auto_blur_threshold([f.get("sharp") for f in self.frames])}


def sessions(root=None):
    root = Path(root or DATA / "sessions")
    if not root.is_dir():
        return []
    return [Session(p) for p in sorted(root.iterdir(), reverse=True) if (p / "meta.json").is_file()]


# ---------------- SAM 2.1 labelling ----------------

def _write_clip(session, order, path):
    """SAM's video predictor reads a video file; MJPG AVI keeps each frame near-lossless."""
    import cv2
    first = cv2.imdecode(np.fromfile(session.frame_path(order[0]), np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 15, (w, h))
    try:
        for index in order:
            image = cv2.imdecode(np.fromfile(session.frame_path(index), np.uint8), cv2.IMREAD_COLOR)
            if image.shape[:2] != (h, w):
                image = cv2.resize(image, (w, h))
            out.write(image)
    finally:
        out.release()


def resolve_device(device="auto"):
    if device != "auto":
        return device
    try:
        import torch
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def label_session(session, prompt, quality="fast", device="auto", progress=lambda done, total: None, cancelled=lambda: False):
    """Track the marked product through the clip with SAM 2.1, forwards and backwards from the marked frame.

    prompt: {"frame": i, "point": [x, y]} or {"frame": i, "box": [x1, y1, x2, y2]} in image pixels.
    """
    from ultralytics.models.sam import SAM2VideoPredictor
    device = resolve_device(device)
    weights = MODELS / SAM_MODELS[quality]
    MODELS.mkdir(parents=True, exist_ok=True)
    n = len(session.frames)
    start = int(prompt["frame"])
    if not 0 <= start < n:
        raise ValueError("Marked frame is outside the clip")
    stride = max(1, math.ceil(n / MAX_LABEL_FRAMES))
    forward = list(range(start, n, stride))
    backward = list(range(start, -1, -stride))
    passes = [forward] + ([backward] if len(backward) > 1 else [])
    kwargs = {"bboxes": [prompt["box"]]} if prompt.get("box") else {"points": [prompt["point"]], "labels": [1]}
    total, done = len(forward) + len(backward) - 1, 0
    boxes = {}
    work = session.path / "work"
    work.mkdir(exist_ok=True)
    try:
        for number, order in enumerate(passes):
            clip = work / f"pass{number}.avi"
            _write_clip(session, order, clip)
            predictor = SAM2VideoPredictor(overrides=dict(model=str(weights), task="segment", mode="predict", imgsz=1024,
                                                          conf=0.25, device=device, save=False, verbose=False))
            for k, result in enumerate(predictor(source=str(clip), stream=True, **kwargs)):
                index = order[k]
                if index not in boxes or boxes[index] is None:
                    xyxy = result.boxes.xyxy if result.boxes is not None else None
                    boxes[index] = [round(float(v), 1) for v in xyxy[0].tolist()] if xyxy is not None and len(xyxy) else None
                if not (number == 1 and k == 0):
                    done += 1
                progress(done, total)
                if cancelled():
                    raise InterruptedError("Labelling cancelled")
            del predictor
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
    for index, frame in enumerate(session.frames):
        frame["box"] = boxes.get(index)
        frame["skipped"] = index not in boxes
    flag_boxes(session.frames, session.meta["width"], session.meta["height"])
    session.meta.update(status="LABELLED", prompt=prompt, sam=SAM_MODELS[quality], labelled=time.time())
    session.save()
    return session


# ---------------- Dataset ----------------

def _link(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def build_dataset(chosen, out, max_per_session=400):
    """YOLO dataset from included frames. Split by ~1 s chunks so near-identical neighbours never
    straddle train/val. Long clips are thinned evenly to max_per_session frames."""
    out = Path(out)
    product_sessions = [s for s in chosen if s.meta.get("kind", "product") == "product" and s.meta.get("status") == "LABELLED"]
    classes = sorted({s.meta["product_id"] for s in product_sessions if s.included()})
    if not classes:
        raise ValueError("No labelled product frames are included yet")
    index = {pid: k for k, pid in enumerate(classes)}
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    counts = {pid: {"train": 0, "val": 0} for pid in classes}
    counts["background"] = {"train": 0, "val": 0}
    for s in chosen:
        kind = s.meta.get("kind", "product")
        if kind == "product" and (s.meta.get("status") != "LABELLED" or s.meta.get("product_id") not in index):
            continue
        frames = s.included()
        if len(frames) > max_per_session:
            step = len(frames) / max_per_session
            frames = [frames[int(k * step)] for k in range(max_per_session)]
        chunks = sorted({f["i"] // CHUNK for f in frames})
        val_chunks = {c for k, c in enumerate(chunks) if k % VAL_EVERY == VAL_EVERY // 2}
        if not val_chunks and len(chunks) > 1:
            val_chunks = {chunks[-1]}
        w, h = s.meta["width"], s.meta["height"]
        key = "background" if kind == "background" else s.meta["product_id"]
        for f in frames:
            split = "val" if f["i"] // CHUNK in val_chunks else "train"
            name = f"{s.id}_{f['i']:06d}"
            _link(s.frame_path(f["i"]), out / "images" / split / f"{name}.jpg")
            line = ""
            if kind == "product":
                x1, y1, x2, y2 = f["box"]
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                line = f"{index[key]} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}\n"
            (out / "labels" / split / f"{name}.txt").write_text(line, encoding="ascii")
            counts[key][split] += 1
    missing = [pid for pid in classes if counts[pid]["val"] == 0 or counts[pid]["train"] == 0]
    if missing:
        raise ValueError(f"Too few usable frames to split train/validation for: {', '.join(missing)}. Record a longer clip.")
    names = "\n".join(f"  {k}: {pid}" for k, pid in enumerate(classes))
    (out / "data.yaml").write_text(f"path: {out.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n{names}\n", encoding="utf-8")
    write_json(out / "summary.json", {"classes": classes, "counts": counts})
    return {"classes": classes, "counts": counts, "yaml": str(out / "data.yaml")}


# ---------------- Training subprocess ----------------

def _metrics(values):
    keys = {"precision": "metrics/precision(B)", "recall": "metrics/recall(B)", "map50": "metrics/mAP50(B)", "map50_95": "metrics/mAP50-95(B)"}
    return {short: round(float(values[k]), 4) for short, k in keys.items() if values and k in values}


def train_main(a):
    run = Path(a.run_dir)
    progress_path, stop_path = run / "progress.json", run / "STOP"
    state = {"phase": "starting", "epoch": 0, "epochs": a.epochs, "batch": 0, "batches": 0, "started": time.time(),
             "history": [], "device": a.device, "message": "Loading YOLO26n"}
    last_write = [0.0]

    def report(force=False, **values):
        state.update(values)
        if force or time.monotonic() - last_write[0] > 0.5:
            last_write[0] = time.monotonic()
            write_json(progress_path, state)

    try:
        from ultralytics import YOLO
        import torch
        MODELS.mkdir(parents=True, exist_ok=True)
        model = YOLO(str(MODELS / YOLO_BASE))  # COCO-pretrained YOLO26n; downloaded once into models/
        report(True, phase="preparing", message="Checking dataset and caching images")

        def epoch_start(trainer):
            report(True, phase="training", epoch=trainer.epoch + 1, epochs=trainer.epochs, batch=0,
                   batches=len(trainer.train_loader), message=f"Epoch {trainer.epoch + 1} of {trainer.epochs}")

        def batch_end(trainer):
            if stop_path.exists():
                trainer.stop = True
                report(True, phase="stopping", message="Stopping after this epoch's validation")
            loss = trainer.tloss
            if isinstance(loss, dict):
                loss = sum(float(v) for v in loss.values())
            elif loss is not None:
                loss = float(loss.sum())
            report(batch=state["batch"] + 1, loss=round(loss, 4) if loss is not None else None)

        def epoch_end(trainer):
            if trainer.epoch + 1 != state["epoch"]:  # final_eval re-fires this hook for best.pt; not a new epoch
                report(True, final=_metrics(trainer.metrics))
                return
            entry = {"epoch": trainer.epoch + 1, **_metrics(trainer.metrics), "loss": state.get("loss"), "time": round(time.time() - state["started"], 1)}
            state["history"].append(entry)
            report(True, metrics=entry, message=f"Epoch {trainer.epoch + 1} done")

        def train_end(trainer):
            report(True, phase="finishing", message="Final validation with the best weights")

        model.add_callback("on_train_epoch_start", epoch_start)
        model.add_callback("on_train_batch_end", batch_end)
        model.add_callback("on_fit_epoch_end", epoch_end)
        model.add_callback("on_train_end", train_end)
        images = sum(1 for _ in (Path(a.data).parent / "images").rglob("*.jpg"))
        model.train(data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=a.device, project=str(run), name="yolo",
                    exist_ok=True, patience=max(10, a.epochs // 3), cache="ram" if images <= 1500 else False,
                    workers=a.workers, plots=False, verbose=False, seed=0)
        trainer = model.trainer
        best = Path(trainer.best)
        if not best.is_file():
            raise RuntimeError("Training produced no weights")
        shutil.copyfile(best, run / "best.pt")
        validator = trainer.validator
        per_class = {}
        if validator is not None and getattr(validator, "metrics", None) is not None:
            box = validator.metrics.box
            names = validator.names
            for k, cls in enumerate(getattr(validator.metrics, "ap_class_index", [])):
                per_class[names[int(cls)]] = {"map50": round(float(box.ap50[k]), 4), "map50_95": round(float(box.ap[k]), 4)}
        result = {"classes": read_json(Path(a.data).parent / "summary.json", {}).get("classes"), "metrics": _metrics(trainer.metrics),
                  "per_class": per_class, "epochs_run": len(state["history"]), "stopped_early": stop_path.exists(),
                  "device": a.device, "gpu": torch.cuda.get_device_name(0) if a.device.startswith("cuda") else None,
                  "seconds": round(time.time() - state["started"], 1), "finished": time.time()}
        write_json(run / "result.json", result)
        report(True, phase="done", message="Training finished", result=result)
    except Exception as exc:  # reported to the web page; the traceback stays in train.log
        import traceback
        traceback.print_exc()
        report(True, phase="failed", message=f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train", help="training subprocess (started by tools.trainer)")
    t.add_argument("--data", required=True)
    t.add_argument("--run-dir", required=True)
    t.add_argument("--epochs", type=int, default=50)
    t.add_argument("--imgsz", type=int, default=640)
    t.add_argument("--batch", type=int, default=16)
    t.add_argument("--workers", type=int, default=4)
    t.add_argument("--device", default="auto")
    a = parser.parse_args()
    a.device = resolve_device(a.device)
    train_main(a)


if __name__ == "__main__":
    main()
