"""Preview reconnects and shares one upstream connection across browser viewers."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import httpx

from tools.camera_preview import SharedCamera, make_server


def test_snapshot_uses_cached_frame_and_rejects_stale():
    camera=SharedCamera('http://unused','key')
    camera.jpeg=b'\xff\xd8fixture\xff\xd9';camera.received=time.monotonic()
    server=make_server(camera,0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/jpeg'
        response=httpx.get(url,trust_env=False)
        assert response.status_code==200 and response.content==camera.jpeg
        camera.received=time.monotonic()-10
        assert httpx.get(url,trust_env=False).status_code==503
    finally:
        server.shutdown();server.server_close()


def test_reconnect_then_share_upstream_across_viewers():
    stopping = threading.Event()
    requests = []

    class Camera(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.headers.get("X-Camera-Key") == "test-key"
            requests.append(self.path)
            attempt = len(requests)
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=sfframe")
            self.end_headers()
            # Transport fixture only; not a decodable photograph.
            jpeg = b"\xff\xd8fixture\xff\xd9"
            try:
                while not stopping.is_set():
                    part = f"--sfframe\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                    self.wfile.write(part + jpeg + b"\r\n")
                    self.wfile.flush()
                    if attempt == 1:
                        return  # Camera drops its first connection after one frame.
                    stopping.wait(0.03)
            except OSError:
                pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    camera = SharedCamera(f"http://127.0.0.1:{upstream.server_port}/stream", "test-key")
    preview = make_server(camera, 0)
    threading.Thread(target=preview.serve_forever, daemon=True).start()
    camera.start()
    try:
        with camera.condition:
            assert camera.condition.wait_for(lambda: camera.serial >= 2, timeout=6)
        assert len(requests) == 2  # One reconnect, then one persistent upstream.

        def view():
            with httpx.stream("GET", f"http://127.0.0.1:{preview.server_port}/stream", timeout=3, trust_env=False) as response:
                response.raise_for_status()
                content = b""
                for chunk in response.iter_raw():
                    content += chunk
                    if b"\xff\xd8fixture\xff\xd9" in content:
                        return True
            return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert all(pool.map(lambda _: view(), range(2)))
        assert len(requests) == 2  # Viewers must not open camera connections.
        assert camera.status()["connected"]
    finally:
        stopping.set()
        camera.close()
        preview.shutdown()
        preview.server_close()
        upstream.shutdown()
        upstream.server_close()
