import time
import httpx
import pytest
from vision.capture import Capture

def capture(clock_boot='b'):
    def respond(request):
        if request.url.path=='/clock':return httpx.Response(200,json={'boot_id':clock_boot,'monotonic_us':1000})
        return httpx.Response(200,content=b'\xff\xd8\xff\xd9',headers={'Content-Type':'image/jpeg','X-Frame-Sequence':'3','X-Boot-Id':'b','X-Capture-Us':'1000','X-Width':'8','X-Height':'8'})
    source=Capture('http://camera','test-key');source.client.close();source.client=httpx.Client(transport=httpx.MockTransport(respond));return source

def test_clock_json_and_original_jpeg_transport():
    source=capture();frame=source.poll()
    assert frame.jpeg==b'\xff\xd8\xff\xd9' and frame.boot_id=='b' and len(source.buffer.frames)==1
    source.client.close()

@pytest.mark.parametrize('old_us',[1000,1001])
def test_repeated_or_backwards_capture_rejected_even_with_new_sequence(old_us):
    source=capture();source.last_capture=('b',old_us)
    with pytest.raises(ValueError,match='capture timestamp'):source.poll()
    assert not source.buffer.frames
    source.client.close()

def test_reboot_during_clock_exchange_rejects_frame():
    source=capture(clock_boot='different-boot')
    with pytest.raises(ValueError,match='rebooted'):source.poll()
    source.client.close()


def test_evidence_can_be_written_in_the_background(tmp_path):
    import time
    from vision.capture import RollingBuffer, Frame
    buffer = RollingBuffer()
    for n in range(3):
        buffer.append(Frame(b'\xff\xd8x\xff\xd9', n, 'b', n, 1.0, float(n), 0, 8, 8, 1, 1.0))
    slow = lambda jpeg: (time.sleep(0.2), (jpeg, 0))[1]
    started = time.monotonic()
    names = buffer.preserve(tmp_path, 'ev', 0, 5, redact=slow, background=True)
    assert len(names) == 3 and time.monotonic() - started < 0.2   # names at once, the loop is not held up
    deadline = time.monotonic() + 10  # generous: the laptop may be busy with the live services
    while not all((tmp_path / n).exists() for n in names) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert all((tmp_path / n).exists() and (tmp_path / n.replace('.jpg', '.json')).exists() for n in names)


def test_long_windows_are_thinned_to_fit_the_event(tmp_path):
    from vision.capture import RollingBuffer, Frame
    buffer = RollingBuffer(max_frames=300)
    for n in range(150):
        buffer.append(Frame(b'\xff\xd8x\xff\xd9', n, 'b', n, 1.0, n * 0.1, 0, 8, 8, 1, 1.0))
    names = buffer.preserve(tmp_path, 'ev', 0, 100, max_frames=90)
    assert len(names) == 90 and names[0].endswith('-0.jpg') and names[-1].endswith(f'-{int(89 * 150 / 90)}.jpg')
