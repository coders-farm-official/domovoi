"""Finding and describing faces: YuNet + SFace on ONNX Runtime, in numpy.

No OpenCV and no dlib: YuNet's output is decoded here as OpenCV's
``FaceDetectorYN`` decodes it (``cls``/``obj``/``bbox``/``kps`` per stride
8, 16 and 32; score = sqrt(cls x obj); boxes and landmarks relative to
the anchor cell), then non-maximum suppression; each face is aligned to
112 x 112 from its five landmarks by a similarity transform (Umeyama, the
template ``FaceRecognizerSF.alignCrop`` uses) warped with Pillow, and
SFace turns it into 128 numbers, made unit length.

Everything here is blocking: callers run it off the event loop (the
runner's ``model_step`` in a thread). ONNX Runtime gets one thread
(``intra_op_num_threads=1``), as the design's CPU caps say.

What is kept of a face (:class:`Found`): its box as fractions of the
picture (so any thumbnail can draw it), the detector's score, a quality
figure (score scaled down for faces under 112 px) and the embedding.
Faces under :data:`MIN_FACE_PX` across or scored under :data:`MIN_SCORE`
are dropped (design §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

#: The detector sees the picture at most this big on its long edge.
DETECT_SIZE = 640
#: The picture faces are cut from is decoded at most this big (Pillow
#: draft mode), so the face crops keep detail without decoding 40 MP.
DECODE_SIZE = 1600
MIN_FACE_PX = 40
MIN_SCORE = 0.8
NMS_IOU = 0.3
TOP_K = 50
STRIDES = (8, 16, 32)
EMBED_DIM = 128
ALIGN_SIZE = 112
#: SFace's alignment template (OpenCV FaceRecognizerSF::alignCrop).
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float64)


@dataclass
class Detection:
    box: np.ndarray        # x, y, w, h in the detector's pixels
    landmarks: np.ndarray  # (5, 2), same pixels
    score: float


@dataclass
class Found:
    box: list[float]       # x, y, w, h as fractions of the picture
    score: float
    quality: float
    embedding: np.ndarray  # (128,) float32, unit length


# ─── YuNet ────────────────────────────────────────────────────────────────


def decode_yunet(outputs: dict[str, np.ndarray], width: int, height: int, *,
                 score_threshold: float = MIN_SCORE) -> list[Detection]:
    """YuNet's raw outputs for a ``width`` x ``height`` input (each a
    multiple of 32) as detections, best first, after NMS."""
    boxes, lms, scores = [], [], []
    for s in STRIDES:
        cols, rows = width // s, height // s
        cls = np.clip(np.asarray(outputs[f"cls_{s}"], dtype=np.float32).reshape(-1), 0, 1)
        obj = np.clip(np.asarray(outputs[f"obj_{s}"], dtype=np.float32).reshape(-1), 0, 1)
        bbox = np.asarray(outputs[f"bbox_{s}"], dtype=np.float32).reshape(-1, 4)
        kps = np.asarray(outputs[f"kps_{s}"], dtype=np.float32).reshape(-1, 10)
        n = min(len(cls), rows * cols)
        score = np.sqrt(cls[:n] * obj[:n])
        keep = np.nonzero(score >= score_threshold)[0]
        if not len(keep):
            continue
        r = (keep // cols).astype(np.float32)
        c = (keep % cols).astype(np.float32)
        b = bbox[keep]
        cx, cy = (c + b[:, 0]) * s, (r + b[:, 1]) * s
        w, h = np.exp(b[:, 2]) * s, np.exp(b[:, 3]) * s
        boxes.append(np.stack([cx - w / 2, cy - h / 2, w, h], axis=1))
        k = kps[keep].reshape(-1, 5, 2)
        lms.append(np.stack([(k[:, :, 0] + c[:, None]) * s, (k[:, :, 1] + r[:, None]) * s], axis=2))
        scores.append(score[keep])
    if not boxes:
        return []
    B, L, S = np.concatenate(boxes), np.concatenate(lms), np.concatenate(scores)
    return [Detection(B[i], L[i], float(S[i])) for i in nms(B, S)]


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float = NMS_IOU, top_k: int = TOP_K) -> list[int]:
    order = list(np.argsort(-scores))
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = x1 + boxes[:, 2], y1 + boxes[:, 3]
    area = np.maximum(boxes[:, 2], 0) * np.maximum(boxes[:, 3], 0)
    kept: list[int] = []
    while order and len(kept) < top_k:
        i = order.pop(0)
        kept.append(int(i))
        if not order:
            break
        rest = np.array(order)
        w = np.maximum(0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        h = np.maximum(0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = w * h
        ratio = inter / np.maximum(area[i] + area[rest] - inter, 1e-9)
        order = [j for j, o in zip(rest, ratio) if o <= iou]
    return kept


# ─── alignment ────────────────────────────────────────────────────────────


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """The 2 x 3 similarity (rotation, uniform scale, shift) that best maps
    ``src`` points onto ``dst`` (Umeyama 1991)."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = len(src)
    sm, dm = src.mean(axis=0), dst.mean(axis=0)
    sc, dc = src - sm, dst - dm
    cov = dc.T @ sc / n
    U, S, Vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        d[1] = -1
    R = U @ np.diag(d) @ Vt
    var = (sc ** 2).sum() / n
    scale = float((S * d).sum() / var) if var > 0 else 1.0
    t = dm - scale * R @ sm
    return np.hstack([scale * R, t[:, None]])


def align(image: Any, landmarks: np.ndarray) -> Any:
    """The face cut out and turned upright at 112 x 112 (a Pillow image)."""
    from PIL import Image

    M = similarity_transform(landmarks, TEMPLATE)
    A, t = M[:, :2], M[:, 2]
    Ainv = np.linalg.inv(A)
    off = -Ainv @ t
    data = (Ainv[0, 0], Ainv[0, 1], off[0], Ainv[1, 0], Ainv[1, 1], off[1])
    return image.transform((ALIGN_SIZE, ALIGN_SIZE), Image.AFFINE, data, resample=Image.BILINEAR)


def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# ─── the engine ───────────────────────────────────────────────────────────


def _session(path: Path) -> Any:
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


class FaceEngine:
    """YuNet + SFace. ``detector``/``recognizer`` are ONNX Runtime sessions
    (or anything with ``get_inputs``/``get_outputs``/``run``); by default
    they are opened from the pinned files on first use."""

    def __init__(self, detector: Any = None, recognizer: Any = None, base: Path | None = None) -> None:
        self._det = detector
        self._rec = recognizer
        self._base = base

    def _sessions(self) -> tuple[Any, Any]:
        from domovoi.media_understanding import face_models

        if self._det is None or self._rec is None:
            if not face_models.ready(self._base):
                raise face_models.ModelsUnavailable("the face models are not on disk")
            self._det = self._det or _session(face_models.path_of(face_models.DETECTOR, self._base))
            self._rec = self._rec or _session(face_models.path_of(face_models.RECOGNIZER, self._base))
        return self._det, self._rec

    # The detector: a fixed input size, or any multiple of 32.
    def _detect_size(self, det: Any, w: int, h: int) -> tuple[int, int, float]:
        shape = list(det.get_inputs()[0].shape)
        scale = min(1.0, DETECT_SIZE / max(w, h))
        sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
        fh, fw = (shape[2], shape[3]) if len(shape) == 4 else (None, None)
        if isinstance(fw, int) and isinstance(fh, int) and fw > 0 and fh > 0:
            scale = min(fw / w, fh / h)
            return fw, fh, scale
        return -(-sw // 32) * 32, -(-sh // 32) * 32, scale

    def detect(self, image: Any) -> list[Detection]:
        """Faces in an RGB Pillow image, in that image's pixels."""
        from PIL import Image

        det, _rec = self._sessions()
        w, h = image.size
        iw, ih, scale = self._detect_size(det, w, h)
        sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
        small = image.resize((sw, sh), Image.BILINEAR) if (sw, sh) != (w, h) else image
        canvas = np.zeros((ih, iw, 3), dtype=np.float32)
        arr = np.asarray(small, dtype=np.float32)[: ih, : iw]
        canvas[: arr.shape[0], : arr.shape[1]] = arr
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].copy()        # BGR, NCHW
        names = [o.name for o in det.get_outputs()]
        out = dict(zip(names, det.run(None, {det.get_inputs()[0].name: blob})))
        found = decode_yunet(out, iw, ih)
        for d in found:
            d.box = d.box / scale
            d.landmarks = d.landmarks / scale
        return found

    def embed(self, image: Any, landmarks: np.ndarray) -> np.ndarray:
        _det, rec = self._sessions()
        face = np.asarray(align(image, landmarks), dtype=np.float32)       # RGB
        blob = face.transpose(2, 0, 1)[None].copy()
        out = rec.run(None, {rec.get_inputs()[0].name: blob})[0]
        return unit(out)

    def faces(self, image: Any) -> list[Found]:
        """Every face worth keeping in an RGB Pillow image."""
        w, h = image.size
        out: list[Found] = []
        for d in self.detect(image):
            x, y, bw, bh = (float(v) for v in d.box)
            side = min(bw, bh)
            if side < MIN_FACE_PX or d.score < MIN_SCORE:
                continue
            emb = self.embed(image, d.landmarks)
            if emb.shape != (EMBED_DIM,):
                continue
            x0, y0 = max(0.0, x), max(0.0, y)
            box = [x0 / w, y0 / h, min(bw, w - x0) / w, min(bh, h - y0) / h]
            quality = d.score * min(1.0, side / ALIGN_SIZE)
            out.append(Found([round(v, 5) for v in box], round(d.score, 4), round(quality, 4), emb))
        return out


def load_picture(data: bytes) -> Any:
    """A picture's bytes as an upright RGB Pillow image at most
    :data:`DECODE_SIZE` on its long edge, through ``image_safety``."""
    import io

    from PIL import Image, ImageOps

    from domovoi import image_safety

    with image_safety.open_image(io.BytesIO(data), image_safety.LIBRARY_FORMATS) as im:
        try:
            im.draft("RGB", (DECODE_SIZE, DECODE_SIZE))
        except Exception:  # noqa: BLE001 — draft is only a hint (JPEG)
            pass
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        if max(im.size) > DECODE_SIZE:
            im.thumbnail((DECODE_SIZE, DECODE_SIZE), Image.BILINEAR)
        im.load()
        return im
