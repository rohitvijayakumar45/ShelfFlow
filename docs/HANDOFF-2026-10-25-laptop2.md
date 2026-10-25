# Handoff: second laptop session, 25 October 2026 (evening)

Read this first, then `docs/HANDOFF.md` (hardware, weighing, camera history). Everything below was done on
Aditya's laptop (RTX 4050 6 GB, Windows 11), folder
`C:\Users\Aditya M Nair\Aditya\VIT Notes\Semester 5\Embedded Systems\embedded project\ShelfFlow`.

## Rules the user set (keep following them)

- **Do not change hardware/firmware/load-cell/RFID code. Do not flash anything without telling the user first.**
  Only the website and the camera are in scope.
- Keep installs inside the project folder; keep a removal list (below).
- Every large download must be resumable and hash-checked (Wi-Fi drops often).
- Keep the DINOv2 gallery path available; YOLO26n video training is the primary training flow.
- User wants fast, plain-language answers; they use the website themselves.

## Hardware and network (this laptop)

| Item | Value |
|---|---|
| Hotspot | **X200T**, laptop and camera. **Pixel** is a planned second network (passwords: ask the user / camera `secrets.h` backups) |
| Camera | ESP32-CAM OV3660 at **10.12.94.251**, MAC `68:09:47:F8:93:B8`, 320x240 ~11 fps, healthy |
| Shelf controller | **COM7** (docs say COM3). Two cells A/C, RC522 R1/R2, USB mode (wifi=down is normal) |
| Programmer ESP32 | **COM9** (docs say COM4) |
| GPU | CUDA PyTorch 2.10.0+cu128 installed in `.venv` |

## Logins

- Staff / admin: http://127.0.0.1:5174; customer: http://127.0.0.1:5173 (username `shopper`). Passwords are in
  `docs/local-hardware-integration.md` and the git-ignored `data/integration/runtime.env`.
- GitHub: Git Credential Manager is signed in as **adityanair42** (has push access to rohitvijayakumar45/ShelfFlow).

## How to start everything (each in its own terminal, from the ShelfFlow folder)

```text
.\.venv\Scripts\python.exe -m tools.local_app backend
.\.venv\Scripts\python.exe -m tools.camera_preview --camera http://10.12.94.251
.\.venv\Scripts\python.exe -m tools.usb_bridge --port COM7
.\.venv\Scripts\python.exe -m tools.weight_monitor
.\.venv\Scripts\python.exe -m tools.detector_preview
.\.venv\Scripts\python.exe -m tools.local_app trainer
.\.venv\Scripts\python.exe -m tools.local_app vision
cd apps; npm run dev --workspace @shelfflow/staff     (and @shelfflow/customer)
```

Do NOT run `tools.local_app setup`: the restored DB is at migration 0013 and setup would try to migrate.
Optional diagnostics: `python -m tools.camera_watch --log data/logs/camera-watch.jsonl` (was running).

## What was done this session (commits on GitHub main)

1. **Setup**: venv with `.[test,vision,hardware]`, npm deps, GPU PyTorch (resumable curl + SHA-256 script),
   YOLO26n + SAM 2.1 tiny/base weights in `models/`, PlatformIO toolchain in `.platformio-core/` (prefetched
   resumably into PlatformIO's cache). The user's old-machine git-ignored files were restored from
   `Downloads\ShelfFlow-ignored-files-20261025` (secrets.h for camera + controller, `data/`, `models/`,
   `evidence/`). Absolute paths in `data/integration/runtime.env` were rewritten from `C:/Users/rohit/...`
   to this folder (original kept as `runtime.env.old-system`).
2. Pulled Rohit's 25 Oct commits (manual capture, two-cell weighing) and merged on top.
3. `29acc13` **Train products** (video training): `tools/trainer.py` (GPU service, port 8001, staff JWT, proxied
   at `/trainer` by the staff Vite app), `vision/training.py` (record every MJPEG frame raw, sharpness, SAM 2.1
   video tracking from one click/box, blur/odd-box auto-exclusion, chunked train/val split, YOLO26n training
   subprocess with progress.json and STOP file), `apps/shared/Training.tsx`.
4. `6a89a67` **Staff app redesign**: grouped sidebar (Home, Live camera, Review queue, Shoppers & checkout,
   Stock / Products & training / Insights, Shelf setup), header status chips, live MJPEG via new
   `/staff/camera/stream` (~11 fps), Products & training tabs (Catalogue, Train with video, Photo references,
   Camera questions). Fixed broken assisted snapshot URL.
5. `88f429f` **Review queue**: snapshot thumbnails per item (`thumb`/`frames` added to `/staff/state`
   hypotheses), clip autoplays, one-tap product (with photos via `/staff/product-image`)/movement/shopper,
   Enter = record, D = dismiss, arrows = navigate, bulk dismiss. Detector preview uses the activated trained model.
6. `7a996d0` **Training fixes**: Windows PermissionError on progress.json crashed a run (now retried, non-fatal);
   train at frame size 320 with batch 32 (epochs 15 s -> 2-4 s); max 250 frames per clip; close_mosaic=0.
   SAM labelling at imgsz 640 (3x faster). Real result: Hair Wax 99.5% mAP50.
7. `a142aa7` **Training page clarity**: "Model in use" banner, run status/recordings/delete, instant stop during
   start-up, stopped runs keep best checkpoint (salvage), atomic activation, detector preview hot-reloads
   `models/trained/active.pt` within ~3 s.

Tests: 227 pass (`.\.venv\Scripts\python.exe -m pytest -q`); `cd apps; node build.mjs` builds both apps.

## Measured facts

- Recording: 89 frames in 8 s = 11.1 fps, 0 skipped. SAM 2.1 tiny on GPU ~15 s per 90 frames.
- Training 30 epochs on one product: ~1.5 min Windows start-up (dataloader workers, AMP check) + 2-4 s/epoch.
- Camera works with the ORIGINAL key; no reflash was needed.

## What is left

1. **User is training products** on the website now (Hair Wax done; Vicks, Sanitizer, Sunscreen, Banana Powder,
   plus an empty-tray recording, then one combined training run). Then "Use this model". The vision service
   (`tools.local_app vision`) needs a restart to use a new model; the detector preview does not.
2. **Camera flash to add Pixel** (X200T + Pixel; firmware already rotates through up to 3 SSIDs every 15 s).
   Needs: (a) a short PlatformIO path, because builds from this long folder exceed Windows' 32,767-char
   command limit (plan: junction `C:\pio` -> `.platformio-core`, set `PLATFORMIO_CORE_DIR=C:\pio`; ask user first);
   (b) add `WIFI_SSID_2 "Pixel"` / `WIFI_PASSWORD_2` (ask the user) to `firmware/camera/include/secrets.h`
   (keep CAMERA_KEY unchanged); (c) user fits programmer EN->GND jumper and holds camera IO0->GND while tapping
   RST; (d) `esptool read_mac` must show `68:09:47:F8:93:B8` before upload. Tell the user before flashing.
3. **Camera start-up problem** (intermittent, see HANDOFF.md section 8). New finding: the COM9 brownout loop
   continued while the camera's 5 V was unplugged and while the camera was up on Wi-Fi, so that loop is most
   likely the **programmer board**, not the camera (programmer not held in reset). The old "camera brownout" log
   may be the same mix-up. Next: check `data/logs/camera-watch.jsonl` for "REBOOTED while away" vs "STAYED
   RUNNING"; fit the 100 uF capacitor at the camera 5V/GND; with EN->GND on the programmer, read the camera's own
   boot log via `tools.camera_serial_log --port COM9`.
4. 87 pending reviews (mostly test leftovers): Review queue -> Select all -> Dismiss selected.
5. Camera framing / tray ROI (HANDOFF.md step 1); weights still disabled (`WEIGHTS_ENABLED=false`).

## Removal list (everything installed this session)

Inside the project: `.venv/` (incl. CUDA torch 2.10, ultralytics, opencv, `lap` auto-installed), `apps/node_modules/`,
`apps/*/dist/`, `.platformio-core/` (~1 GB), `models/`, `data/` (wheels, downloads, logs, training, restored files),
`evidence/`, `.env`, `shelfflow.db`, `firmware/*/include/secrets.h` + `board_config.h` (restored).
Outside: `%APPDATA%\Ultralytics\` (settings), the pip/npm caches, Git Credential Manager's stored GitHub login.
