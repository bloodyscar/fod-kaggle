"""
ONNX YOLOv8 inference — moved verbatim out of main.py (plan §7 Fase 3).

Behaviour is unchanged from the original prototype: static 960x960 letterboxed
input, per-class NMS via cv2.dnn.NMSBoxes, normalised 0-1 boxes on the way out.
The only edit is that CLASS_NAMES now comes from classes.py so the class order
is defined in exactly one place.
"""

import os
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from classes import CLASS_NAMES
from config import MODEL_PATH, STORAGE_DIR, settings

INPUT_SIZE = settings.input_size
LETTERBOX_COLOR = (114, 114, 114)


# --------------------------------------------------------------------------
# Model session
# --------------------------------------------------------------------------


def build_session() -> ort.InferenceSession:
    available = ort.get_available_providers()
    providers = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    providers.append("CPUExecutionProvider")

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = max(1, (os.cpu_count() or 4) - 1)
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    return ort.InferenceSession(str(MODEL_PATH), sess_options=so, providers=providers)


session = build_session()
INPUT_NAME = session.get_inputs()[0].name
ACTIVE_PROVIDER = session.get_providers()[0]


# --------------------------------------------------------------------------
# Pre / post processing
# --------------------------------------------------------------------------


def letterbox(img: np.ndarray, size: int = INPUT_SIZE):
    h, w = img.shape[:2]
    scale = min(size / h, size / w)
    nh, nw = int(round(h * scale)), int(round(w * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)

    canvas = np.full((size, size, 3), LETTERBOX_COLOR, dtype=np.uint8)
    top = (size - nh) // 2
    left = (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, scale, left, top


def preprocess(frame: np.ndarray):
    canvas, scale, left, top = letterbox(frame, INPUT_SIZE)
    img = canvas[:, :, ::-1].astype(np.float32) / 255.0   # BGR -> RGB, 0..1
    img = img.transpose(2, 0, 1)[None, ...]                # HWC -> NCHW
    return np.ascontiguousarray(img), scale, left, top


def postprocess(output, scale, left, top, orig_w, orig_h, conf_thres, iou_thres):
    preds = output[0][0].T   # (1,35,18900) -> (18900,35)

    boxes_xywh = preds[:, :4]
    class_scores = preds[:, 4:]
    class_ids = np.argmax(class_scores, axis=1)
    confs = class_scores[np.arange(class_scores.shape[0]), class_ids]

    mask = confs >= conf_thres
    if not np.any(mask):
        return []

    boxes_xywh = boxes_xywh[mask]
    confs = confs[mask]
    class_ids = class_ids[mask]

    cx, cy, w, h = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
    x1 = cx - w / 2
    y1 = cy - h / 2
    x2 = cx + w / 2
    y2 = cy + h / 2

    # undo letterbox padding/scale to map back to the original frame
    x1 = (x1 - left) / scale
    y1 = (y1 - top) / scale
    x2 = (x2 - left) / scale
    y2 = (y2 - top) / scale

    x1 = np.clip(x1, 0, orig_w)
    y1 = np.clip(y1, 0, orig_h)
    x2 = np.clip(x2, 0, orig_w)
    y2 = np.clip(y2, 0, orig_h)

    boxes_for_nms = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1)

    results = []
    for cid in np.unique(class_ids):
        cls_mask = class_ids == cid
        cls_boxes = boxes_for_nms[cls_mask]
        cls_confs = confs[cls_mask]

        idxs = cv2.dnn.NMSBoxes(
            cls_boxes.tolist(), cls_confs.tolist(), conf_thres, iou_thres
        )
        if len(idxs) == 0:
            continue
        idxs = np.array(idxs).flatten()

        for i in idxs:
            bx, by, bw, bh = cls_boxes[i]
            results.append({
                "x1": float(bx / orig_w),
                "y1": float(by / orig_h),
                "x2": float((bx + bw) / orig_w),
                "y2": float((by + bh) / orig_h),
                "conf": float(cls_confs[i]),
                "class_id": int(cid),
                "class_name": CLASS_NAMES.get(int(cid), str(cid)),
            })

    results.sort(key=lambda d: -d["conf"])
    return results


def run_inference(frame: np.ndarray, conf_thres: float, iou_thres: float):
    orig_h, orig_w = frame.shape[:2]
    tensor, scale, left, top = preprocess(frame)
    outputs = session.run(None, {INPUT_NAME: tensor})
    return postprocess(outputs, scale, left, top, orig_w, orig_h, conf_thres, iou_thres)


# --------------------------------------------------------------------------
# Annotated snapshot (evidence for the detections table)
# --------------------------------------------------------------------------

# Same golden-angle hue spread the frontend canvas uses, so a saved JPEG looks
# like what the operator saw live.
def _color_for_class(class_id: int) -> tuple[int, int, int]:
    hue = int((class_id * 137.508) % 360)
    hsv = np.uint8([[[hue // 2, 209, 158]]])            # OpenCV hue is 0-179
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def save_snapshot(
    frame: np.ndarray,
    detection: dict,
    risk_level: str,
    when: datetime | None = None,
) -> str | None:
    """Write one annotated JPEG and return its path relative to backend/.

    Returns None on any failure — a missing snapshot must never block the DB
    insert or the live stream.
    """
    if not settings.save_snapshots:
        return None
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        img = frame.copy()
        h, w = img.shape[:2]

        x1 = int(detection["x1"] * w)
        y1 = int(detection["y1"] * h)
        x2 = int(detection["x2"] * w)
        y2 = int(detection["y2"] * h)
        color = _color_for_class(detection["class_id"])

        cv2.rectangle(img, (x1, y1), (x2, y2), color, max(2, w // 400))

        label = (
            f"{detection['class_name']} {detection['conf'] * 100:.0f}% | {risk_level}"
        )
        scale_f = max(0.5, w / 1200)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale_f, 2)
        ty = max(th + 6, y1)
        cv2.rectangle(img, (x1, ty - th - 6), (x1 + tw + 8, ty + 2), color, -1)
        cv2.putText(
            img, label, (x1 + 4, ty - 4),
            cv2.FONT_HERSHEY_SIMPLEX, scale_f, (10, 13, 18), 2, cv2.LINE_AA,
        )

        stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        filename = f"{stamp}_{detection['class_name']}.jpg"
        out_path = STORAGE_DIR / filename
        if not cv2.imwrite(str(out_path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 82]):
            return None
        return str(Path("storage/detections") / filename).replace("\\", "/")
    except Exception:
        return None
