Jangan copy backend/ saja. config.py:15 menurunkan FRONTEND_DIR = BASE_DIR.parent / "frontend", dan main.py:269-270 y
Interrupted · What should Claude do instead?

❯ saya ingin install @backend\ di VM, apa saja yang dibutuhkan untuk menjalankan backend dan frontend?
Listed directory backend\

Asumsi saya: VM Linux (Ubuntu 22.04/24.04). Kalau ternyata Windows Server, bilang saja — beberapa bagian berubah.

1. Hal paling krusial: jangan copy backend/ saja

config.py:15 menurunkan path frontend dari lokasi backend:

FRONTEND_DIR = BASE_DIR.parent / "frontend"

dan main.py:270 me-mount folder itu sebagai static files. Jadi frontend/ wajib ikut, sebagai folder sejajar backend/:

/opt/fod-sentry/
├── backend/     ← termasuk best.onnx (43 MB)
└── frontend/

Kalau frontend/ tidak ada, server tetap jalan tapi hanya /api, /health, /ws — semua halaman UI 404. Tidak ada build step di frontend, dan semua URL-nya relatif (/api, /ws, /health), jadi tidak ada yang perlu dikonfigurasi di sisi frontend.

2. Paket OS

sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
                    mysql-server libglib2.0-0 ca-certificates

- libglib2.0-0 — dibutuhkan opencv-python-headless di image Ubuntu minimal/slim.
- ca-certificates — weather.py:18 memanggil https://api.open-meteo.com lewat httpx.
- Tidak perlu compiler atau libmysqlclient-dev: PyMySQL murni Python, dan onnxruntime/bcrypt punya wheel manylinux.
- Python 3.10 minimum (kode pakai sintaks X | None yang dievaluasi runtime); 3.11 yang sudah teruji.

3. Database

sudo mysql -e "CREATE DATABASE fod_sentry CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
sudo mysql -e "CREATE USER 'fod'@'localhost' IDENTIFIED BY 'password-kuat';
               GRANT ALL ON fod_sentry.* TO 'fod'@'localhost';"

Tabel dibuat otomatis saat startup (create_all()), tidak ada Alembic.

4. Aplikasi

cd /opt/fod-sentry/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"   # → JWT_SECRET
python seed.py          # user default + 31 fod_classes
mkdir -p storage/detections

.env yang perlu disesuaikan untuk VM:

┌─────────────────┬─────────────────────────────────────────────────────────────────────────────┐
│       Key       │                                 Nilai di VM                                 │
├─────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ DATABASE_URL    │ mysql+pymysql://fod:password-kuat@127.0.0.1:3306/fod_sentry?charset=utf8mb4 │
├─────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ JWT_SECRET      │ hasil generate di atas — jangan biarkan default                             │
├─────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ ALLOWED_ORIGINS │ https://domain-anda (bukan localhost lagi, dan tidak boleh * karena auth    │
│                 │ pakai cookie)                                                               │
├─────────────────┼─────────────────────────────────────────────────────────────────────────────┤
│ COOKIE_SECURE   │ true begitu jalan di HTTPS                                                  │
└─────────────────┴─────────────────────────────────────────────────────────────────────────────┘

Ganti juga password admin123 / petugas123 setelah login pertama.

5. HTTPS itu wajib, bukan opsional

Ini kendala terbesar deploy ke VM: live.js:124 dan live.js:469 memanggil navigator.mediaDevices.getUserMedia(). Browser memblokir kamera di http:// untuk host apa pun selain localhost. Jadi kalau VM diakses via http://IP-VM:8000, halaman Live tidak akan bisa membuka kamera — dashboard/detections/inspections tetap normal.

Solusinya: reverse proxy + TLS. Frontend sudah otomatis pindah ke wss:// mengikuti protokol halaman (live.js:143-144), tapi nginx perlu header upgrade untuk /ws:

server {
    listen 443 ssl;
    server_name domain-anda;
    ssl_certificate     /etc/letsencrypt/live/domain-anda/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/domain-anda/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;   # stream panjang, jangan sampai diputus
    }
}

Tanpa domain? Bisa self-signed cert langsung di uvicorn (--ssl-keyfile/--ssl-certfile) — browser akan protes, tapi kamera jalan setelah warning di-accept.

6. Systemd
Model jalan di 960×960, dan best.onnx di-load ke memori:

- CPU: 4 vCPU minimum. Inferensi CPU-only ~100–400 ms/frame, artinya 2–8 FPS. Bisa dipakai, tapi terasa lambat.
- RAM: 4 GB (onnxruntime + OpenCV + model + MySQL).
- Disk: 20 GB+ — storage/detections/ menumpuk JPEG bukti terus-menerus, tidak ada auto-purge. Ini yang paling cepat menghabiskan disk; siapkan cron pembersih kalau jalan lama.
- GPU / kamera fisik: tidak perlu. Webcam yang dipakai adalah webcam browser klien, dikirim sebagai JPEG lewat WebSocket. VM tidak butuh kamera sama sekali. Kalau VM punya GPU NVIDIA, pip uninstall onnxruntime && pip install onnxruntime-gpu menurunkan latensi ke <30 ms tanpa ubah kode.

8. Jaringan

- Inbound: 443 (dan 80 untuk redirect/ACME). Port 8000 tidak perlu diekspos kalau di belakang nginx.
- Outbound: HTTPS ke api.open-meteo.com untuk widget cuaca — kalau VM tanpa internet, widget cuaca kosong (sisanya jalan).
- Chart.js diambil dari CDN oleh browser klien, bukan VM. Kalau klien di jaringan tertutup, chart tidak muncul; solusinya download chart.umd.min.js ke frontend/assets/vendor/.