"""Face redaction for stored evidence frames.

The shelf camera looks down at the trays, so faces are rare, but a shopper leaning in can appear.
Frames with a detected face are blurred before they are written to the evidence store; frames
without one are kept byte-for-byte (no recompression). The metadata of every frame records
whether it was redacted, so reviewers know which frames are originals.
Uses the Haar cascades shipped inside opencv-python: nothing to download.
"""
import numpy as np

class FaceRedactor:
    def __init__(self, scale=1.1, neighbours=5, min_size=24, pad=0.25):
        import cv2
        root = cv2.data.haarcascades
        self.cascades = [cv2.CascadeClassifier(root + name) for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
        self.cascades = [c for c in self.cascades if not c.empty()]
        if not self.cascades:
            raise RuntimeError("OpenCV face cascades unavailable")
        self.scale, self.neighbours, self.min_size, self.pad = scale, neighbours, min_size, pad

    def faces(self, image):
        import cv2
        gray = cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        found = []
        for cascade in self.cascades:
            for (x, y, w, h) in cascade.detectMultiScale(gray, self.scale, self.neighbours, minSize=(self.min_size, self.min_size)):
                found.append((int(x), int(y), int(w), int(h)))
        return found

    def redact_image(self, image):
        import cv2
        faces = self.faces(image)
        H, W = image.shape[:2]
        for x, y, w, h in faces:
            px, py = int(w * self.pad), int(h * self.pad)
            a, b, c, d = max(0, x - px), max(0, y - py), min(W, x + w + px), min(H, y + h + py)
            region = image[b:d, a:c]
            k = max(15, (min(region.shape[:2]) // 3) | 1)
            image[b:d, a:c] = cv2.GaussianBlur(region, (k, k), 0)
        return image, len(faces)

    def __call__(self, jpeg):
        """JPEG bytes in -> (JPEG bytes, faces redacted). Unchanged bytes when no face is found."""
        import cv2
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return jpeg, 0
        image, n = self.redact_image(image)
        if not n:
            return jpeg, 0
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return (buf.tobytes(), n) if ok else (jpeg, 0)
