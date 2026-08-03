# 📋 Daftar Fitur & Komponen yang Belum Ada (TODO List)

Berdasarkan perbandingan antara prototipe aplikasi saat ini dan target spesifikasi pada **REVISI PROPOSAL TERBARU.pdf**, berikut adalah daftar fitur, modul, dan komponen yang **belum diimplementasikan** dan harus segera dibuat:

---

## 🛠️ 1. Modul Proses Cerdas & backend (Python/FastAPI)

- [x] **Modul Penilaian Risiko (Risk Assessment Module)**
  - Mengimplementasikan perhitungan Skor Risiko berbasis formula: $Risk = Likelihood \times Severity$.
  - Menerjemahkan bobot *Severity* berdasarkan jenis/kelas objek FOD (misal: baut besi memiliki Severity lebih tinggi daripada plastik).
  - Mengklasifikasikan hasil skor ke dalam tingkatan: *Critical, High, Medium, Low, Very Low*.

- [x] **Intelligent Decision Support Engine**
  - Membuat logika untuk menghasilkan **Rekomendasi Tindakan Inspeksi** secara otomatis berdasarkan tingkat risiko FOD.
  - Alur tindakan:
    - *Low/Very Low*: Pantau pada inspeksi terjadwal berikutnya.
    - *Medium*: Rekomendasi verifikasi/inspeksi visual lapangan secepatnya.
    - *High/Critical*: Rekomendasi pembersihan runway darurat segera sebelum penerbangan berikutnya.

- [x] **Notification Manager (Sistem Alerting)**
  - Integrasi push notification atau alert otomatis ke petugas ketika terdeteksi FOD berbahaya (*High/Critical*).
  - Bisa dikembangkan dengan Web Push API, Telegram Bot, WhatsApp API, atau alert suara/visual di dashboard. (Dibuat ada icon notif aja di dashboard)

- [x] **Pipeline Pengolahan Citra Tambahan (Pre-processing)**
  - Menambahkan filter *Noise Reduction* di backend (mengurangi gangguan hujan/kabut).
  - Menambahkan *Contrast Enhancement / Sharpening* (meningkatkan kualitas gambar saat kondisi silau atau malam hari).

- [ ] **Koneksi IP Camera (CCTV)** — ❌ **DIBATALKAN** atas permintaan; sumber kamera cukup webcam internal/eksternal USB via browser (dropdown pemilih kamera sudah ada di `live.html`). Lihat `PLAN-WEB-ADMIN-DASHBOARD.md` §1.3.
  - ~~Menambahkan fungsionalitas pembacaan feed video RTSP dari IP Camera menggunakan OpenCV, bukan hanya bergantung pada Web Camera lokal browser.~~

---

## 💾 2. Integrasi Basis Data (Database Integration)

- [x] **Konfigurasi Database Relasional**
  - Koneksi ke database **PostgreSQL** atau **MySQL** di backend menggunakan SQLAlchemy atau ORM Python sejenis.
  
- [x] **Skema Tabel Database**
  - Tabel `users` (Menyimpan kredensial login Admin dan Petugas).
  - Tabel `fod_detections` (Menyimpan data koordinat bounding box, nama objek, confidence score, dan path gambar temuan).
  - Tabel `risk_assessments` (Menyimpan skor risiko, level risiko, dan hasil rekomendasi tindakan).
  - Tabel `inspections` (Menyimpan riwayat penanganan FOD oleh petugas, waktu respon, dan status penyelesaian).

- [x] **User & Role Management (Autentikasi)**
  - Sistem login dan pembagian hak akses/peran pengguna:
    - **Petugas Airside / TOKPD** (Melihat dashboard, memproses alert, menandai status penanganan FOD).
    - **Administrator** (Mengelola konfigurasi sistem, data pengguna, dan database).

---

## 💻 3. Pengembangan Antarmuka (Smart Dashboard Frontend)

- [x] **Desain Layout Dashboard Premium (Smart Airport)**
  - Mengubah UI saat ini menjadi antarmuka visual terpadu berstandar premium (glassmorphism/dark mode) sesuai tata letak mockup pada proposal tesis (Lampiran 5).

- [x] **Peta Runway Interaktif (Runway Map)**
  - Menampilkan skema runway 2D dan memetakan titik koordinat ditemukannya FOD secara visual (seperti marker lokasi pada runway).

- [x] **Widget Cuaca Real-Time**
  - Integrasi widget informasi cuaca daerah Nabire (suhu, kelembapan, kecepatan angin) menggunakan API cuaca (misal: OpenWeatherMap).

- [x] **Grafik & Statistik Visual (Chart.js)**
  - Menampilkan grafik tren temuan FOD (Grafik Batang) 7 hari terakhir.
  - Diagram lingkaran (*Pie Chart*) distribusi level risiko FOD yang terdeteksi.

- [x] **Tabel Riwayat Hasil Inspeksi Terakhir**
  - Menampilkan data historis penanganan FOD dengan status penyelesaian (*Selesai / Sedang Diproses*) yang tersinkronisasi langsung dengan database.
