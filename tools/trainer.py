"""Local product-training service for the staff app (Train products page).

Record a clip per product from the shelf camera (every frame of the MJPEG stream, original
bytes), mark the product once, let SAM 2.1 box it in every frame, review blurred/odd frames,
then fine-tune YOLO26n. Runs natively next to the vision service because it needs the GPU;
the staff app reaches it through its /trainer proxy with the normal staff login.

  python -m tools.trainer                              # shared camera preview on :8765 (hardware)
  python -m tools.trainer --source webcam:0            # laptop webcam, for trying the flow
  python -m tools.local_app trainer                    # hardware setup with runtime.env
"""
import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.auth import staff
from vision import training as T

app = FastAPI(title="ShelfFlow trainer", version="0.1.0")
SESSIONS = T.DATA / "sessions"
RUNS = T.DATA / "runs"
ACTIVE = T.MODELS / "trained" / "active.json"
MAX_SECONDS, MAX_FRAMES = 120, 2400
lock = threading.Lock()


# ---------------- Camera sources ----------------

class WebcamCamera:
    """Laptop webcam with the same latest-frame interface as camera_preview.SharedCamera."""

    def __init__(self, index):
        self.index = index
        self.condition = threading.Condition()
        self.stopping = threading.Event()
        self.jpeg, self.headers, self.serial, self.received = b"", {}, 0, 0.0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        import cv2
        while not self.stopping.is_set():
            cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            try:
                while not self.stopping.is_set():
                    ok, image = cap.read()
                    if not ok:
                        break
                    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    if ok:
                        with self.condition:
                            self.jpeg = encoded.tobytes()
                            self.headers = {"x-width": str(image.shape[1]), "x-height": str(image.shape[0])}
                            self.serial += 1
                            self.received = time.monotonic()
                            self.condition.notify_all()
            finally:
                cap.release()
            self.stopping.wait(1)

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=3)


class Annotated:
    """Latest annotated frame from the live model test; same interface for streaming."""

    def __init__(self):
        self.condition = threading.Condition()
        self.jpeg, self.serial, self.received = b"", 0, 0.0


class State:
    camera = None
    source = ""
    times = deque(maxlen=90)       # receive times of recent frames -> live fps
    recording = None               # dict while a clip is being recorded
    job = None                     # labelling or training job (one GPU job at a time)
    test = None                    # live model test
    annotated = Annotated()


S = State()


def pump():
    """One thread sees every camera frame: measures fps and writes frames while recording.
    Writing the received JPEG bytes as they are (no decode/re-encode) keeps up with any frame rate."""
    last = 0
    while True:
        camera = S.camera
        with camera.condition:
            ready = camera.condition.wait_for(lambda: camera.serial != last and camera.jpeg, timeout=1)
            if not ready:
                continue
            jpeg, headers, serial, received = camera.jpeg, dict(camera.headers), camera.serial, camera.received
        skipped = serial - last - 1 if last else 0
        last = serial
        S.times.append(received)
        rec = S.recording
        if rec and not rec["stop"].is_set():
            index = len(rec["frames"])
            (rec["dir"] / "frames" / f"{index:06d}.jpg").write_bytes(jpeg)
            score = T.sharpness(jpeg)
            rec["frames"].append({"i": index, "t": round(received - rec["started"], 3), "sharp": score})
            rec["skipped"] += max(0, skipped) if index else 0
            if not rec.get("width"):
                rec["width"], rec["height"] = frame_size(jpeg, headers)
            if index + 1 >= MAX_FRAMES or received - rec["started"] >= MAX_SECONDS:
                threading.Thread(target=finish_recording, daemon=True).start()


def frame_size(jpeg, headers):
    if headers.get("x-width") and headers.get("x-height"):
        return int(headers["x-width"]), int(headers["x-height"])
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_8)
    return image.shape[1] * 8, image.shape[0] * 8


def camera_status():
    camera = S.camera
    age = time.monotonic() - camera.received if camera and camera.received else None
    times = [t for t in S.times if time.monotonic() - t < 3]
    fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 2 and times[-1] > times[0] else 0.0
    return {"source": S.source, "connected": age is not None and age < 3, "fps": round(fps, 1), "frame_age_s": round(age, 2) if age is not None else None}


# ---------------- Recording ----------------

class StartRecording(BaseModel):
    kind: str = Field(default="product", pattern="^(product|background)$")
    product_id: str | None = Field(default=None, max_length=60)
    product_name: str | None = Field(default=None, max_length=120)


@app.post("/recordings")
def start_recording(body: StartRecording, user=Depends(staff)):
    if body.kind == "product" and not body.product_id:
        raise HTTPException(422, "Choose the product you are recording")
    if not camera_status()["connected"]:
        raise HTTPException(503, "Camera is not sending frames; check the camera preview first")
    with lock:
        if S.recording:
            raise HTTPException(409, "A recording is already running")
        sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        path = SESSIONS / sid
        (path / "frames").mkdir(parents=True)
        S.recording = {"id": sid, "dir": path, "frames": [], "skipped": 0, "started": time.monotonic(),
                       "wall": time.time(), "stop": threading.Event(), "body": body.model_dump()}
    return {"id": sid}


def finish_recording():
    with lock:
        rec = S.recording
        if not rec or rec["stop"].is_set():
            return None
        rec["stop"].set()
    time.sleep(0.05)  # let the pump finish the frame it may be writing
    frames = rec["frames"]
    duration = frames[-1]["t"] if frames else 0
    meta = {**rec["body"], "created": rec["wall"], "duration": duration, "fps": round((len(frames) - 1) / duration, 1) if duration else 0,
            "width": rec.get("width"), "height": rec.get("height"), "skipped": rec["skipped"], "source": S.source,
            "status": "RECORDED" if rec["body"]["kind"] == "product" else "LABELLED", "blur_threshold": None}
    session = T.Session(rec["dir"])
    session.meta, session.frames = meta, frames
    if not frames:
        shutil.rmtree(rec["dir"], ignore_errors=True)
    else:
        session.save()
    with lock:
        S.recording = None
    return session


@app.post("/recordings/stop")
def stop_recording(user=Depends(staff)):
    session = finish_recording()
    if session is None:
        raise HTTPException(409, "No recording is running")
    if not session.frames:
        raise HTTPException(422, "No frames were received; nothing was saved")
    return session.summary()


def recording_status():
    rec = S.recording
    if not rec:
        return None
    frames = rec["frames"]
    scores = [f["sharp"] for f in frames]
    threshold = T.auto_blur_threshold(scores)
    recent = scores[-15:]
    return {"id": rec["id"], **rec["body"], "frames": len(frames), "seconds": round(time.monotonic() - rec["started"], 1),
            "sharp": sum(1 for s in scores if s >= threshold), "skipped": rec["skipped"], "max_seconds": MAX_SECONDS,
            "recent_blurry": bool(len(recent) >= 10 and sum(1 for s in recent if s < threshold) > len(recent) / 2)}


# ---------------- Sessions and review ----------------

def get_session(sid):
    path = (SESSIONS / sid).resolve()
    if path.parent != SESSIONS.resolve() or not (path / "meta.json").is_file():
        raise HTTPException(404, "Recording not found")
    return T.Session(path)


@app.get("/sessions")
def list_sessions(user=Depends(staff)):
    return [s.summary() for s in T.sessions(SESSIONS)]


@app.get("/sessions/{sid}")
def session_detail(sid: str, user=Depends(staff)):
    s = get_session(sid)
    return {**s.summary(), "frames": s.frames}


@app.get("/sessions/{sid}/frames/{index}")
def session_frame(sid: str, index: int, thumb: bool = False, user=Depends(staff)):
    s = get_session(sid)
    path = s.frame_path(index)
    if not path.is_file():
        raise HTTPException(404, "Frame not found")
    if not thumb:
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_REDUCED_COLOR_4)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(encoded.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class Review(BaseModel):
    blur_threshold: float | None = None
    auto_threshold: bool = False
    manual: dict[str, bool | None] = {}
    reset_manual: bool = False


@app.post("/sessions/{sid}/review")
def review_session(sid: str, body: Review, user=Depends(staff)):
    s = get_session(sid)
    if body.auto_threshold:
        s.meta["blur_threshold"] = None
    elif body.blur_threshold is not None:
        s.meta["blur_threshold"] = max(0.0, float(body.blur_threshold))
    for frame in s.frames:
        if body.reset_manual:
            frame["manual"] = None
        key = str(frame["i"])
        if key in body.manual:
            frame["manual"] = body.manual[key]
    s.save()
    return s.summary()


@app.post("/sessions/{sid}/delete")
def delete_session(sid: str, user=Depends(staff)):
    s = get_session(sid)
    if S.job and S.job.get("session") == sid and S.job["phase"] not in ("done", "failed", "cancelled"):
        raise HTTPException(409, "This recording is being labelled")
    shutil.rmtree(s.path)
    return {"ok": True}


# ---------------- Jobs: SAM labelling and training ----------------

def job_busy():
    job = S.job
    return job is not None and job["phase"] not in ("done", "failed", "cancelled")


def claim_job(**job):
    with lock:
        if job_busy():
            raise HTTPException(409, f"Wait for the running job ({S.job['kind']}) to finish or stop it")
        if S.test:
            raise HTTPException(409, "Stop the live model test first; it is using the GPU")
        S.job = {"id": uuid.uuid4().hex[:8], "phase": "starting", "progress": 0.0, "message": "", "started": time.time(),
                 "cancel": threading.Event(), **job}
        return S.job


class Mark(BaseModel):
    frame: int = Field(ge=0)
    point: list[float] | None = Field(default=None, min_length=2, max_length=2)
    box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    quality: str = Field(default="fast", pattern="^(fast|precise)$")


@app.post("/sessions/{sid}/label")
def label(sid: str, body: Mark, user=Depends(staff)):
    s = get_session(sid)
    if s.meta.get("kind") != "product":
        raise HTTPException(422, "Background recordings need no boxes")
    if not body.point and not body.box:
        raise HTTPException(422, "Click the product or drag a box around it")
    prompt = {"frame": body.frame, **({"box": body.box} if body.box else {"point": body.point})}
    job = claim_job(kind="label", session=sid, message="Loading SAM 2.1")

    def work():
        def progress(done, total):
            job.update(phase="running", progress=done / max(1, total), message=f"Finding the product: frame {done} of {total}")
        try:
            T.label_session(s, prompt, body.quality, progress=progress, cancelled=job["cancel"].is_set)
            job.update(phase="done", progress=1.0, message="Boxes ready for review", result=s.summary())
        except InterruptedError:
            job.update(phase="cancelled", message="Labelling cancelled; previous boxes kept")
        except Exception as exc:
            job.update(phase="failed", message=f"{type(exc).__name__}: {exc}")
    threading.Thread(target=work, daemon=True).start()
    return {k: v for k, v in job.items() if k != "cancel"}


class Train(BaseModel):
    sessions: list[str] | None = None
    epochs: int = Field(default=50, ge=1, le=300)
    imgsz: int = Field(default=640, ge=320, le=1280)


@app.post("/train")
def train(body: Train, user=Depends(staff)):
    chosen = [get_session(sid) for sid in body.sessions] if body.sessions else T.sessions(SESSIONS)
    rid = time.strftime("%Y%m%d-%H%M%S")
    run = RUNS / rid
    try:
        summary = T.build_dataset(chosen, run / "dataset")
    except ValueError as exc:
        shutil.rmtree(run, ignore_errors=True)
        raise HTTPException(422, str(exc))
    device = T.resolve_device("auto")
    T.write_json(run / "meta.json", {"id": rid, "created": time.time(), "sessions": [s.id for s in chosen], "epochs": body.epochs,
                                     "imgsz": body.imgsz, "device": device, **summary})
    base = T.MODELS / T.YOLO_BASE
    if base.is_file():
        T._link(base, run / T.YOLO_BASE)  # the AMP check loads ./yolo26n.pt; avoid a second download
    try:
        job = claim_job(kind="train", run=rid, message="Starting training")
    except HTTPException:
        shutil.rmtree(run, ignore_errors=True)
        raise
    cmd = [sys.executable, "-m", "vision.training", "train", "--data", summary["yaml"], "--run-dir", str(run),
           "--epochs", str(body.epochs), "--imgsz", str(body.imgsz), "--device", device,
           "--batch", "16" if device.startswith("cuda") else "8", "--workers", "4"]
    env = {**os.environ, "PYTHONPATH": str(T.ROOT), "PYTHONUNBUFFERED": "1"}
    log = open(run / "train.log", "w", encoding="utf-8", errors="replace")
    job["proc"] = subprocess.Popen(cmd, cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT)

    def watch():
        proc = job["proc"]
        while proc.poll() is None:
            update_train_job(job, run)
            time.sleep(0.5)
        log.close()
        update_train_job(job, run)
        if job["phase"] not in ("done", "failed"):
            job.update(phase="cancelled" if job["cancel"].is_set() else "failed",
                       message="Training stopped" if job["cancel"].is_set() else f"Training process exited ({proc.returncode}); see train.log")
    threading.Thread(target=watch, daemon=True).start()
    return {"id": job["id"], "run": rid, **summary}


def update_train_job(job, run):
    p = T.read_json(run / "progress.json")
    if not p:
        return
    epochs, epoch = max(1, p.get("epochs") or 1), p.get("epoch") or 0
    within = (p.get("batch", 0) / p["batches"]) if p.get("batches") else 0
    job.update(train=p, message=p.get("message", ""), progress=min(1.0, (max(0, epoch - 1) + within) / epochs))
    if p.get("phase") in ("done", "failed"):
        job["phase"] = p["phase"]
        if p["phase"] == "done":
            job["progress"] = 1.0
    elif job["phase"] != "stopping":
        job["phase"] = "running"


@app.post("/jobs/cancel")
def cancel_job(force: bool = False, user=Depends(staff)):
    job = S.job
    if not job_busy():
        raise HTTPException(409, "Nothing is running")
    job["cancel"].set()
    if job["kind"] == "train":
        run = RUNS / job["run"]
        (run / "STOP").write_text("stop", encoding="ascii")
        job.update(phase="stopping", message="Stopping after the current batch; the best weights so far are kept")
        if force and job.get("proc"):
            job["proc"].kill()
    else:
        job["message"] = "Cancelling"
    return {"ok": True}


def public_job():
    job = S.job
    return {k: v for k, v in job.items() if k not in ("cancel", "proc")} if job else None


# ---------------- Trained models ----------------

def get_run(rid):
    path = (RUNS / rid).resolve()
    if path.parent != RUNS.resolve() or not (path / "meta.json").is_file():
        raise HTTPException(404, "Training run not found")
    return path


@app.get("/runs")
def list_runs(user=Depends(staff)):
    active = T.read_json(ACTIVE, {}) or {}
    out = []
    if RUNS.is_dir():
        for path in sorted(RUNS.iterdir(), reverse=True):
            meta = T.read_json(path / "meta.json")
            if not meta:
                continue
            out.append({**meta, "result": T.read_json(path / "result.json"), "has_weights": (path / "best.pt").is_file(),
                        "active": active.get("run") == path.name})
    return out


@app.post("/runs/{rid}/activate")
def activate(rid: str, user=Depends(staff)):
    path = get_run(rid)
    if not (path / "best.pt").is_file():
        raise HTTPException(422, "This run has no finished weights")
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path / "best.pt", ACTIVE.parent / "active.pt")
    result = T.read_json(path / "result.json", {}) or {}
    T.write_json(ACTIVE, {"run": rid, "weights": "models/trained/active.pt", "classes": result.get("classes"),
                          "metrics": result.get("metrics"), "activated": time.time()})
    return {"ok": True, "note": "Restart the vision service and detector preview to use this model."}


class TestStart(BaseModel):
    run: str | None = None
    conf: float = Field(default=0.4, ge=0.05, le=0.95)


@app.post("/test/start")
def test_start(body: TestStart, user=Depends(staff)):
    if job_busy():
        raise HTTPException(409, "The GPU is busy with a job; test after it finishes")
    if body.run:
        weights = get_run(body.run) / "best.pt"
    else:
        weights = ACTIVE.parent / "active.pt"
    if not weights.is_file():
        raise HTTPException(422, "No trained weights to test yet")
    test_stop(user)
    from ultralytics import YOLO
    model = YOLO(str(weights))
    stop = threading.Event()
    S.test = {"run": body.run or (T.read_json(ACTIVE, {}) or {}).get("run"), "stop": stop, "ms": None, "detections": []}
    threading.Thread(target=run_test, args=(model, body.conf, stop, S.test), daemon=True).start()
    return {"ok": True}


def run_test(model, conf, stop, test):
    import cv2
    import numpy as np
    device = T.resolve_device("auto")
    colours = [(40, 200, 60), (0, 170, 255), (220, 80, 220), (255, 160, 0), (60, 60, 255), (0, 220, 220)]
    last = 0
    while not stop.is_set():
        camera = S.camera
        with camera.condition:
            if not camera.condition.wait_for(lambda: camera.serial != last and camera.jpeg, timeout=1):
                continue
            jpeg, last = camera.jpeg, camera.serial
        started = time.monotonic()
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        result = model.predict(image, conf=conf, device=device, verbose=False)[0]
        found = []
        for box in result.boxes:
            cls, score = int(box.cls.item()), float(box.conf.item())
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            name, colour = result.names[cls], colours[cls % len(colours)]
            cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
            label = f"{name} {score:.2f}"
            (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            ty = max(th + 6, y1)
            cv2.rectangle(image, (x1, ty - th - 6), (x1 + tw + 8, ty + base), colour, -1)
            cv2.putText(image, label, (x1 + 4, ty - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
            found.append({"product": name, "confidence": round(score, 3)})
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        test.update(ms=round((time.monotonic() - started) * 1000, 1), detections=found)
        if ok:
            target = S.annotated
            with target.condition:
                target.jpeg, target.received = encoded.tobytes(), time.monotonic()
                target.serial += 1
                target.condition.notify_all()


@app.post("/test/stop")
def test_stop(user=Depends(staff)):
    if S.test:
        S.test["stop"].set()
        S.test = None
    return {"ok": True}


# ---------------- Live view and status ----------------

async def mjpeg(source):
    """multipart/x-mixed-replace of the newest frame; a slow viewer skips frames instead of lagging."""
    last = -1
    idle = 0.0
    while idle < 30:
        if source.serial != last and source.jpeg:
            jpeg, last, idle = source.jpeg, source.serial, 0.0
            yield b"--sfframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
        else:
            await asyncio.sleep(0.01)
            idle += 0.01


@app.get("/stream")
def stream(user=Depends(staff)):
    return StreamingResponse(mjpeg(S.camera), media_type="multipart/x-mixed-replace;boundary=sfframe", headers={"Cache-Control": "no-store"})


@app.get("/test/stream")
def test_stream(user=Depends(staff)):
    return StreamingResponse(mjpeg(S.annotated), media_type="multipart/x-mixed-replace;boundary=sfframe", headers={"Cache-Control": "no-store"})


GPU = {}


def gpu_info():
    if not GPU:
        try:
            import torch
            GPU.update(available=torch.cuda.is_available(), name=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                       torch=torch.__version__)
        except ImportError:
            GPU.update(available=False, name=None, torch=None)
    return GPU


@app.get("/status")
def status(user=Depends(staff)):
    test = S.test
    return {"camera": camera_status(), "gpu": gpu_info(), "recording": recording_status(), "job": public_job(),
            "active_model": T.read_json(ACTIVE), "max_seconds": MAX_SECONDS,
            "weights": {name: (T.MODELS / name).is_file() for name in (T.YOLO_BASE, *T.SAM_MODELS.values())},
            "test": {k: v for k, v in test.items() if k != "stop"} if test else None}


def prefetch_weights():
    """Download YOLO26n and SAM 2.1 tiny once, up front, with a progress bar in this console."""
    try:
        from ultralytics.utils.downloads import attempt_download_asset
        T.MODELS.mkdir(parents=True, exist_ok=True)
        for name in (T.YOLO_BASE, T.SAM_MODELS["fast"]):
            if not (T.MODELS / name).is_file():
                print(f"Downloading {name} into models/ ...", flush=True)
                attempt_download_asset(str(T.MODELS / name))
    except Exception as exc:
        print(f"Model download failed ({exc}); it will be retried when first needed.", flush=True)


def make_source(spec):
    if spec.startswith("webcam:"):
        return WebcamCamera(int(spec.split(":", 1)[1]))
    from tools.camera_preview import SharedCamera
    return SharedCamera(spec, os.getenv("CAMERA_KEY", "") if ":81/" in spec else "")


def configure(source):
    S.source = source
    S.camera = make_source(source)
    S.camera.start()
    threading.Thread(target=pump, daemon=True).start()
    threading.Thread(target=prefetch_weights, daemon=True).start()
    threading.Thread(target=gpu_info, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.getenv("TRAINER_SOURCE", "http://127.0.0.1:8765/stream"),
                        help="MJPEG stream URL (default: shared camera preview) or webcam:N")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    configure(args.source)
    import uvicorn
    print(f"Trainer service on http://127.0.0.1:{args.port} (camera: {args.source})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
