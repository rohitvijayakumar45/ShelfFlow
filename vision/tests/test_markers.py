import cv2
from vision.markers import generate,decode

def test_marker_roundtrip_with_border(tmp_path):
    path=tmp_path/'marker.png';generate(101,path,pixels=300)
    image=cv2.imread(str(path));result=decode(image)
    assert len(result)==1 and result[0]['id']==101 and len(result[0]['corners'])==4
