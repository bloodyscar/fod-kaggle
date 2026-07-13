# FOD Sentry — Live Runway Debris Detection

A real-time webcam object-detection app built around your trained YOLOv8
ONNX model (`best.onnx`, 31 FOD-A classes — bolts, wrenches, wire, rocks,
tools, etc. at a 960×960 input resolution). FastAPI backend streams
detections over a WebSocket; the frontend is plain HTML/CSS/JS with no
build step.

```
fod-sentry/
├── backend/
│   ├── main.py            # FastAPI app + ONNXRuntime inference + WebSocket
│   ├── requirements.txt
│   └── best.onnx           # your model (copied in)
└── frontend/
    ├── index.html
    ├── style.css
    └── app.js
```

## 1. Install & run

```bash
cd backend
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — the backend also serves the frontend, so
there's nothing else to start. Pick a camera (built-in or external/USB) and
press **Start scanning**.

> `getUserMedia` (camera access) only works on `localhost` or over **HTTPS**.
> `http://localhost:8000` is fine for local testing. For any other host,
> see the WSS/HTTPS section below.

## 2. GPU acceleration (recommended)

The model runs at a fixed 960×960 input, which is fairly heavy for CPU-only
inference. If you have an NVIDIA GPU + CUDA/cuDNN installed:

```bash
pip uninstall onnxruntime
pip install onnxruntime-gpu
```

Restart the server — it auto-detects `CUDAExecutionProvider` and uses it
automatically (check the **ENGINE** chip in the top bar to confirm). No code
changes needed. On CPU-only machines, expect roughly 100–400 ms per frame
depending on core count; on a modern GPU, expect well under 30 ms.

## 3. How it stays smooth

- **One frame in flight at a time.** The browser only sends a new frame
  after the previous one's result comes back (or times out). This means the
  send rate automatically adapts to your hardware's actual inference speed —
  no backlog, no growing lag.
- **Always-freshest-frame processing.** The server keeps only the *latest*
  frame it received; if frames arrive faster than it can process them
  (shouldn't happen given the above, but protects against bursts), older
  ones are dropped rather than queued.
- **Decoupled render loop.** The video canvas redraws every animation frame
  (~60 fps) regardless of network/inference timing, so playback never
  looks choppy — only the bounding boxes update at the detection rate.
- **Tunable trade-offs** in the side panel: confidence/IoU thresholds,
  upload resolution, JPEG quality, and a max send-rate cap.

## 4. Running over HTTPS / WSS (remote camera access)

Browsers block camera access on plain HTTP for any host other than
`localhost`. To use this from another device on your network, or over the
internet, serve it over HTTPS so the frontend can open a `wss://` socket
(the frontend already auto-switches between `ws://` and `wss://` based on
the page's protocol — no code changes needed).

**Quick local test with a self-signed cert:**

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 365 \
  -subj "/CN=localhost"

uvicorn main:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Then open `https://<your-ip>:8443` (your browser will warn about the
self-signed cert — that's expected for local testing).

**For real deployment**, put the app behind a reverse proxy (nginx, Caddy,
Traefik) with a proper TLS certificate (e.g. Let's Encrypt), proxying both
HTTP and the `/ws` WebSocket upgrade to `uvicorn`.

## 5. API reference

- `GET /health` — model provider, input size, class list, default thresholds.
- `WS /ws` — binary frames in, JSON detections out.
  - **Client → server (binary):** `8-byte big-endian float64 timestamp` +
    `raw JPEG bytes`.
  - **Client → server (text, optional):**
    `{"type": "config", "conf": 0.35, "iou": 0.45}` to change thresholds live.
  - **Server → client:**
    `{"type": "detections", "ts": <echoed timestamp>, "infer_ms": <float>, "detections": [{"x1","y1","x2","y2" (normalized 0–1), "conf", "class_id", "class_name"}]}`

## Notes

- The ONNX model was exported without built-in NMS (`nms=False` in its
  metadata), so non-max suppression runs server-side, per class, using
  OpenCV.
- Detection coordinates are sent normalized (0–1) so they scale correctly
  to any display size without the backend needing to know your canvas
  resolution.
