import cv2
import numpy as np
from pathlib import Path

DICTIONARY = cv2.aruco.DICT_4X4_250

def decode(image):
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTIONARY), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(image)
    return [] if ids is None else [{"id": int(i), "corners": c.reshape(4,2).tolist()} for c, i in zip(corners, ids.flatten())]

def generate(marker_id, path, pixels=600):
    if not 0 <= marker_id < 250:
        raise ValueError("Marker ID outside DICT_4X4_250")
    marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(DICTIONARY), marker_id, pixels)
    bordered = cv2.copyMakeBorder(marker, pixels // 5, pixels // 5, pixels // 5, pixels // 5, cv2.BORDER_CONSTANT, value=255)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), bordered)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("id", type=int)
    p.add_argument("output")
    args = p.parse_args()
    generate(args.id, args.output)
