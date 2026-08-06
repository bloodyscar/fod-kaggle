"""
FOD Sentry — inference server + web admin API
=============================================
FastAPI backend that streams live webcam frames from the browser over a
WebSocket, runs them through the FOD-A YOLOv8 ONNX model, streams detections
back in real time, and records the noteworthy ones (with a risk score) to MySQL.

Design goals for smoothness / low latency:
  * The socket has two independent loops: a `receiver` that just stores the
    *latest* frame the client sent, and a `processor` that runs inference in
    a background thread. If the client sends frames faster than the model
    can run, older frames are simply dropped — we always work on the freshest
    frame, so the stream never "catches up" through a backlog.
  * Inference runs in a ThreadPoolExecutor so the asyncio event loop is never
    blocked and can keep receiving/sending concurrently.
  * DB writes go to their own single-worker pool and are never awaited, so a
    slow INSERT can't add latency to the next frame.
  * ONNXRuntime will use CUDA automatically if it's available, otherwise CPU.
"""

import asyncio
import json
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import dataset_index
import store
from auth import authenticate_ws
from classes import CLASS_NAMES
from config import FRONTEND_DIR, STORAGE_DIR, settings
from database import SessionLocal, create_all
from inference import ACTIVE_PROVIDER, INPUT_SIZE, run_inference
from preprocess import apply_chain
from routers import auth as auth_router
from routers import dashboard as dashboard_router
from routers import dataset as dataset_router
from routers import detections as detections_router
from routers import inspections as inspections_router
from routers import users as users_router

# A small dedicated pool keeps inference off the event loop. 1-2 workers is
# usually best: onnxruntime already parallelizes a single inference across
# intra_op_num_threads, so stacking many Python threads on top just adds
# contention rather than throughput.
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ort-infer")
# Separate pool so a DB write never queues behind (or ahead of) an inference.
db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")


@asynccontextmanager
async def lifespan(_: FastAPI):
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        create_all()
        print("[startup] database ready")
    except Exception as exc:  # noqa: BLE001
        # The live page and /health still work without a DB; the dashboard
        # endpoints will surface the error instead of the app refusing to boot.
        print(f"[startup] WARNING database unavailable: {exc}")
    # Reuse a previous run's dataset sample index. Indexing itself is lazy —
    # the first request to /api/dataset kicks it off, so boot stays fast.
    dataset_index.load_cached()
    yield
    executor.shutdown(wait=False)
    db_executor.shutdown(wait=False)


app = FastAPI(title="FOD Sentry", lifespan=lifespan)

# Explicit origin list: "*" together with credentials is rejected by browsers,
# and we authenticate with a cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (
    auth_router.router,
    users_router.router,
    detections_router.router,
    inspections_router.router,
    dashboard_router.router,
    dataset_router.router,
):
    app.include_router(r, prefix="/api")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": ACTIVE_PROVIDER,
        "input_size": INPUT_SIZE,
        "num_classes": len(CLASS_NAMES),
        "classes": CLASS_NAMES,
        "default_conf": settings.default_conf,
        "default_iou": settings.default_iou,
    }


@app.get("/", include_in_schema=False)
async def root():
    # Registered before the StaticFiles mount, so it wins over it.
    return RedirectResponse(url="/dashboard.html")


def _infer_with_preprocess(frame, conf, iou, flags):
    """Pre-processing + inference in one executor hop (plan §4.4)."""
    if any(flags):
        frame = apply_chain(
            frame,
            use_denoise=flags[0], use_clahe=flags[1], use_sharpen=flags[2],
        )
    return frame, run_inference(frame, conf, iou)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # Auth from the handshake cookie. We accept first so the browser actually
    # surfaces the close code instead of a generic handshake failure.
    with SessionLocal() as db:
        try:
            user = authenticate_ws(ws, db)
        except Exception:      # DB down — don't hand out an unauthenticated stream
            user = None
    if user is None:
        await ws.close(code=4401, reason="Sesi tidak valid, silakan login ulang")
        return

    loop = asyncio.get_event_loop()
    store.reset_cooldown()

    state = {
        "latest": None,       # (client_ts: float, jpeg_bytes: bytes)
        "processing": False,
        "conf": settings.default_conf,
        "iou": settings.default_iou,
        "denoise": False,
        "clahe": False,
        "sharpen": False,
        "camera_label": None,
        "db_busy": False,
    }
    stop_flag = {"stop": False}

    await ws.send_text(json.dumps({
        "type": "hello",
        "provider": ACTIVE_PROVIDER,
        "input_size": INPUT_SIZE,
        "classes": CLASS_NAMES,
        "user": {"username": user.username, "role": user.role},
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
                        for flag in ("denoise", "clahe", "sharpen"):
                            if flag in msg:
                                state[flag] = bool(msg[flag])
                        if "camera_label" in msg:
                            label = str(msg["camera_label"] or "")[:120]
                            state["camera_label"] = label or None
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            stop_flag["stop"] = True

    def _release_db(_fut):
        state["db_busy"] = False

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

                flags = (state["denoise"], state["clahe"], state["sharpen"])
                processed, detections = await loop.run_in_executor(
                    executor, _infer_with_preprocess,
                    frame, state["conf"], state["iou"], flags,
                )
                infer_ms = (time.perf_counter() - t0) * 1000.0

                # Risk badge on every box the operator sees…
                try:
                    store.annotate_risk(detections)
                except Exception:
                    pass    # DB unreachable: stream on without badges

                # …but only cooldown survivors get written. Fire and forget, and
                # never more than one write in flight.
                if detections and not state["db_busy"]:
                    state["db_busy"] = True
                    fut = loop.run_in_executor(
                        db_executor, store.persist_detections,
                        processed, detections, state["camera_label"],
                    )
                    fut.add_done_callback(_release_db)

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


# --------------------------------------------------------------------------
# HTML pages: served through here (not StaticFiles) so every local asset ref
# gets a "?v=<mtime>" cache-buster stamped in on the fly. The browser then
# re-fetches app.css / *.js the moment the file on disk changes, and keeps
# using its cached copy otherwise. No build step, nothing to bump by hand.
# --------------------------------------------------------------------------
_ASSET_REF_RE = re.compile(r'((?:href|src)=")(assets/[^"?]+)(")')


def _asset_version(rel_path: str) -> str:
    """Cache-buster token for a frontend asset: its mtime, or 0 if missing."""
    try:
        return str(int((FRONTEND_DIR / rel_path).stat().st_mtime))
    except OSError:
        return "0"


@app.get("/{page}.html", include_in_schema=False)
async def serve_page(page: str):
    path = FRONTEND_DIR / f"{page}.html"
    if not path.is_file():
        return HTMLResponse("Not Found", status_code=404)

    html = _ASSET_REF_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}?v={_asset_version(m.group(2))}{m.group(3)}",
        path.read_text(encoding="utf-8"),
    )
    # The HTML itself must never be cached, otherwise the stale copy would keep
    # pointing at the old ?v= values and the whole scheme is moot.
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# Serve the static frontend (assets + anything else) at "/".
# Mounted last so /api, /health, /ws, "/" and the .html routes keep priority.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
