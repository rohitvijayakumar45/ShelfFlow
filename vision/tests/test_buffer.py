from vision.capture import RollingBuffer, Frame
from vision.association import roi_pixels, full_frame_box

def test_preserve_without_flush_and_bounds(tmp_path):
    b=RollingBuffer(max_frames=3,max_bytes=10)
    for i in range(5):b.append(Frame(b'xx',i,'b',i,0,float(i),0,10,10,10,0))
    assert [f.sequence for f in b.frames]==[2,3,4]
    refs=b.preserve(tmp_path,'event',2,4)
    assert len(refs)==3 and len(b.frames)==3 and b.dropped==2
    assert (tmp_path/refs[0]).read_bytes()==b'xx'

def test_coordinate_transform():
    assert roi_pixels([.25,0,.75,1],800,600)==(200,0,600,600)
    assert full_frame_box((10,20,30,40),(200,0))==(210,20,230,40)
