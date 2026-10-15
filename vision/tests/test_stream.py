import httpx
import pytest
from vision.capture import Capture

JPEG = b'\xff\xd8' + b'x' * 50 + b'\xff\xd9'

def part(sequence, capture_us, body=JPEG, boot='b'):
    head = (f'--sfframe\r\nContent-Type: image/jpeg\r\nContent-Length: {len(body)}\r\n'
            f'X-Frame-Sequence: {sequence}\r\nX-Boot-Id: {boot}\r\nX-Capture-Us: {capture_us}\r\n'
            f'X-Width: 800\r\nX-Height: 600\r\n\r\n').encode()
    return head + body + b'\r\n'

def source(payload, chunk=7, seen=None):
    def respond(request):
        if request.url.path == '/clock':
            return httpx.Response(200, json={'boot_id': 'b', 'monotonic_us': 1000})
        assert request.url.port == 81 and request.url.path == '/stream'
        if seen is not None:
            seen.append(request.headers.get('X-Camera-Key'))
        # Deliberately tiny chunks: parts must reassemble across arbitrary TCP boundaries.
        return httpx.Response(200, headers={'Content-Type': 'multipart/x-mixed-replace;boundary=sfframe'},
                              stream=httpx.ByteStream(payload) if chunk is None else Chunked(payload, chunk))
    capture = Capture('http://camera', 'test-key', transport='stream')
    capture.client.close()
    capture.client = httpx.Client(transport=httpx.MockTransport(respond))
    return capture

class Chunked(httpx.SyncByteStream):
    def __init__(self, data, size):
        self.data, self.size = data, size
    def __iter__(self):
        for i in range(0, len(self.data), self.size):
            yield self.data[i:i + self.size]

def test_stream_parts_reassemble_and_keep_sequence_metadata():
    keys = []
    capture = source(part(1, 1000) + part(2, 2000), seen=keys)
    first, second = capture.poll(), capture.poll()
    assert (first.sequence, second.sequence) == (1, 2)
    assert first.jpeg == JPEG and second.capture_us == 2000 and first.width == 800
    assert keys == ['test-key'] and len(capture.buffer.frames) == 2
    capture.close()

def test_stream_end_reconnects_on_next_poll():
    capture = source(part(1, 1000))
    capture.poll()
    with pytest.raises(ConnectionError):
        capture.poll()
    assert capture.stream is None  # next poll opens a fresh stream
    capture.close()

def test_stream_rejects_repeated_capture_time():
    capture = source(part(1, 1000) + part(2, 1000))
    capture.poll()
    with pytest.raises(ValueError, match='capture timestamp'):
        capture.poll()
    capture.close()

def test_stream_rejects_oversized_part():
    head = b'--sfframe\r\nContent-Length: 99999999\r\nX-Frame-Sequence: 1\r\nX-Boot-Id: b\r\nX-Capture-Us: 1\r\nX-Width: 8\r\nX-Height: 8\r\n\r\n'
    capture = source(head)
    with pytest.raises(ValueError, match='part length'):
        capture.poll()
    assert capture.stream is None
    capture.close()

def test_unknown_transport_refused():
    with pytest.raises(ValueError):
        Capture('http://camera', 'k', transport='udp')
