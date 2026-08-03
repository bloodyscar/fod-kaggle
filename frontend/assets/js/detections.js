/* ==========================================================================
   Detection history: filters, pagination, detail modal, admin delete.
   ========================================================================== */

(() => {
  "use strict";

  let currentUser = null;
  let page = 1;
  let openId = null;          // detection currently shown in the modal

  const el = (id) => document.getElementById(id);

  function filters() {
    return {
      date_from: el("fFrom").value,
      date_to: el("fTo").value,
      class_id: el("fClass").value,
      risk_level: el("fLevel").value,
      status: el("fStatus").value,
      page,
      per_page: el("perPage").value,
    };
  }

  // ------------------------------------------------------------- listing ----
  async function load() {
    const body = el("tableBody");
    let data;
    try {
      data = await API.get(`/detections${API.qs(filters())}`);
    } catch (err) {
      if (err.status === 401) return;
      body.innerHTML = `<tr><td colspan="8" class="row-empty">${API.escapeHtml(err.message)}</td></tr>`;
      return;
    }

    el("totalBadge").textContent = data.total;
    el("pageInfo").textContent = `Halaman ${data.page} / ${data.pages}`;
    el("prevPage").disabled = data.page <= 1;
    el("nextPage").disabled = data.page >= data.pages;

    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="8" class="row-empty">Tidak ada deteksi yang cocok dengan filter.</td></tr>`;
      return;
    }

    body.innerHTML = data.items.map((d) => `
      <tr class="clickable" data-id="${d.id}">
        <td class="mono">${API.fmtDateTime(d.detected_at)}</td>
        <td>${API.escapeHtml(d.class_name)}</td>
        <td class="num">${(d.confidence * 100).toFixed(0)}%</td>
        <td class="num">${d.risk ? `${d.risk.risk_score}/25` : "—"}</td>
        <td>${API.riskBadge(d.risk && d.risk.risk_level)}</td>
        <td>${d.inspection ? API.statusBadge(d.inspection.status) : "—"}</td>
        <td class="mono">${API.escapeHtml(d.camera_label || "—")}</td>
        <td><button class="icon-btn" data-detail="${d.id}">Detail</button></td>
      </tr>`).join("");

    body.querySelectorAll("[data-detail]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        showDetail(Number(btn.dataset.detail));
      });
    });
    body.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => showDetail(Number(tr.dataset.id)));
    });
  }

  // -------------------------------------------------------------- detail ----
  async function showDetail(id) {
    openId = id;
    const modal = el("detailModal");
    const body = el("detailBody");
    body.innerHTML = `<p class="hint">Memuat…</p>`;
    modal.classList.add("open");

    let d;
    try {
      d = await API.get(`/detections/${id}`);
    } catch (err) {
      if (err.status === 401) return;
      body.innerHTML = `<p class="hint">${API.escapeHtml(err.message)}</p>`;
      return;
    }

    el("detailTitle").textContent = `${d.class_name} — #${d.id}`;
    el("deleteBtn").hidden = currentUser.role !== "admin";

    const risk = d.risk;
    const insp = d.inspection;
    const accent = risk ? API.RISK_COLORS[risk.risk_level] : "var(--amber)";

    // The snapshot endpoint 404s for demo rows (no real image was captured).
    const snapshot = d.image_path
      ? `<img class="snapshot" alt="Snapshot deteksi ${API.escapeHtml(d.class_name)}"
              src="/api/detections/${d.id}/snapshot"
              onerror="this.outerHTML='<div class=&quot;snapshot-missing&quot;>Snapshot tidak dapat dibuka.</div>'" />`
      : `<div class="snapshot-missing">Tidak ada snapshot untuk deteksi ini.</div>`;

    body.innerHTML = `
      ${snapshot}
      <div class="detail-grid">
        <div class="detail-item">
          <span class="detail-key">Waktu deteksi</span>
          <span class="detail-val mono">${API.fmtDateTime(d.detected_at)}</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Kamera</span>
          <span class="detail-val">${API.escapeHtml(d.camera_label || "—")}</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Confidence</span>
          <span class="detail-val mono">${(d.confidence * 100).toFixed(1)}%</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Kelas (class_id)</span>
          <span class="detail-val mono">${API.escapeHtml(d.class_name)} (${d.class_id})</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Likelihood × Severity</span>
          <span class="detail-val mono">${risk ? `${risk.likelihood} × ${risk.severity} = ${risk.risk_score}/25` : "—"}</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Level risiko</span>
          <span class="detail-val">${API.riskBadge(risk && risk.risk_level)}</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Bounding box (0–1)</span>
          <span class="detail-val mono">
            ${d.x1.toFixed(3)}, ${d.y1.toFixed(3)} → ${d.x2.toFixed(3)}, ${d.y2.toFixed(3)}
          </span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Status inspeksi</span>
          <span class="detail-val">
            ${insp ? API.statusBadge(insp.status) : "—"}
            ${insp && insp.handler_name ? ` · ${API.escapeHtml(insp.handler_name)}` : ""}
          </span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Waktu respon</span>
          <span class="detail-val mono">${API.fmtDuration(insp && insp.response_time_seconds)}</span>
        </div>
        <div class="detail-item">
          <span class="detail-key">Catatan penanganan</span>
          <span class="detail-val">${API.escapeHtml((insp && insp.notes) || "—")}</span>
        </div>
      </div>
      ${risk ? `<div class="recommend-box" style="--accent:${accent}">
        <strong>Rekomendasi:</strong> ${API.escapeHtml(risk.recommendation)}
      </div>` : ""}`;
  }

  function closeDetail() {
    el("detailModal").classList.remove("open");
    openId = null;
  }

  async function deleteDetection() {
    if (!openId) return;
    // Native confirm() is fine here: this is a real destructive action and the
    // page is not driven by automation.
    if (!confirm(`Hapus deteksi #${openId} beserta risk & inspeksinya? Tindakan ini permanen.`)) {
      return;
    }
    try {
      await API.del(`/detections/${openId}`);
      API.toast("Deteksi dihapus.");
      closeDetail();
      await load();
      Layout.pollNotifications();
    } catch (err) {
      API.toast(err.message, true);
    }
  }

  // ------------------------------------------------------------ class list --
  async function fillClasses() {
    try {
      const classes = await API.get("/fod-classes");
      const select = el("fClass");
      for (const c of classes) {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = `${c.name} (S${c.severity_weight})`;
        select.appendChild(opt);
      }
    } catch { /* filter just stays at "Semua" */ }
  }

  // ---------------------------------------------------------------- boot ----
  (async () => {
    currentUser = await Layout.mount({
      title: "Riwayat FOD",
      subtitle: "Semua deteksi yang tersimpan beserta skor risikonya",
    });
    if (!currentUser) return;

    await fillClasses();

    ["fFrom", "fTo", "fClass", "fLevel", "fStatus"].forEach((id) => {
      el(id).addEventListener("change", () => { page = 1; load(); });
    });
    el("perPage").addEventListener("change", () => { page = 1; load(); });
    el("resetFilters").addEventListener("click", () => {
      ["fFrom", "fTo", "fClass", "fLevel", "fStatus"].forEach((id) => { el(id).value = ""; });
      page = 1;
      load();
    });

    el("prevPage").addEventListener("click", () => { if (page > 1) { page -= 1; load(); } });
    el("nextPage").addEventListener("click", () => { page += 1; load(); });

    el("detailClose").addEventListener("click", closeDetail);
    el("detailCloseFoot").addEventListener("click", closeDetail);
    el("detailModal").addEventListener("click", (e) => {
      if (e.target === el("detailModal")) closeDetail();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDetail();
    });
    el("deleteBtn").addEventListener("click", deleteDetection);

    await load();

    // Deep link from the runway map marker: ?id=123 opens that detection.
    const deepLink = new URLSearchParams(location.search).get("id");
    if (deepLink) showDetail(Number(deepLink));
  })();
})();
