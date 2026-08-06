/* ==========================================================================
   Dataset gallery — FOD-A (Pascal VOC), 33.793 frames on disk.

   The page never asks for the whole dataset: the backend keeps 20 sample
   frames per FOD class (labelled by best.onnx, not by the dataset's own XML)
   and this file pages through that sample with limit/offset. Indexing runs in
   the background on the server, so while `status.status === "indexing"` we
   poll and let the grid fill in.
   ========================================================================== */

(() => {
  "use strict";

  let currentUser = null;
  let offset = 0;
  let classes = [];           // [{class_id, class_name, count}]
  let pollTimer = null;
  // Kept here rather than read off the <select>: the options only exist after
  // the first response, so a ?class= deep link would otherwise be dropped.
  let selectedClass = "";

  const POLL_MS = 4000;       // only while the indexer is still running
  const el = (id) => document.getElementById(id);
  const fmtNum = (n) => Number(n || 0).toLocaleString("id-ID");

  const STATUS_TEXT = {
    idle: "Belum diindeks",
    indexing: "Mengindeks…",
    ready: "Siap",
    error: "Gagal",
    missing: "Dataset tidak ditemukan",
  };

  function query() {
    return {
      class_name: selectedClass,
      limit: el("perPage").value,
      offset,
    };
  }

  // ------------------------------------------------------- status / header ----
  function renderStatus(s) {
    const target = s.per_class * (classes.length || 31);
    const pct = target ? Math.min(100, Math.round((s.sampled / target) * 100)) : 0;

    el("sampledBadge").textContent = fmtNum(s.sampled);
    el("indexBarFill").style.width = `${pct}%`;
    el("indexBarFill").classList.toggle("done", s.status === "ready");

    if (s.status === "missing" || s.status === "error") {
      el("indexNote").innerHTML =
        `<span class="index-warn">${API.escapeHtml(STATUS_TEXT[s.status])}</span> — ` +
        API.escapeHtml(s.error || "Tidak ada detail.");
      return;
    }

    // This is the "sisanya" note the dataset page exists to make honest: we
    // show a sample, and we say plainly how many frames we are not showing.
    const parts = [
      `Dataset FOD-A berisi <strong>${fmtNum(s.total_images)}</strong> gambar.`,
      `Ditampilkan <strong>${fmtNum(s.sampled)}</strong> contoh ` +
        `(maksimal ${s.per_class} per kelas), dilabeli otomatis oleh model <code>best.onnx</code>.`,
      `Sisanya <strong>${fmtNum(Math.max(0, s.total_images - s.sampled))}</strong> gambar tidak ditampilkan.`,
    ];
    if (s.status === "indexing") {
      parts.push(
        `<span class="index-live">● ${API.escapeHtml(STATUS_TEXT.indexing)}</span> ` +
        `${fmtNum(s.scanned)} frame sudah dipindai (batas ${fmtNum(s.scan_limit)}) — ` +
        "halaman ini memperbarui sendiri."
      );
    } else if (s.status === "ready") {
      parts.push(
        `Indeks selesai: ${fmtNum(s.scanned)} frame dipindai pada ambang keyakinan ` +
        `${Math.round(s.min_conf * 100)}%.`
      );
    } else if (s.status === "idle") {
      parts.push("Indeks akan mulai otomatis saat halaman dibuka.");
    }
    el("indexNote").innerHTML = parts.join(" ");
  }

  function renderChips() {
    const active = selectedClass;
    const withSamples = classes.filter((c) => c.count > 0);
    const empty = classes.length - withSamples.length;

    const chips = withSamples.map((c) => `
      <button class="class-chip${c.class_name === active ? " active" : ""}"
              data-class="${API.escapeHtml(c.class_name)}">
        ${API.escapeHtml(c.class_name)}<span class="class-chip-n">${c.count}</span>
      </button>`).join("");

    el("classChips").innerHTML = chips
      ? chips + (empty
          ? `<span class="class-chip-note">${empty} kelas belum punya contoh</span>`
          : "")
      : `<span class="class-chip-note">Belum ada kelas terdeteksi — tunggu indeks berjalan.</span>`;

    el("classChips").querySelectorAll(".class-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const name = btn.dataset.class;
        selectedClass = selectedClass === name ? "" : name;   // click again to clear
        offset = 0;
        load();
      });
    });
  }

  function fillClassSelect() {
    el("fClass").innerHTML =
      `<option value="">Semua kelas</option>` +
      classes.map((c) => `
        <option value="${API.escapeHtml(c.class_name)}"${c.count ? "" : " disabled"}>
          ${API.escapeHtml(c.class_name)} (${c.count})
        </option>`).join("");
    el("fClass").value = selectedClass;
  }

  // ------------------------------------------------------------- gallery ----
  function tile(item) {
    const conf = Math.round(item.conf * 100);
    return `
      <figure class="gallery-item" data-file="${API.escapeHtml(item.file)}"
              data-class="${API.escapeHtml(item.class_name)}"
              data-conf="${conf}" data-box="${API.escapeHtml((item.box || []).join(","))}"
              data-size="${item.width}×${item.height}" tabindex="0">
        <img class="gallery-thumb" loading="lazy" decoding="async"
             src="/api/dataset/image/${encodeURIComponent(item.file)}"
             alt="Frame ${API.escapeHtml(item.file)} — ${API.escapeHtml(item.class_name)}"
             onerror="this.outerHTML='<div class=&quot;gallery-thumb-missing&quot;>Gambar tidak terbaca</div>'" />
        <figcaption class="gallery-cap">
          <span class="gallery-class">${API.escapeHtml(item.class_name)}</span>
          <span class="gallery-conf">${conf}%</span>
        </figcaption>
        <span class="gallery-file">${API.escapeHtml(item.file)}</span>
      </figure>`;
  }

  async function load() {
    const grid = el("galleryGrid");
    let data;
    try {
      data = await API.get(`/dataset${API.qs(query())}`);
    } catch (err) {
      if (err.status === 401) return;
      // A ?class= deep link naming something best.onnx doesn't know: drop the
      // filter rather than leaving the page stuck on an error.
      if (err.status === 404 && selectedClass) {
        selectedClass = "";
        return load();
      }
      grid.innerHTML = `<div class="row-empty">${API.escapeHtml(err.message)}</div>`;
      return;
    }

    classes = data.classes;
    fillClassSelect();
    renderChips();
    renderStatus(data.status);

    const limit = data.limit;
    const shown = data.items.length;
    const from = data.total ? data.offset + 1 : 0;
    const pages = Math.max(1, Math.ceil(data.total / limit));
    const pageNo = Math.floor(data.offset / limit) + 1;

    el("totalBadge").textContent = fmtNum(data.total);
    el("galleryTitle").textContent = selectedClass
      ? `Contoh gambar — ${selectedClass}`
      : "Contoh gambar — semua kelas";
    el("pageInfo").textContent = data.total
      ? `${fmtNum(from)}–${fmtNum(data.offset + shown)} dari ${fmtNum(data.total)} · hal. ${pageNo}/${pages}`
      : "—";
    el("prevPage").disabled = data.offset <= 0;
    el("nextPage").disabled = data.offset + shown >= data.total;

    if (!shown) {
      grid.innerHTML = `<div class="row-empty">${
        data.status.status === "indexing"
          ? "Belum ada contoh untuk filter ini — indeks masih berjalan."
          : "Tidak ada contoh untuk filter ini."
      }</div>`;
    } else {
      grid.innerHTML = data.items.map(tile).join("");
      grid.querySelectorAll(".gallery-item").forEach((node) => {
        node.addEventListener("click", () => openFrame(node.dataset));
        node.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFrame(node.dataset); }
        });
      });
    }

    schedulePoll(data.status.status === "indexing");
  }

  // The indexer runs server-side; keep refreshing until it settles.
  function schedulePoll(active) {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (active) pollTimer = setTimeout(load, POLL_MS);
  }

  // -------------------------------------------------------------- preview ----
  function openFrame(d) {
    const box = (d.box || "").split(",").map(Number).filter((n) => !Number.isNaN(n));
    // Normalised x1,y1,x2,y2 from the model — overlay it as a percentage box so
    // it lines up whatever size the browser renders the frame at.
    const overlay = box.length === 4
      ? `<span class="frame-box" style="left:${box[0] * 100}%;top:${box[1] * 100}%;
             width:${(box[2] - box[0]) * 100}%;height:${(box[3] - box[1]) * 100}%"></span>`
      : "";

    el("frameTitle").textContent = `${d.class} — ${d.file}`;
    el("frameBody").innerHTML = `
      <div class="frame-stage">
        <img class="snapshot" alt="Frame ${API.escapeHtml(d.file)}"
             src="/api/dataset/image/${encodeURIComponent(d.file)}" />
        ${overlay}
      </div>
      <div class="detail-grid" style="margin-top:14px">
        <div><span class="hint">Label model</span><div class="detail-val">${API.escapeHtml(d.class)}</div></div>
        <div><span class="hint">Keyakinan</span><div class="detail-val mono">${API.escapeHtml(d.conf)}%</div></div>
        <div><span class="hint">Berkas</span><div class="detail-val mono">${API.escapeHtml(d.file)}</div></div>
        <div><span class="hint">Resolusi</span><div class="detail-val mono">${API.escapeHtml(d.size)}</div></div>
      </div>
      <p class="panel-note" style="margin-top:12px">
        Label dan kotak di atas berasal dari <code>best.onnx</code>, bukan dari anotasi
        VOC bawaan dataset.
      </p>`;
    el("frameModal").classList.add("open");
  }

  function closeFrame() {
    el("frameModal").classList.remove("open");
    el("frameBody").innerHTML = "";
  }

  // ---------------------------------------------------------------- boot ----
  (async () => {
    currentUser = await Layout.mount({
      title: "Dataset",
      subtitle: "Contoh gambar FOD-A dari Kaggle, dilabeli oleh best.onnx",
    });
    if (!currentUser) return;

    if (currentUser.role === "admin") el("reindexBtn").hidden = false;

    el("fClass").addEventListener("change", () => {
      selectedClass = el("fClass").value;
      offset = 0;
      load();
    });
    el("perPage").addEventListener("change", () => { offset = 0; load(); });
    el("resetFilters").addEventListener("click", () => {
      selectedClass = "";
      offset = 0;
      load();
    });

    el("prevPage").addEventListener("click", () => {
      offset = Math.max(0, offset - Number(el("perPage").value));
      load();
    });
    el("nextPage").addEventListener("click", () => {
      offset += Number(el("perPage").value);
      load();
    });
    el("refreshBtn").addEventListener("click", () => load());
    el("reindexBtn").addEventListener("click", async () => {
      if (!confirm("Hapus indeks contoh dan pindai ulang dataset dengan best.onnx?")) return;
      try {
        await API.post("/dataset/reindex");
        API.toast("Indeks ulang dimulai.");
        offset = 0;
        load();
      } catch (err) {
        API.toast(err.message, true);
      }
    });

    el("frameClose").addEventListener("click", closeFrame);
    el("frameCloseFoot").addEventListener("click", closeFrame);
    el("frameModal").addEventListener("click", (e) => {
      if (e.target === el("frameModal")) closeFrame();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeFrame();
    });

    // Deep link from anywhere: ?class=Bolt lands straight on that class.
    selectedClass = new URLSearchParams(location.search).get("class") || "";

    await load();
  })();
})();
