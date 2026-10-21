from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass
class Detection:
    sku: str
    confidence: float
    box: tuple
    track_id: int | None
    match: object = None  # vision.identify.Match when a gallery labelled this detection

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
    """Item detector (custom YOLO26): one class "item", optionally a second class "hand".

    It finds any graspable item, including products it never saw in training; WHICH product
    is decided afterwards by vision.identify (gallery) and the tray weights. Detections leave
    here as UNKNOWN. With a hand class, hand boxes of the last frame are in `last_hands`
    (used by vision.hands.MotionProof).
    """
    status = "TRAINED"

    def __init__(self, weights, device="cpu", conf=0.25, hand_conf=0.35):
        if not Path(weights).is_file():
            raise FileNotFoundError("Item detector weights unavailable; train with tools.dataset.cli model train --single-class")
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.device, self.conf, self.hand_conf = device, conf, hand_conf
        names = {int(k): str(v).lower() for k, v in self.model.names.items()}
        hand = [k for k, v in names.items() if v == "hand"]
        if len(names) == 1:
            self.hand_class = None
        elif len(names) == 2 and len(hand) == 1:
            self.hand_class = hand[0]
        else:
            raise ValueError("Item detector must have one class (item) or two (item, hand)")
        self.has_hands = self.hand_class is not None
        self.last_hands = []

    def detect(self, image):
        result = self.model.track(image, persist=True, tracker="bytetrack.yaml", device=self.device, conf=min(self.conf, self.hand_conf), verbose=False)[0]
        items, hands = [], []
        for b in result.boxes:
            cls, conf = int(b.cls.item()), float(b.conf.item())
            if cls == self.hand_class:
                if conf >= self.hand_conf:
                    hands.append(tuple(b.xyxy[0].tolist()))
            elif conf >= self.conf:
                items.append(Detection("UNKNOWN", conf, tuple(b.xyxy[0].tolist()), int(b.id.item()) if b.id is not None else None))
        self.last_hands = hands
        return items

class OpenVocabularyFallback:
    """Optional prompt-free open-vocabulary detector (e.g. a YOLOE prompt-free checkpoint).

    Only consulted when the item detector finds nothing while the scale reports a change, so an
    odd object the item detector never learned still gets a box for review. Never identifies.
    """
    status = "TRAINED"

    def __init__(self, weights, device="cpu", conf=0.3, max_area_frac=0.5):
        if not Path(weights).is_file():
            raise FileNotFoundError("Fallback detector weights not found: " + str(weights))
        from ultralytics import YOLO
        self.model, self.device, self.conf, self.max_area_frac = YOLO(weights), device, conf, max_area_frac

    def detect(self, image):
        h, w = image.shape[:2]
        result = self.model.predict(image, device=self.device, conf=self.conf, verbose=False)[0]
        out = []
        for b in result.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            if (x2 - x1) * (y2 - y1) <= self.max_area_frac * w * h:  # not the tray itself
                out.append(Detection("UNKNOWN", float(b.conf.item()), (x1, y1, x2, y2), None))
        return out

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
