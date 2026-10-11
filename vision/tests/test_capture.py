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
