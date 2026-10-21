"""Local browser preview for the camera's authenticated MJPEG transport."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from vision.capture import MjpegStream


PAGE = b'''<!doctype html><html><head><meta name="viewport" content="width=device-width">
<title>ShelfFlow camera</title><style>
body{margin:0;background:#151b18;color:#eef4ef;font:16px system-ui;padding:24px}
h1{font-size:24px}img{display:block;max-width:100%;height:auto;border-radius:8px}
a{color:#b4dbc2}</style></head><body><h1>OV3660 live feed</h1>
<p id="status">Connecting to camera...</p><button id="reconnect">Reconnect</button>
<img id="camera" alt="Waiting for camera frames"><script>
const image=document.getElementById('camera'), status=document.getElementById('status');
let retry;
function connect(){clearTimeout(retry);image.src='/stream?t='+Date.now();}
image.onerror=()=>{status.textContent='Reconnecting to camera...';retry=setTimeout(connect,1500);};
document.getElementById('reconnect').onclick=connect;
setInterval(async()=>{try{const r=await fetch('/status',{cache:'no-store'});const s=await r.json();
status.textContent=s.connected?'Live camera feed':'Reconnecting to camera...';}catch{
status.textContent='Preview server unavailable; reload this page after it restarts.';}},2000);
connect();</script></body></html>'''


class SharedCamera:
    """One camera stream, one latest-frame buffer, any number of local viewers."""
    def __init__(self, upstream, key):
        self.upstream, self.key = upstream, key
        self.condition = threading.Condition()
        self.stopping = threading.Event()
        self.jpeg = b""
        self.headers = {}
        self.serial = 0
        self.received = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            stream = None
            try:
                with httpx.Client(trust_env=False) as client:
                    stream = MjpegStream(client, self.upstream, self.key, timeout=15)
                    try:
                        while not self.stopping.is_set():
                            headers, jpeg = stream.next_part()
                            if not jpeg.startswith(b"\xff\xd8"):
                                raise ValueError("Invalid camera JPEG")
                            with self.condition:
                                self.jpeg, self.headers = jpeg, dict(headers)
                                self.serial += 1
                                self.received = time.monotonic()
                                self.condition.notify_all()
                    finally:
                        stream.close()
            except (httpx.HTTPError, OSError, ConnectionError, ValueError, KeyError):
                self.stopping.wait(1)

    def close(self):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=4)

    def status(self):
        with self.condition:
            age = time.monotonic() - self.received if self.received else None
            return {"connected": age is not None and age < 6,
                    "frames_received": self.serial, "frame_age_s": age}


def make_server(camera, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self.serve()
            except (OSError, ConnectionError):
                pass  # Browser reloads and disconnects are normal.

        def serve(self):
            path = urlsplit(self.path).path
            if path in ("/", "/status"):
                body = PAGE if path == "/" else json.dumps(camera.status()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if path == "/" else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/jpeg":
                with camera.condition:
                    jpeg = camera.jpeg if camera.jpeg and time.monotonic() - camera.received < 2 else b""
                if not jpeg:
                    self.send_error(503, "Waiting for a fresh camera frame")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpeg)
            elif path == "/stream":
                last = -1
                started = False
                self.connection.settimeout(10)
                while not camera.stopping.is_set():
                    with camera.condition:
                        fresh = camera.condition.wait_for(
                            lambda: camera.stopping.is_set() or (
                                camera.serial != last and camera.jpeg and
                                time.monotonic() - camera.received < 6), timeout=8)
                        if camera.stopping.is_set():
                            return
                        if not fresh:
                            if not started:
                                self.send_error(503, "Camera reconnecting; preview will retry")
                                return
                            continue  # Keep existing viewers attached through upstream outages.
                        jpeg, headers, last = camera.jpeg, camera.headers.copy(), camera.serial
                    if not started:
                        self.send_response(200)
                        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=sfframe")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        started = True
                    part = f"--sfframe\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n"
                    for name in ("x-frame-sequence", "x-boot-id", "x-capture-us", "x-width", "x-height"):
                        if name in headers:
                            value = headers[name].replace("\r", "").replace("\n", "")
                            part += f"{name}: {value}\r\n"
                    self.wfile.write(part.encode() + b"\r\n" + jpeg + b"\r\n")
                    self.wfile.flush()
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", required=True, help="Camera base URL, e.g. http://192.168.1.25")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--secrets", type=Path, default=Path(__file__).resolve().parents[1] / "firmware/camera/include/secrets.h")
    args = parser.parse_args()
    match = re.search(r'^\s*#define\s+CAMERA_KEY\s+"([^"\r\n]+)"', args.secrets.read_text(), re.MULTILINE)
    if not match:
        parser.error("CAMERA_KEY was not found in the local camera secrets file")
    key = match.group(1)
    base = httpx.URL(args.camera)
    if base.scheme != "http" or not base.host:
        parser.error("Use the camera's http:// address")
    upstream = str(base.copy_with(port=81, path="/stream", query=None))

    camera = SharedCamera(upstream, key)
    server = make_server(camera, args.port)
    camera.start()
    print(f"Camera preview: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        camera.close()


if __name__ == "__main__":
    main()
