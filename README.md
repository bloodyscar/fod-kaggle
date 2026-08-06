# FOD Sentry — Live Runway Debris Detection + Web Admin Dashboard

A real-time webcam object-detection app built around your trained YOLOv8
ONNX model (`best.onnx`, 31 FOD-A classes — bolts, wrenches, wire, rocks,
tools, etc. at a 960×960 input resolution), plus a MySQL-backed admin
dashboard: risk scoring, inspection tracking, role-based login, charts, a
runway map and a weather widget. FastAPI backend streams detections over a
WebSocket; the frontend is plain HTML/CSS/JS with **no build step**.

```
fod-sentry/
├── backend/
│   ├── main.py            # FastAPI app + WebSocket + static mount
│   ├── config.py          # settings from .env
│   ├── database.py        # engine, SessionLocal, get_db(), create_all()
│   ├── models.py          # 5 tables
│   ├── schemas.py         # Pydantic
│   ├── auth.py            # bcrypt + JWT in an httpOnly cookie
│   ├── classes.py         # the 31 FOD classes (model contract) + severity defaults
│   ├── inference.py       # letterbox / preprocess / postprocess / snapshot
│   ├── preprocess.py      # denoise · CLAHE · sharpen
│   ├── risk.py            # Risk = Likelihood × Severity
│   ├── store.py           # cooldown + writing detections from the live stream
│   ├── weather.py         # Open-Meteo proxy + 10-minute cache
│   ├── dataset_index.py   # samples the FOD-A dataset, 20 frames/class via best.onnx
│   ├── routers/           # auth · users · detections · inspections · dashboard · dataset
│   ├── seed.py            # default users, 31 severity rows, demo data
│   ├── storage/detections/# annotated evidence JPEGs (gitignored)
│   ├── best.onnx          # your model (copied in)
│   ├── requirements.txt
│   └── .env               # NOT committed — copy from .env.example
├── frontend/
│   ├── login.html · dashboard.html · live.html
│   ├── detections.html · inspections.html · users.html · dataset.html
│   └── assets/css/app.css · assets/js/*.js
└── VOC2007/               # FOD-A dataset from Kaggle (gitignored, see §9.1)
    └── JPEGImages/        # 33.793 frames — the Dataset page samples these
```

---

## 1. Setup

### 1.1 Database (MySQL 8.0 / MariaDB 10.6+)

```bash
mysql -u root -p -e "CREATE DATABASE fod_sentry CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

Using **Laragon** or **XAMPP**? Start MySQL from its control panel first. Laragon's
`mysql` client lives at `C:\laragon\bin\mysql\mysql-8.0.30-winx64\bin\mysql.exe` and
its default credentials are user `root` with an empty password.

### 1.2 Dependencies

```bash
cd backend
python -m venv venv && venv\Scripts\activate     # Windows
# python3 -m venv venv && source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 1.3 Configuration

```bash
cp .env.example .env        # Windows: copy .env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"   # paste into JWT_SECRET
```

Then edit `backend/.env`:

| Key | Meaning |
|---|---|
| `DATABASE_URL` | `mysql+pymysql://root:PASSWORD@127.0.0.1:3306/fod_sentry?charset=utf8mb4` (omit `:PASSWORD` if it's empty) |
| `JWT_SECRET` | random string — **change this**, it signs the login cookie |
| `ALLOWED_ORIGINS` | comma-separated origins. Must **not** be `*`, because auth uses cookies |
| `COOKIE_SECURE` | `true` only when serving over HTTPS |
| `COOLDOWN_SECONDS` | anti-duplicate window per FOD class (default `10`) |
| `WEATHER_LAT` / `WEATHER_LON` | default `-3.3667` / `135.4833` (Bandara Douw Aturure, Nabire) |

There is **no weather API key** — Open-Meteo is free and keyless.

### 1.4 Seed

```bash
python seed.py            # tables + default users + 31 severity rows
python seed.py --demo     # ...plus 7 days of demo detections (for a dry run/defence)
```

`seed.py` is idempotent — run it as often as you like. It never overwrites a
severity weight an admin has already tuned. To regenerate demo rows:
`python seed.py --demo --reset-demo`.

### 1.5 Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend too, so
there's nothing else to start. You land on the login page.

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | admin |
| `petugas` | `petugas123` | petugas |

> **Change both default passwords before real use** (Pengguna → Ubah).

> `getUserMedia` (camera access) only works on `localhost` or over **HTTPS**.
> `http://localhost:8000` is fine for local testing. For any other host,
> see the HTTPS/WSS section below.

---

## 2. Roles & permissions

| Capability | admin | petugas |
|---|:--:|:--:|
| Dashboard, charts, runway map, weather | ✅ | ✅ |
| Live detection (`/ws`) | ✅ | ✅ |
| Read detection history + detail + snapshot | ✅ | ✅ |
| Update inspection status / notes | ✅ | ✅ |
| Browse the dataset gallery | ✅ | ✅ |
| Delete a detection | ✅ | ❌ |
| User CRUD | ✅ | ❌ |
| Edit FOD severity weights | ✅ | ❌ |
| Re-index the dataset gallery | ✅ | ❌ |

Enforced **server-side** on every endpoint. Hiding the *Pengguna* menu item is
only a convenience — a `petugas` who types the URL gets a 403 from the API.

---

## 3. How the risk score works

```
Risk = Likelihood × Severity          →  1..25  →  5 levels  →  recommendation
```

**Likelihood** comes from the detector's confidence:

| Confidence | ≥0.85 | ≥0.70 | ≥0.55 | ≥0.45 | else |
|---|:--:|:--:|:--:|:--:|:--:|
| **L** | 5 | 4 | 3 | 2 | 1 |

**Severity** is the per-class weight in `fod_classes` (1–5, admin editable —
hard metal like `Bolt` or `Wrench` defaults to 5, paper/film like `Label` or
`Tape` to 1).

| Score | Level | Action |
|:--:|---|---|
| 20–25 | **Critical** | Emergency runway cleaning before the next flight |
| 13–19 | **High** | Clean the runway before the next flight |
| 7–12 | **Medium** | Verify on the ground as soon as possible |
| 3–6 | **Low** | Monitor at the next scheduled inspection |
| 1–2 | **Very Low** | Monitor at the next scheduled inspection |

Editing a severity weight affects **new** detections only — scores already
stored are not recalculated.

### Anti-duplicate

A 20 fps stream would otherwise write ~20 rows a second per visible object.
Instead, at most **one row per FOD class per 10 seconds** is saved, using the
highest-confidence box of that class in the frame. So a bolt held in front of
the camera for 30 s produces ~3 rows, not 600. Writes happen on a separate
thread pool and are never awaited, so they add no inference latency.

### Notifications

There is no notifications table. The bell is a live query: detections whose
risk level is **High** or **Critical** *and* whose inspection is still `open`.
Marking an inspection `proses` or `selesai` is what makes an alert disappear.
The frontend polls every 15 s and beeps once for each newly-seen Critical.

---

## 4. GPU acceleration (recommended)

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

## 5. How it stays smooth

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
  upload resolution, JPEG quality, a max send-rate cap, and three optional
  server-side pre-processing filters (denoise / CLAHE / sharpen).

## 6. Running over HTTPS / WSS (remote camera access)

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
self-signed cert — that's expected for local testing). Also set
`COOKIE_SECURE=true` and add the origin to `ALLOWED_ORIGINS` in `.env`.

**For real deployment**, put the app behind a reverse proxy (nginx, Caddy,
Traefik) with a proper TLS certificate (e.g. Let's Encrypt), proxying both
HTTP and the `/ws` WebSocket upgrade to `uvicorn`.

---

## 7. API reference

All REST endpoints are under `/api` and require the login cookie.
`A` = admin only, `A P` = both roles.

| Method | Path | Access | Purpose |
|---|---|:--:|---|
| POST | `/api/auth/login` | — | set the JWT cookie |
| POST | `/api/auth/logout` | A P | clear the cookie |
| GET | `/api/auth/me` | A P | current user + role |
| GET | `/api/dashboard/summary` | A P | KPIs: today, per level, open, avg response |
| GET | `/api/dashboard/charts` | A P | 7-day bar + risk doughnut in one request |
| GET | `/api/detections` | A P | list + filters (`date_from`, `date_to`, `class_id`, `risk_level`, `status`) + pagination |
| GET | `/api/detections/{id}` | A P | detail incl. risk + inspection |
| GET | `/api/detections/{id}/snapshot` | A P | the annotated evidence JPEG |
| GET | `/api/detections/map?hours=24` | A P | points for the runway map |
| DELETE | `/api/detections/{id}` | **A** | delete (cascades to risk + inspection + JPEG) |
| GET | `/api/inspections` | A P | handling history (`?status=`) |
| PATCH | `/api/inspections/{id}` | A P | change `status`, `notes`, `handled_by` |
| GET | `/api/notifications` | A P | 10 open High/Critical alerts + `unread_count` |
| GET | `/api/weather` | A P | Nabire weather (Open-Meteo, cached 10 min) |
| GET | `/api/dataset` | A P | sample dataset frames (`class_name`, `limit`, `offset`) + indexer status |
| GET | `/api/dataset/status` | A P | indexer progress only (cheap poll) |
| GET | `/api/dataset/image/{file}` | A P | one dataset frame JPEG |
| POST | `/api/dataset/reindex` | **A** | drop the sample index and scan again |
| GET/POST/PATCH/DELETE | `/api/users`, `/api/users/{id}` | **A** | user CRUD |
| GET | `/api/fod-classes` | A P | 31 classes + severity weights |
| PATCH | `/api/fod-classes/{id}` | **A** | change a severity weight |
| GET | `/health` | — | model provider, input size, class list, defaults |

Interactive docs while the server runs: **http://localhost:8000/docs**

### WebSocket `/ws`

Requires the login cookie; an invalid session is closed with code **4401**.

- **Client → server (binary):** `8-byte big-endian float64 timestamp` +
  `raw JPEG bytes`.
- **Client → server (text, optional):**
  `{"type":"config","conf":0.35,"iou":0.45,"denoise":false,"clahe":false,"sharpen":false,"camera_label":"Logitech C920 (external)"}`
- **Server → client:**
  `{"type":"detections","ts":<echoed timestamp>,"infer_ms":<float>,"detections":[{"x1","y1","x2","y2" (normalized 0–1),"conf","class_id","class_name","risk_level","risk_score"}]}`

---

## 8. Database schema (5 tables)

Created automatically by `Base.metadata.create_all()` — there is no Alembic.
While still in development, a schema change means `DROP DATABASE fod_sentry`
then re-`CREATE` and re-seed.

| Table | Purpose |
|---|---|
| `users` | login credentials, `role` = `admin` \| `petugas` |
| `fod_classes` | severity weight per class. `id` **is** the model's `class_id` (0–30) |
| `fod_detections` | class, confidence, normalised bbox, camera label, snapshot path, timestamp |
| `risk_assessments` | 1:1 with a detection — L, S, score, level, recommendation |
| `inspections` | 1:1 with a detection — status, handler, notes, response time |

⚠️ The order of `FOD_CLASSES` in `backend/classes.py` is a **contract with
`best.onnx`**: the list index is the `class_id` the model emits. Reordering or
inserting entries without replacing the model would mislabel every stored
detection.

---

## 9. Dataset gallery (menu *Dataset*)

The **Dataset** page browses the FOD-A training set the model came from, so you
can eyeball what each of the 31 classes actually looks like — and what
`best.onnx` calls them.

### 9.1 Getting the data

The dataset is **not** in the repository (33.793 frames, ~412 MB — `VOC2007/` is
gitignored). Download it from Kaggle:

```python
# pip install kagglehub
import kagglehub
path = kagglehub.dataset_download(
    "imenesabeur/dataset-for-foreign-object-debris-in-airports"
)
print(path)   # .../versions/1/FODPascalVOCFormat-V.2.1/VOC2007
```

No Kaggle API token is needed — the dataset is public. Copy (or symlink) the
`VOC2007` folder to the **project root**, so the layout is:

```
fod-sentry/
├── VOC2007/
│   ├── JPEGImages/     # 33.793 frames, 300x300  ← the only folder we read
│   ├── Annotations/    # VOC XML — deliberately ignored, see below
│   └── ImageSets/
├── backend/
└── frontend/
```

Without it the page still loads and says the folder is missing; nothing else
breaks.

### 9.2 Why the labels come from the model, not the XML

The dataset ships Pascal-VOC XML labels, but the gallery ignores them and runs
**`best.onnx`** on each frame instead. The point of the page is to show what
*our* model sees, which makes it a quick sanity check on the deployed weights:
a class whose samples look wrong is a class the model is weak on. Each tile
shows the predicted class and confidence; clicking one draws the predicted box
over the frame.

### 9.3 Why it shows a sample, not 33.793 images

Running the model over the whole set would take hours, and no browser wants
33.793 thumbnails. So `backend/dataset_index.py` keeps **20 frames per class**
(620 total) and the page pages through that with `limit`/`offset`. The header
always states the full frame count and how many are *not* shown.

Two details make the sampling cheap and useful:

- **Early exit.** Indexing stops as soon as every class has its 20 frames, or
  after `DATASET_SCAN_LIMIT` frames — whichever comes first.
- **Strided walk.** Consecutive filenames are consecutive *video frames of the
  same object* (the VOC XML carries a `track_id`), so a sequential scan would
  fill one class and starve the rest. The indexer walks the file list in a fixed
  coprime stride instead — deterministic, resumable, and it spreads the scan
  across the whole dataset. In practice the first handful of frames already hit
  a different class each.

Indexing starts **lazily** on the first request to `/api/dataset` and runs on a
background thread, so server boot stays fast and live detection keeps priority
(`DATASET_INDEX_SLEEP` is the breather between frames). The result is cached in
`backend/storage/dataset_index.json`, so it is built once; a run interrupted
half-way resumes where it stopped. While it works, the page polls every 4 s and
fills in.

Tuning (all optional, `backend/.env`):

| Key | Default | Meaning |
|---|---|---|
| `DATASET_PER_CLASS` | `20` | frames kept per class |
| `DATASET_SCAN_LIMIT` | `6000` | hard stop on frames scanned |
| `DATASET_MIN_CONF` | `0.45` | below this the frame is skipped, not filed |
| `DATASET_INDEX_SLEEP` | `0.01` | seconds between frames |

Replaced `best.onnx`? An admin can hit **Indeks ulang** on the page (or
`POST /api/dataset/reindex`) to rebuild the sample with the new weights.

---

## Notes

- The ONNX model was exported without built-in NMS (`nms=False` in its
  metadata), so non-max suppression runs server-side, per class, using
  OpenCV.
- Detection coordinates are sent normalized (0–1) so they scale correctly
  to any display size without the backend needing to know your canvas
  resolution.
- Runway map marker positions are a **linear** mapping of the bbox centre onto
  the SVG — there is no perspective calibration, which is why the UI labels
  them "posisi estimasi".
- Chart.js loads from a CDN. Offline, the dashboard says so instead of showing
  two blank boxes; to fix it permanently, download `chart.umd.min.js` into
  `frontend/assets/vendor/` and change the `<script src>` in `dashboard.html`.
- `backend/.env` and `backend/storage/` are gitignored — secrets and evidence
  images stay out of the repository. So is `VOC2007/`: the Kaggle dataset is
  412 MB of downloaded data, not source (§9.1 has the one-liner to fetch it).
- Dataset frames are served through `/api/dataset/image/{file}` rather than a
  StaticFiles mount, for the same reason as detection snapshots — a mount would
  also expose `Annotations/` and `ImageSets/`, and we only ever want the JPEGs.
