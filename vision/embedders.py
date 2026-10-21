"""Interchangeable frozen image embedders for the recognition gallery.

Every embedder maps BGR crops to L2-normalised float32 vectors and has a `name` that is stored
in the gallery, so a gallery can never be queried with a different model by mistake.

  dinov2-small   facebook/dinov2-small   (default; CLS + mean patch token)
  dinov2-base    facebook/dinov2-base
  siglip2-base   google/siglip2-base-patch16-224 (image tower only)
  clip-base      openai/clip-vit-base-patch16    (image tower only)
  dinov3-small   facebook/dinov3-vits16-pretrain-lvd1689m (gated on Hugging Face and needs a
                 newer transformers; reported as unavailable when it cannot be loaded)

Which one is best for THIS shelf is measured with tools.dataset.embedder_bench, never assumed.
Cosine similarities of different models are on different scales: thresholds do not transfer.
"""
import numpy as np

MODELS = {
    "dinov2-small": ("dino", "facebook/dinov2-small"),
    "dinov2-base": ("dino", "facebook/dinov2-base"),
    "dinov3-small": ("dino", "facebook/dinov3-vits16-pretrain-lvd1689m"),
    "siglip2-base": ("siglip", "google/siglip2-base-patch16-224"),
    "clip-base": ("clip", "openai/clip-vit-base-patch16"),
}

class EmbedderUnavailable(RuntimeError):
    pass

class HFEmbedder:
    """Hugging Face vision backbone, frozen, inference only."""

    def __init__(self, key, device="cpu"):
        if key not in MODELS:
            raise ValueError(f"Unknown embedder {key!r}; choose from {sorted(MODELS)}")
        import torch
        from transformers import AutoImageProcessor, AutoModel
        self.torch, self.device, self.key = torch, device, key
        self.kind, self.name = MODELS[key]
        try:
            self.processor = AutoImageProcessor.from_pretrained(self.name)
            self.model = AutoModel.from_pretrained(self.name).to(device).eval()
        except Exception as exc:  # gated repo, missing architecture in this transformers, offline
            raise EmbedderUnavailable(f"{key}: {type(exc).__name__}: {str(exc)[:200]}") from exc
        self._dim = None

    def embed(self, images_bgr):
        if not len(images_bgr):
            return np.zeros((0, self.dim), np.float32)
        torch = self.torch
        rgb = [np.ascontiguousarray(img[:, :, ::-1]) for img in images_bgr]
        with torch.inference_mode():
            inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
            if self.kind == "dino":
                hidden = self.model(**inputs).last_hidden_state
                vectors = torch.cat([hidden[:, 0], hidden[:, 1:].mean(1)], dim=1)
            else:  # contrastive models: the projected image embedding
                vectors = self.model.get_image_features(**inputs)
            vectors = torch.nn.functional.normalize(vectors.float(), dim=1)
        return vectors.cpu().numpy().astype(np.float32)

    @property
    def dim(self):
        if self._dim is None:
            self._dim = int(self.embed([np.zeros((32, 32, 3), np.uint8)]).shape[1])
        return self._dim

def make_embedder(key_or_name, device="cpu"):
    """Accepts a short key (dinov2-small) or a stored gallery name (facebook/dinov2-small)."""
    if key_or_name in MODELS:
        key = key_or_name
    else:
        key = next((k for k, (_, name) in MODELS.items() if name == key_or_name), None)
        if key is None:
            raise ValueError(f"No embedder registered for {key_or_name!r}")
    if key == "dinov2-small":
        from vision.identify import Dinov2Embedder  # unchanged default: existing galleries stay valid
        return Dinov2Embedder(device)
    return HFEmbedder(key, device)
