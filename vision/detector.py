from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass
class Detection:
    sku: str
    confidence: float
    box: tuple
    track_id: int | None

class Detector(Protocol):
    status: str
    def detect(self, image) -> list[Detection]: ...

class UnavailableDetector:
    status = "MODEL_UNAVAILABLE"
    def detect(self, image):
        return []

class MockDetector:
    status = "MOCK"
    def __init__(self, detections=None):
        self.detections = detections or []
    def detect(self, image):
        return self.detections

class YoloDetector:
    status = "TRAINED"
    def __init__(self, weights, sku_map, device="cpu"):
        if not Path(weights).is_file():
            raise FileNotFoundError("Custom SKU weights unavailable; pretrained COCO is not an exact SKU detector")
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.sku_map, self.device = sku_map, device
        if set(map(int, self.model.names)) != set(map(int, sku_map)):
            raise ValueError("Custom model classes do not match catalogue mapping")
        if any(self.model.names[int(key)] != value for key,value in sku_map.items()):
            raise ValueError("Custom model class names must match the dataset's exact SKU IDs")

    def detect(self, image):
        result = self.model.track(image, persist=True, tracker="bytetrack.yaml", device=self.device, verbose=False)[0]
        output = []
        for box in result.boxes:
            output.append(Detection(self.sku_map[int(box.cls.item())], float(box.conf.item()), tuple(box.xyxy[0].tolist()), int(box.id.item()) if box.id is not None else None))
        return output

class AgnosticDetector:
    """Single-class "item" detector (custom YOLO26 trained with one class).

    It finds any graspable item, including products it never saw in training; WHICH product
    is decided afterwards by vision.identify (gallery) and the tray weights. Detections leave
    here as UNKNOWN.
    """
    status = "TRAINED"

    def __init__(self, weights, device="cpu", conf=0.25):
        if not Path(weights).is_file():
            raise FileNotFoundError("Item detector weights unavailable; train with tools.dataset.cli model train --single-class")
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.device, self.conf = device, conf
        if len(self.model.names) != 1:
            raise ValueError("Item detector must have exactly one class (train with --single-class)")

    def detect(self, image):
        result = self.model.track(image, persist=True, tracker="bytetrack.yaml", device=self.device, conf=self.conf, verbose=False)[0]
        return [Detection("UNKNOWN", float(b.conf.item()), tuple(b.xyxy[0].tolist()), int(b.id.item()) if b.id is not None else None)
                for b in result.boxes]

class OptionalPose:
    """Full-body arm linkage hints only. Pose never proves grasp or customer ownership."""
    def __init__(self, weights="yolo26n-pose.pt", device="cpu", full_body_verified=False):
        if not full_body_verified:
            raise ValueError("Pose linkage requires verified full-body visibility; default shelf close-up does not support it")
        from ultralytics import YOLO
        self.model=YOLO(weights);self.device=device
    def observe(self,image):
        result=self.model.track(image,persist=True,tracker='bytetrack.yaml',device=self.device,verbose=False)[0]
        output=[]
        if result.keypoints is None:return output
        for i,points in enumerate(result.keypoints.data.cpu().tolist()):
            tracks=result.boxes.id
            output.append({'temporary_person_track':int(tracks[i].item()) if tracks is not None else None,'left_arm':[points[j] for j in (5,7,9)],'right_arm':[points[j] for j in (6,8,10)],'ownership':'UNPROVEN'})
        return output
