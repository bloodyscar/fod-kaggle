# 🗺️ Rencana Pengembangan — FOD Sentry Web Admin Dashboard (Versi Sederhana)

> Rencana teknis menambahkan **web admin dashboard + MySQL** ke prototipe
> **FOD Sentry** yang sudah ada — sesuai [`list-belum-ada.md`](./list-belum-ada.md).
> Prinsip dokumen ini: **CRUD sederhana, tanpa over-engineering.**

- **Dibuat:** 3 Agustus 2026
- **Database:** MySQL 8.0 / MariaDB 10.6+ (utf8mb4)
- **Stack:** FastAPI + SQLAlchemy (sinkron) · Frontend vanilla HTML/CSS/JS (tanpa build step)
- **API cuaca:** [Open-Meteo](https://api.open-meteo.com/v1/forecast) — gratis, **tanpa API key**
- **Sumber kamera:** **hanya webcam browser** — kamera internal laptop **atau** webcam eksternal/USB, dipilih dari dropdown (mekanisme ini sudah ada di kode)
- **Estimasi total:** ~7–8 hari kerja

---

## 1. Kondisi Kode Saat Ini

### 1.1 Backend — `backend/main.py` (312 baris, 1 file)

| Aspek | Kondisi |
|---|---|
| Framework | FastAPI + Uvicorn |
| Model | `best.onnx` YOLOv8, input statis **960×960**, **31 kelas** FOD-A |
| Inference | ONNXRuntime, auto CUDA → fallback CPU (`build_session()`) |
| Pipeline | `letterbox()` → `preprocess()` → `session.run()` → `postprocess()` (NMS per kelas, `cv2.dnn.NMSBoxes`) |
| Transport | `WS /ws` — `receiver()` simpan **frame terbaru saja**, `processor()` jalankan inference di `ThreadPoolExecutor` |
| Output | bbox **ternormalisasi 0–1** + `conf`, `class_id`, `class_name`, `infer_ms` |
| REST | hanya `GET /health` |
| Static | `StaticFiles(frontend, html=True)` di-mount pada `/` |

**Belum ada:** database, login, penyimpanan hasil, penilaian risiko, notifikasi, pre-processing citra.

⚠️ **Satu bug yang harus diperbaiki lebih dulu:** `main.py:189-195` memakai `allow_origins=["*"]` bersama `allow_credentials=True`. Kombinasi ini ditolak browser begitu login berbasis cookie dipakai.

### 1.2 Frontend — `frontend/` (index 163 · app.js 536 · style.css 461)

| Aspek | Kondisi |
|---|---|
| Arsitektur | 1 halaman, vanilla JS dalam IIFE |
| **Pemilihan kamera** | ✅ **sudah ada** — `populateCameraList()` (`app.js:88`) memakai `enumerateDevices()`, filter `videoinput`, dan sudah menandai `(external)` untuk perangkat USB (`app.js:106`). **Cukup dipertahankan, tidak perlu dibangun ulang** |
| Design token | rapi di `:root` — `--bg:#0a0d12`, `--panel:#10141b`, `--amber:#ffb627`, `--teal:#35e0c7`, `--red:#ff5457` + font Barlow/Inter/JetBrains Mono |
| Fitur | start/stop, slider conf/IoU/resolusi/kualitas/FPS, canvas reticle, panel Active Detections, Detection Log (hilang saat refresh) |

### 1.3 Keputusan Penyederhanaan

| Yang **dibuang** dari rencana awal | Alasan |
|---|---|
| ❌ Koneksi RTSP / IP Camera + MJPEG stream | Kamera cukup webcam internal/eksternal via browser (permintaan Anda). Menghapus ini juga menghapus tabel `cameras`, enkripsi Fernet, thread worker, reconnect backoff |
| ❌ Alembic (migrasi) | Pakai `Base.metadata.create_all()` sekali jalan. Kalau skema berubah: `DROP DATABASE` + seed ulang (masih tahap pengembangan) |
| ❌ Tabel `audit_logs`, `settings`, `weather_cache`, `notifications` | Konstanta cukup di file Python; cuaca di-cache pakai variabel dict; notifikasi **diturunkan dari query** deteksi High/Critical yang belum ditangani → 0 tabel tambahan |
| ❌ SSE real-time | Polling `setInterval` 15 detik. Cukup untuk 1–5 pengguna, kodenya ~5 baris |
| ❌ Kalibrasi perspektif 4 titik (`cv2.getPerspectiveTransform`) | Peta runway pakai pemetaan linear titik tengah bbox → SVG, diberi label "estimasi" |
| ❌ Event tracker IoU + thumbnail + retensi + ekspor PDF + audit viewer | Dedup diganti **cooldown 10 detik per kelas** (~6 baris kode) |
| ✅ Yang **tetap ada** | Semua 13 item di `list-belum-ada.md` kecuali RTSP — hanya dengan implementasi paling ringkas |

| # | Keputusan teknis | Alasan |
|---|---|---|
| 1 | Vanilla JS multi-halaman, tanpa React/bundler | Konsisten dengan kode existing & janji "no build step" di README |
| 2 | SQLAlchemy sinkron + PyMySQL, endpoint pakai `def` | FastAPI menjalankan `def` di threadpool otomatis → tidak blocking, tanpa ribet `async` driver |
| 3 | JWT di **httpOnly cookie** | Cookie ikut terkirim saat navigasi halaman **dan** saat handshake WebSocket — tidak perlu menyisipkan header manual |
| 4 | Penyimpanan dilakukan **di backend** (dalam `processor()`) | Data tidak bisa dipalsukan dari browser |
| 5 | Kolom status pakai `VARCHAR(20)`, bukan `ENUM` MySQL | Lebih mudah diubah, validasi di Pydantic |

---

## 2. Arsitektur Target

```mermaid
flowchart LR
  C[Webcam Browser<br/>internal / eksternal USB] -->|WS /ws| P[Pre-processing<br/>opsional]
  P --> I[ONNX YOLOv8]
  I --> K[Cooldown 10s<br/>anti-duplikat]
  K --> R[Risk = L x S]
  R --> DB[(MySQL<br/>5 tabel)]
  R --> S[Snapshot JPEG]
  DB --> API[REST API + login]
  API --> UI[Dashboard · Deteksi<br/>Inspeksi · Users]
```

### 2.1 Struktur Direktori Target

```
fod-sentry/
├── backend/
│   ├── main.py              # app + router + mount static (tetap 1 entry point)
│   ├── config.py            # baca .env
│   ├── database.py          # engine, SessionLocal, get_db(), create_all()
│   ├── models.py            # 5 model SQLAlchemy — SATU file
│   ├── schemas.py           # Pydantic — SATU file
│   ├── auth.py              # hash password, JWT, get_current_user, require_admin
│   ├── inference.py         # dipindah dari main.py (letterbox/pre/postprocess)
│   ├── preprocess.py        # denoise · CLAHE · sharpen (3 fungsi kecil)
│   ├── risk.py              # risk engine + decision engine (1 file, ~60 baris)
│   ├── weather.py           # proxy Open-Meteo (tanpa API key) + cache dict
│   ├── routers/
│   │   ├── auth.py  users.py  detections.py
│   │   └── inspections.py  dashboard.py
│   ├── seed.py              # admin default + 31 severity + data demo
│   ├── storage/detections/*.jpg
│   ├── best.onnx
│   ├── requirements.txt
│   └── .env
└── frontend/
    ├── login.html
    ├── dashboard.html       # halaman utama
    ├── live.html            # eks index.html (deteksi real-time)
    ├── detections.html      # tabel riwayat FOD
    ├── inspections.html     # penanganan
    ├── users.html           # CRUD user (admin)
    └── assets/
        ├── css/app.css      # token existing + sidebar + komponen
        └── js/api.js · layout.js · dashboard.js · live.js
                · detections.js · inspections.js · users.js
```

Total file backend baru: **~14**. Halaman frontend: **6**.

---

## 3. Skema Database (5 Tabel)

`utf8mb4`, dibuat otomatis oleh `Base.metadata.create_all()`.

### 3.1 `users`

| Kolom | Tipe | Catatan |
|---|---|---|
| `id` | INT PK AI | |
| `username` | VARCHAR(50) UNIQUE | login |
| `full_name` | VARCHAR(100) | |
| `password_hash` | VARCHAR(255) | bcrypt |
| `role` | VARCHAR(20) | `admin` \| `petugas` |
| `is_active` | BOOL default 1 | |
| `created_at` | DATETIME | |

### 3.2 `fod_classes` — bobot Severity (seed 31 baris, editable admin)

`id` INT PK (= `class_id` 0–30, **bukan** auto-increment), `name` VARCHAR(50), `severity_weight` TINYINT 1–5.

#### 3.2.1 Daftar 31 kelas (urutan = output model)

⚠️ **Urutan list ini adalah kontrak dengan model** — index array = `class_id` yang dikeluarkan `best.onnx`, dan sudah sama persis dengan `CLASS_NAMES` di `backend/main.py:48-56`. **Jangan diurutkan ulang / disisipi** tanpa mengganti model, karena `fod_detections.class_id` akan salah label.

```python
# backend/seed.py — urutan HARUS sama dengan CLASS_NAMES (main.py:48)
FOD_CLASSES = [
    'AdjustableClamp', 'AdjustableWrench', 'Battery', 'Bolt', 'BoltNutSet',
    'BoltWasher', 'ClampPart', 'Cutter', 'FuelCap', 'Hammer', 'Hose',
    'Label', 'LuggagePart', 'LuggageTag', 'MetalPart', 'MetalSheet',
    'Nail', 'Nut', 'PaintChip', 'Pen', 'PlasticPart', 'Pliers', 'Rock',
    'Screw', 'Screwdriver', 'SodaCan', 'Tape', 'Washer', 'Wire', 'Wood',
    'Wrench',
]
assert len(FOD_CLASSES) == 31
```

> Sebaiknya `CLASS_NAMES` di `main.py` diganti jadi turunan dari list ini
> (`CLASS_NAMES = dict(enumerate(FOD_CLASSES))`) agar satu sumber kebenaran —
> dipindahkan ke `inference.py` saat Fase 3.

#### 3.2.2 Bobot Severity default

Nilai default (5 = paling merusak mesin/roda — logika ICAO/FAA: metal keras ≫ plastik/kertas):

| Severity | Kelas |
|:--:|---|
| **5** | `AdjustableWrench`, `Bolt`, `BoltNutSet`, `BoltWasher`, `Hammer`, `MetalPart`, `MetalSheet`, `Pliers`, `Screwdriver`, `Wrench` |
| **4** | `AdjustableClamp`, `Battery`, `ClampPart`, `Cutter`, `Nail`, `Nut`, `Rock`, `Screw`, `Wire` |
| **3** | `FuelCap`, `Hose`, `SodaCan`, `Washer`, `Wood` |
| **2** | `LuggagePart`, `Pen`, `PlasticPart` |
| **1** | `Label`, `LuggageTag`, `PaintChip`, `Tape` |

#### 3.2.3 Baris seed final (`id` = `class_id`)

| id | name | S | id | name | S |
|:--:|---|:--:|:--:|---|:--:|
| 0 | `AdjustableClamp` | 4 | 16 | `Nail` | 4 |
| 1 | `AdjustableWrench` | 5 | 17 | `Nut` | 4 |
| 2 | `Battery` | 4 | 18 | `PaintChip` | 1 |
| 3 | `Bolt` | 5 | 19 | `Pen` | 2 |
| 4 | `BoltNutSet` | 5 | 20 | `PlasticPart` | 2 |
| 5 | `BoltWasher` | 5 | 21 | `Pliers` | 5 |
| 6 | `ClampPart` | 4 | 22 | `Rock` | 4 |
| 7 | `Cutter` | 4 | 23 | `Screw` | 4 |
| 8 | `FuelCap` | 3 | 24 | `Screwdriver` | 5 |
| 9 | `Hammer` | 5 | 25 | `SodaCan` | 3 |
| 10 | `Hose` | 3 | 26 | `Tape` | 1 |
| 11 | `Label` | 1 | 27 | `Washer` | 3 |
| 12 | `LuggagePart` | 2 | 28 | `Wire` | 4 |
| 13 | `LuggageTag` | 1 | 29 | `Wood` | 3 |
| 14 | `MetalPart` | 5 | 30 | `Wrench` | 5 |
| 15 | `MetalSheet` | 5 | | | |

Seed bersifat **idempoten** — pakai `session.merge(FodClass(id=i, name=n, severity_weight=w))`
supaya `seed.py` boleh dijalankan berulang tanpa duplikat/error, dan **tidak menimpa**
bobot yang sudah diubah admin (opsional: hanya `INSERT` bila `id` belum ada).

### 3.3 `fod_detections`

| Kolom | Tipe | Catatan |
|---|---|---|
| `id` | BIGINT PK AI | |
| `class_id` | INT FK → `fod_classes.id` | |
| `class_name` | VARCHAR(50) | denormalisasi, hemat JOIN |
| `confidence` | FLOAT | |
| `x1`,`y1`,`x2`,`y2` | FLOAT | ternormalisasi 0–1 (sama seperti output WS sekarang) |
| `camera_label` | VARCHAR(120) | **nama device webcam** yang dipakai (mis. "HD Webcam (external)") — dikirim client saat start |
| `image_path` | VARCHAR(255) | snapshot beranotasi |
| `detected_at` | DATETIME | index |

> Tidak ada tabel `cameras` — karena kamera dipilih di browser, cukup simpan labelnya sebagai teks.

### 3.4 `risk_assessments`

`id` BIGINT PK, `detection_id` FK **UNIQUE** (`ON DELETE CASCADE`), `likelihood` TINYINT 1–5, `severity` TINYINT 1–5, `risk_score` TINYINT 1–25, `risk_level` VARCHAR(20), `recommendation` TEXT, `created_at`. Index `(risk_level)`.

### 3.5 `inspections`

`id` BIGINT PK, `detection_id` FK (`ON DELETE CASCADE`), `status` VARCHAR(20) — `open` \| `proses` \| `selesai`, `handled_by` FK `users.id` NULL, `notes` TEXT NULL, `started_at` DATETIME NULL, `completed_at` DATETIME NULL, `response_time_seconds` INT NULL (`started_at − detected_at`), `created_at`/`updated_at`. Index `(status)`.

Setiap deteksi baru otomatis membuat 1 baris `inspections` berstatus `open`.

---

## 4. Logika Inti (Ringkas)

### 4.1 Risk Engine — `Risk = Likelihood × Severity` (`risk.py`)

**Severity (S)** = `fod_classes.severity_weight` (1–5), dibaca dari DB.

**Likelihood (L)** — sederhana, murni dari confidence score:

```python
def likelihood(conf: float) -> int:
    if conf >= 0.85: return 5
    if conf >= 0.70: return 4
    if conf >= 0.55: return 3
    if conf >= 0.45: return 2
    return 1
```

**Klasifikasi `risk_score = L × S` (1–25):**

| Skor | Level | Warna UI |
|:--:|---|---|
| 20–25 | **Critical** | `#ff5457` (`--red`) |
| 13–19 | **High** | `#ff8a3d` |
| 7–12 | **Medium** | `#ffb627` (`--amber`) |
| 3–6 | **Low** | `#35e0c7` (`--teal`) |
| 1–2 | **Very Low** | `#8b93a3` (`--text-dim`) |

### 4.2 Decision Engine (dict mapping, bukan kelas/rule engine)

| Level | Rekomendasi | Efek |
|---|---|---|
| Very Low / Low | "Pantau pada inspeksi terjadwal berikutnya." | hanya tercatat |
| Medium | "Lakukan verifikasi/inspeksi visual lapangan secepatnya." | muncul di daftar notifikasi |
| High | "Lakukan pembersihan runway sebelum penerbangan berikutnya." | notifikasi + badge merah |
| Critical | "PEMBERSIHAN DARURAT SEGERA sebelum penerbangan berikutnya." | notifikasi + banner + beep |

Teks final: `f"{class_name} terdeteksi (skor {score}/25 — {level}). {aksi}"`.

### 4.3 Anti-Duplikat: Cooldown 10 Detik

Stream 20 fps akan menulis ~20 baris/detik per objek. Solusi paling sederhana yang cukup:

```python
COOLDOWN_SECONDS = 10
last_saved: dict[int, float] = {}   # {class_id: monotonic_ts}

def should_save(class_id: int, now: float) -> bool:
    if now - last_saved.get(class_id, 0) < COOLDOWN_SECONDS:
        return False
    last_saved[class_id] = now
    return True
```

Simpan hanya deteksi dengan **confidence tertinggi** per kelas dalam satu frame. Insert dijalankan lewat `run_in_executor` agar tidak menambah latensi inference.

### 4.4 Pre-processing (3 fungsi, checkbox di halaman live)

| Toggle | Implementasi | Biaya |
|---|---|---|
| Noise reduction | `cv2.medianBlur(img, 3)` — hindari `fastNlMeansDenoising` (terlalu berat) | ~2 ms |
| Contrast (CLAHE) | CLAHE pada kanal L di ruang LAB, `clipLimit=2.0`, `tile=8×8` | ~5 ms |
| Sharpening | `addWeighted(img, 1.5, GaussianBlur(img,(0,0),3), -0.5, 0)` | ~3 ms |

Dikontrol lewat pesan WS yang sudah ada: `{"type":"config","conf":..,"iou":..,"denoise":true,"clahe":true,"sharpen":false}`. Backend hanya perlu membaca 3 field tambahan di `receiver()`.

### 4.5 Notifikasi (tanpa tabel, tanpa SSE)

- `GET /api/notifications` → `SELECT` deteksi dengan `risk_level IN ('High','Critical')` **AND** `inspections.status = 'open'`, urut terbaru, limit 10 + `unread_count`.
- Frontend: `setInterval(load, 15000)` → ikon 🔔 + badge angka di topbar, dropdown 10 terbaru, beep `AudioContext` bila ada Critical baru.
- Notifikasi otomatis "hilang" saat inspeksi ditandai `proses`/`selesai` — tidak perlu kolom `is_read`.

### 4.6 Peta Runway (SVG statis + marker)

- SVG runway 2D digambar manual di HTML (persegi panjang + centerline dash + label threshold).
- Posisi marker: titik tengah bbox `((x1+x2)/2, (y1+y2)/2)` dipetakan linear ke area SVG. Diberi catatan **"posisi estimasi (belum terkalibrasi)"** agar tidak menyesatkan.
- Warna marker = risk level, klik → tooltip nama objek + waktu + skor.

### 4.7 Widget Cuaca — Open-Meteo (tanpa API key)

Sumber: **`https://api.open-meteo.com/v1/forecast`** — gratis untuk penggunaan non-komersial, **tidak perlu API key / registrasi**, sehingga item "API key" hilang dari daftar konfirmasi dan `.env` tidak perlu menyimpan rahasia tambahan.

Koordinat Bandara Douw Aturure, Nabire: **`lat=-3.3667`, `lon=135.4833`** (disimpan sebagai konstanta di `config.py`, bukan hardcode di `weather.py`).

```python
# backend/weather.py
import time, httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
PARAMS = {
    "latitude": -3.3667,
    "longitude": 135.4833,
    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
               "precipitation,weather_code,wind_speed_10m,wind_direction_10m,visibility",
    "wind_speed_unit": "ms",      # m/s — satuan penerbangan, bukan km/h
    "timezone": "Asia/Jayapura",  # WIT (UTC+9)
}

TTL = 600                          # 10 menit
_cache: dict = {"data": None, "ts": 0.0}

def get_weather() -> dict:
    now = time.monotonic()
    if _cache["data"] and now - _cache["ts"] < TTL:
        return {**_cache["data"], "stale": False}
    try:
        r = httpx.get(OPEN_METEO_URL, params=PARAMS, timeout=6.0)
        r.raise_for_status()
        cur = r.json()["current"]
        data = {
            "temperature": cur["temperature_2m"],          # °C
            "feels_like": cur["apparent_temperature"],     # °C
            "humidity": cur["relative_humidity_2m"],       # %
            "wind_speed": cur["wind_speed_10m"],           # m/s
            "wind_direction": cur["wind_direction_10m"],   # derajat
            "precipitation": cur["precipitation"],         # mm
            "visibility_km": round(cur["visibility"] / 1000, 1),
            "condition": WMO_CODES.get(cur["weather_code"], "Tidak diketahui"),
            "observed_at": cur["time"],                    # ISO lokal WIT
        }
        _cache.update(data=data, ts=now)
        return {**data, "stale": False}
    except Exception:
        if _cache["data"]:
            return {**_cache["data"], "stale": True}       # UI beri label "data lama"
        return {"error": "unavailable"}                    # UI tampilkan "—"
```

**Contoh respons Open-Meteo** (diverifikasi 3 Agu 2026, `current`):

```json
{"time":"2026-08-03T19:00","temperature_2m":28.2,"relative_humidity_2m":73,
 "apparent_temperature":33.6,"precipitation":0.00,"weather_code":3,
 "wind_speed_10m":0.11,"wind_direction_10m":117,"visibility":57080.00}
```

> Catatan unit: `visibility` dalam **meter**, `precipitation` dalam **mm**, `wind_direction_10m` dalam **derajat** (0 = utara). `weather_code` adalah **kode WMO**, bukan teks — harus dipetakan sendiri (Open-Meteo tidak mengirim deskripsi seperti OpenWeatherMap).

**`WMO_CODES` — mapping minimal (dict di `weather.py`):**

| Kode | Teks UI | Kode | Teks UI |
|:--:|---|:--:|---|
| 0 | Cerah | 61 / 63 / 65 | Hujan ringan / sedang / lebat |
| 1 / 2 / 3 | Cerah berawan / Berawan / Berawan tebal | 80 / 81 / 82 | Hujan lokal ringan / sedang / lebat |
| 45 / 48 | Berkabut / Kabut beku | 95 | Badai petir |
| 51 / 53 / 55 | Gerimis ringan / sedang / lebat | 96 / 99 | Badai petir + hujan es |

Kode lain → fallback `"Tidak diketahui"` (jangan `KeyError`).

Tampilan di widget dashboard: **suhu**, **kelembapan**, **kecepatan + arah angin**, **kondisi**, plus **visibilitas** (relevan untuk operasi runway). Bila gagal/offline: data cache terakhir dengan label "data lama", atau "—" bila cache masih kosong.

### 4.8 Grafik (Chart.js via CDN)

- **Bar chart** — jumlah temuan FOD 7 hari terakhir.
- **Doughnut chart** — distribusi 5 level risiko.

Keduanya dari `GET /api/dashboard/charts` (satu endpoint, dua array) supaya cuma 1 request.

---

## 5. Ringkasan CRUD

| Entitas | C | R | U | D | Halaman | Akses |
|---|:-:|:-:|:-:|:-:|---|---|
| **Users** | ✅ | ✅ | ✅ | ✅ | `users.html` | **admin saja** |
| **FOD Classes** (severity) | — | ✅ | ✅ | — | modal di `users.html` / `dashboard.html` | **admin saja** |
| **Detections** | otomatis (sistem) | ✅ | — | ✅ | `detections.html` | R: semua · D: admin |
| **Inspections** | otomatis (ikut deteksi) | ✅ | ✅ | — | `inspections.html` | admin + petugas |
| **Risk Assessments** | otomatis (sistem) | ✅ | — | ikut deteksi | tampil di detail | semua |

CRUD manual sesungguhnya hanya **Users** (penuh) + **Inspections** (update status) + **FOD Classes** (update bobot) + **hapus Detections**. Sisanya dihasilkan sistem.

---

## 6. Kontrak API (14 Endpoint)

Prefix `/api`. `A` = admin, `P` = petugas.

| Method | Path | Akses | Fungsi |
|---|---|:--:|---|
| POST | `/auth/login` | — | set cookie JWT |
| POST | `/auth/logout` | A P | hapus cookie |
| GET | `/auth/me` | A P | user aktif + role (untuk sembunyikan menu admin) |
| GET | `/dashboard/summary` | A P | KPI: total hari ini, per level, inspeksi open, rata-rata waktu respon |
| GET | `/dashboard/charts` | A P | data bar 7 hari + doughnut distribusi risiko |
| GET | `/detections` | A P | list + filter (tanggal, kelas, level, status) + paginasi |
| GET | `/detections/{id}` | A P | detail + risk + inspection |
| DELETE | `/detections/{id}` | **A** | hapus (cascade ke risk + inspection) |
| GET | `/detections/map?hours=24` | A P | titik untuk runway map |
| GET | `/inspections` | A P | list riwayat penanganan |
| PATCH | `/inspections/{id}` | A P | ubah `status`, `notes`, `handled_by` |
| GET | `/notifications` | A P | 10 alert High/Critical yang masih `open` + `unread_count` |
| GET | `/weather` | A P | proxy cuaca Nabire (Open-Meteo, cache 10 mnt) |
| GET/POST/PATCH/DELETE | `/users` · `/users/{id}` | **A** | CRUD pengguna |
| GET/PATCH | `/fod-classes` · `/fod-classes/{id}` | **A** | lihat/ubah bobot severity |
| GET | `/health` | — | (existing, dipertahankan) |
| WS | `/ws` | A P | (existing) + auth cookie + simpan ke DB |

---

## 7. Rencana Eksekusi (5 Fase · ~7–8 hari)

Setiap fase harus meninggalkan aplikasi **tetap jalan**.

### 🔹 Fase 1 — Database & Setup · ~1 hari

- [x] `pip install "sqlalchemy>=2.0" pymysql "passlib[bcrypt]" pyjwt pydantic-settings httpx` → update `requirements.txt`
- [x] `CREATE DATABASE fod_sentry CHARACTER SET utf8mb4;`
- [x] `config.py` (baca `.env`) + `database.py` (`create_engine(..., pool_pre_ping=True)`, `SessionLocal`, `get_db()`)
- [x] `models.py` — 5 model (§3) · `schemas.py` — Pydantic
- [x] **Perbaiki CORS** di `main.py`: `allow_origins` dari `.env`, bukan `["*"]`
- [x] `seed.py` — admin (`admin`/`admin123`), petugas contoh, konstanta `FOD_CLASSES` (§3.2.1) + 31 baris `fod_classes` dengan `id` = index list & bobot dari §3.2.3, insert via `merge()` (idempoten)
- [x] **Verifikasi:** `COUNT(*) FROM fod_classes` = 31; `id,name` cocok baris-per-baris dengan `FOD_CLASSES` (`classes.py`); `seed.py` dijalankan 2× → tetap 31 baris; halaman live masih normal
  - ⚠️ **Diverifikasi di SQLite**, belum di MySQL — MySQL Laragon belum dinyalakan saat implementasi. Ulangi `python seed.py --demo` setelah MySQL hidup.

### 🔹 Fase 2 — Login & Role · ~1 hari

- [x] `auth.py` — bcrypt, JWT HS256 (exp 8 jam), `get_current_user` (baca cookie), `require_admin`
- [x] `routers/auth.py` + `routers/users.py` (CRUD lengkap, tidak boleh hapus akun sendiri)
- [x] Cek cookie di handshake `/ws` → tutup dengan code `4401` bila invalid
- [x] `login.html` (tema sama) + `assets/js/api.js` (wrapper `fetch` + auto-redirect saat 401)
- [x] Menu admin disembunyikan di UI **dan** ditolak di server
- [x] **Verifikasi:** petugas akses `/api/users` → **403** (juga POST `/users`, PATCH `/fod-classes`, DELETE `/detections`); tanpa cookie semua endpoint → **401**; login salah → 401; tidak bisa hapus/nonaktifkan akun sendiri → 400
  - ⚠️ Redirect `dashboard.html` → login diuji pada level API (401 dari `/auth/me`); **belum dicek di browser** karena ekstensi Chrome tidak tersambung

### 🔹 Fase 3 — Risk Engine & Penyimpanan · ~2 hari

- [x] `risk.py` — `likelihood()`, `classify()`, `recommend()` (§4.1–4.2)
- [x] `inference.py` — pindahkan `letterbox`/`preprocess`/`postprocess` + `CLASS_NAMES` dari `main.py` (tanpa ubah perilaku); jadikan `CLASS_NAMES = dict(enumerate(FOD_CLASSES))` agar urutan kelas hanya didefinisikan di satu tempat (§3.2.1)
- [x] `preprocess.py` — 3 fungsi (§4.4) + baca 3 field baru di pesan `config` WS
- [x] Cooldown 10 detik (§4.3) + simpan snapshot beranotasi ke `storage/detections/`
- [x] Di `processor()`: hitung risk → insert `fod_detections` + `risk_assessments` + `inspections('open')` via `run_in_executor`
- [x] Kirim `risk_level` + `risk_score` di payload WS agar badge muncul di canvas live
- [x] `routers/detections.py` + `routers/inspections.py`
- [x] **Verifikasi:** cooldown disimulasikan 600 frame @20 fps selama 30 detik → **3 baris** (bukan 600); `Bolt` conf 0.93 × S=5 → skor 25 → Critical; JPEG beranotasi 7,7 KB tersimpan di `storage/detections/`
  - ⚠️ Diuji dengan frame sintetis, **belum dengan baut sungguhan di depan webcam** — perlu dicoba manual di `live.html`.

### 🔹 Fase 4 — Dashboard Frontend · ~2,5 hari

- [x] `assets/css/app.css` — perluas token existing: sidebar, tabel, badge risk level, sentuhan glassmorphism (`backdrop-filter: blur(14px)` + border `rgba(255,255,255,.08)`) di atas palet yang sudah ada — **palet jangan diganti**
- [x] `layout.js` — inject sidebar + topbar + ikon 🔔 ke semua halaman (hindari duplikasi HTML)
- [x] `dashboard.html` — 4 KPI card · bar 7 hari + doughnut risiko (Chart.js) · runway map SVG · widget cuaca · tabel 10 inspeksi terakhir
- [x] `notifications` polling 15 s + badge + beep untuk Critical (§4.5)
- [x] `detections.html` — tabel + filter + paginasi + modal detail (snapshot, skor, rekomendasi) + tombol hapus (admin)
- [x] `inspections.html` — tabel + dropdown ubah status `open → proses → selesai` + catatan + tampil waktu respon
- [x] `users.html` — CRUD user + editor bobot severity 31 kelas
- [x] `live.html` — `index.html` sekarang + 3 checkbox pre-processing + badge risk pada tiap box (**dropdown pilih kamera internal/eksternal tetap seperti sekarang**)
- [x] **Verifikasi:** 33 pemeriksaan — semua angka `/dashboard/summary`, `/dashboard/charts`, `/notifications` cocok baris-per-baris dengan query SQL manual; invarian `risk_score = L×S` dan pemetaan level dicek pada **seluruh** baris; tidak ada baris orphan
  - ⚠️ Yang dibandingkan adalah **respons API vs SQL**; rendering DOM (Chart.js, marker SVG) **belum dilihat di browser**

### 🔹 Fase 5 — Cuaca, Data Demo & Dokumentasi · ~1 hari

- [x] `weather.py` + `GET /api/weather` (§4.7) — Open-Meteo, `WMO_CODES`, cache TTL 10 menit
- [x] **Verifikasi cuaca:** matikan internet → widget menampilkan cache terakhir berlabel "data lama"; hapus cache + offline → tampil "—" (tidak error 500)
- [x] Tambahkan generator data demo di `seed.py --demo` — 7 hari data realistis untuk keperluan demo sidang tanpa harus menaruh FOD sungguhan di runway
- [x] Update `README.md`: setup MySQL, seed, kredensial default, tabel hak akses per peran
- [x] Keamanan: `JWT_SECRET` acak, `.gitignore` untuk `.env` + `storage/`, ganti password admin default
- [ ] **Verifikasi:** clone bersih → ikuti README → aplikasi jalan penuh — **belum dilakukan** (butuh MySQL hidup + clone terpisah)

---

## 8. Matriks Keterlacakan (`list-belum-ada.md`)

| # | Item TODO | Fase | Implementasi ringkas |
|:--:|---|:--:|---|
| 1.1 | Modul Penilaian Risiko | 3 | `risk.py` — `L(conf) × S(kelas)` → 5 level |
| 1.2 | Intelligent Decision Support | 3 | dict mapping level → teks rekomendasi |
| 1.3 | Notification Manager (ikon notif) | 3 / 4 | query High+Critical `open` + polling 15 s + 🔔 |
| 1.4 | Pre-processing tambahan | 3 | `preprocess.py` — medianBlur, CLAHE, unsharp |
| 1.5 | Koneksi IP Camera RTSP | — | **dibatalkan** — kamera internal/eksternal via browser |
| 2.1 | Konfigurasi DB relasional (MySQL) | 1 | SQLAlchemy + PyMySQL, `create_all()` |
| 2.2 | Skema tabel (`users`, `fod_detections`, `risk_assessments`, `inspections`) | 1 | ✅ keempatnya + `fod_classes` |
| 2.3 | User & Role Management | 2 | JWT cookie, `admin` / `petugas`, `users.html` |
| 3.1 | Layout dashboard premium | 4 | token existing + sidebar + glassmorphism |
| 3.2 | Peta runway interaktif | 4 | SVG + marker linear (label "estimasi") |
| 3.3 | Widget cuaca real-time | 5 | proxy **Open-Meteo** (tanpa API key) + cache 10 menit |
| 3.4 | Grafik Chart.js (bar 7 hari + pie risiko) | 4 | `/api/dashboard/charts` |
| 3.5 | Tabel riwayat inspeksi | 4 | `inspections.html` + status Selesai/Proses |

**12 dari 13 item terpenuhi.** Item 1.5 (RTSP) dibatalkan atas permintaan — sumber kamera cukup webcam internal laptop atau webcam eksternal USB, yang dipilih lewat dropdown existing.

---

## 9. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Deteksi duplikat membanjiri DB | Cooldown 10 detik per kelas (§4.3) — dibuat **sebelum** insert diaktifkan |
| Insert DB memperlambat inference | Insert lewat `run_in_executor`, hanya saat lolos cooldown |
| Skema berubah di tengah jalan (tanpa Alembic) | Masih tahap pengembangan: `DROP DATABASE` + `seed.py` ulang. Pertimbangkan Alembic hanya bila sudah ada data produksi |
| Posisi marker runway tidak akurat | Diberi label "posisi estimasi" secara eksplisit di UI |
| CPU-only 100–400 ms/frame | Slider FPS cap sudah ada; `onnxruntime-gpu` sudah didokumentasikan di README |
| Tanpa internet → Chart.js CDN gagal | Bila jadi masalah: unduh `chart.umd.min.js` ke `assets/vendor/` (1 baris ganti `src`) |
| API cuaca gagal / tanpa internet | Tampilkan cache terakhir + label "data lama"; `timeout=6s` agar tidak menggantung dashboard |
| Rate limit Open-Meteo (~10k request/hari, non-komersial) | Cache 10 menit di server → maks ~144 request/hari, jauh di bawah batas. Semua klien memanggil `/api/weather`, **bukan** Open-Meteo langsung |

---

## 10. Perlu Dikonfirmasi

1. **Kredensial MySQL** — `backend/.env` sudah diisi default Laragon (`root`, password kosong). **Nyalakan MySQL di Laragon**, lalu `CREATE DATABASE fod_sentry` + `python seed.py --demo`. Bila password root Anda tidak kosong, ubah `DATABASE_URL`.
2. ~~API key cuaca~~ — **tidak perlu lagi.** Open-Meteo (`https://api.open-meteo.com/v1/forecast`) tidak memakai API key, jadi widget cuaca bisa langsung jalan tanpa registrasi.
3. **Dimensi runway Bandara Douw Aturure (Nabire)** — untuk proporsi gambar SVG. Sementara dipakai rasio placeholder. Koordinat cuaca sudah dipakai `-3.3667, 135.4833`; konfirmasi bila ingin titik yang lebih presisi.
4. **Multi-user atau single-user?** Bila hanya dipakai 1 orang saat demo, halaman `users.html` bisa dipersempit lagi (tapi item 2.3 TODO memintanya, jadi saya tetap masukkan).

---

## 11. Langkah Pertama

```powershell
# 1. Database
mysql -u root -p -e "CREATE DATABASE fod_sentry CHARACTER SET utf8mb4;"

# 2. Dependency
cd backend
pip install "sqlalchemy>=2.0" pymysql "passlib[bcrypt]" pyjwt pydantic-settings httpx
pip freeze > requirements.txt

# 3. backend/.env
#    DATABASE_URL=mysql+pymysql://root:PASSWORD@localhost:3306/fod_sentry?charset=utf8mb4
#    JWT_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(48))">
#    ALLOWED_ORIGINS=http://localhost:8000
#    WEATHER_LAT=-3.3667
#    WEATHER_LON=135.4833
#    (tidak ada API key cuaca — Open-Meteo bebas kunci)
```

Lanjut ke **Fase 1**.
