"""
FOD Sentry — inference server
==============================
FastAPI backend that streams live webcam frames from the browser over a
WebSocket, runs them through the FOD-A YOLOv8 ONNX model, and streams
detections back in real time.

Design goals for smoothness / low latency:
  * The socket has two independent loops: a `receiver` that just stores the
    *latest* frame the client sent, and a `processor` that runs inference in
    a background thread. If the client sends frames faster than the model
    can run, older frames are simply dropped — we always work on the freshest
    frame, so the stream never "catches up" through a backlog.
  * Inference runs in a ThreadPoolExecutor so the asyncio event loop is never
    blocked and can keep receiving/sending concurrently.
  * ONNXRuntime will use CUDA automatically if it's available, otherwise CPU.
"""

import asyncio
import json
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "best.onnx"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

INPUT_SIZE = 960          # model is exported with a static 960x960 input
LETTERBOX_COLOR = (114, 114, 114)

DEFAULT_CONF = 0.35
DEFAULT_IOU = 0.45

CLASS_NAMES = {
    0: "AdjustableClamp", 1: "AdjustableWrench", 2: "Battery", 3: "Bolt",
    4: "BoltNutSet", 5: "BoltWasher", 6: "ClampPart", 7: "Cutter",
    8: "FuelCap", 9: "Hammer", 10: "Hose", 11: "Label", 12: "LuggagePart",
    13: "LuggageTag", 14: "MetalPart", 15: "MetalSheet", 16: "Nail",
    17: "Nut", 18: "PaintChip", 19: "Pen", 20: "PlasticPart", 21: "Pliers",
    22: "Rock", 23: "Screw", 24: "Screwdriver", 25: "SodaCan", 26: "Tape",
    27: "Washer", 28: "Wire", 29: "Wood", 30: "Wrench",
}

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

# A small dedicated pool keeps inference off the event loop. 1-2 workers is
# usually best: onnxruntime already parallelizes a single inference across
# intra_op_num_threads, so stacking many Python threads on top just adds
# contention rather than throughput.
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ort-infer")

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
# FastAPI app
# --------------------------------------------------------------------------

app = FastAPI(title="FOD Sentry Inference Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": ACTIVE_PROVIDER,
        "input_size": INPUT_SIZE,
        "num_classes": len(CLASS_NAMES),
        "classes": CLASS_NAMES,
        "default_conf": DEFAULT_CONF,
        "default_iou": DEFAULT_IOU,
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_event_loop()

    state = {
        "latest": None,       # (client_ts: float, jpeg_bytes: bytes)
        "processing": False,
        "conf": DEFAULT_CONF,
        "iou": DEFAULT_IOU,
    }
    stop_flag = {"stop": False}

    await ws.send_text(json.dumps({
        "type": "hello",
        "provider": ACTIVE_PROVIDER,
        "input_size": INPUT_SIZE,
        "classes": CLASS_NAMES,
    }))

    async def receiver():
        try:
            while True:
                message = await ws.receive()
                if message.get("bytes") is not None:
                    data = message["bytes"]
                    if len(data) < 9:
                        continue
                    ts = struct.unpack("!d", data[:8])[0]
                    # Always overwrite: only the freshest frame matters.
                    state["latest"] = (ts, data[8:])
                elif message.get("text") is not None:
                    try:
                        msg = json.loads(message["text"])
                    except ValueError:
                        continue
                    if msg.get("type") == "config":
                        if "conf" in msg:
                            state["conf"] = max(0.01, min(0.99, float(msg["conf"])))
                        if "iou" in msg:
                            state["iou"] = max(0.05, min(0.95, float(msg["iou"])))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            stop_flag["stop"] = True

    async def processor():
        while not stop_flag["stop"]:
            if state["latest"] is None or state["processing"]:
                await asyncio.sleep(0.001)
                continue

            ts, jpeg_bytes = state["latest"]
            state["latest"] = None
            state["processing"] = True
            t0 = time.perf_counter()
            try:
                arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                detections = await loop.run_in_executor(
                    executor, run_inference, frame, state["conf"], state["iou"]
                )
                infer_ms = (time.perf_counter() - t0) * 1000.0

                await ws.send_text(json.dumps({
                    "type": "detections",
                    "ts": ts,
                    "infer_ms": round(infer_ms, 1),
                    "detections": detections,
                }))
            except (WebSocketDisconnect, RuntimeError):
                stop_flag["stop"] = True
            except Exception as exc:  # noqa: BLE001
                try:
                    await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
                except Exception:
                    stop_flag["stop"] = True
            finally:
                state["processing"] = False

    recv_task = asyncio.create_task(receiver())
    proc_task = asyncio.create_task(processor())
    try:
        await recv_task
    finally:
        stop_flag["stop"] = True
        proc_task.cancel()
        try:
            await proc_task
        except Exception:
            pass


# Serve the static frontend (index.html, style.css, app.js) at "/"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
