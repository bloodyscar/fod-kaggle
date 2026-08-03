/* ==========================================================================
   Inspections: the handling worklist. Status open -> proses -> selesai, notes,
   and the response time the backend derives from started_at - detected_at.
   ========================================================================== */

(() => {
  "use strict";

  const el = (id) => document.getElementById(id);
  let rows = [];
  let editing = null;

  async function load() {
    const body = el("tableBody");
    const status = el("fStatus").value;

    try {
      rows = await API.get(`/inspections${API.qs({ status, limit: 200 })}`);
    } catch (err) {
      if (err.status === 401) return;
      body.innerHTML = `<tr><td colspan="8" class="row-empty">${API.escapeHtml(err.message)}</td></tr>`;
      return;
    }

    el("rowCount").textContent = rows.length;
    renderKpis();

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="8" class="row-empty">Tidak ada inspeksi pada filter ini.</td></tr>`;
      return;
    }

    body.innerHTML = rows.map((r) => `
      <tr data-id="${r.id}">
        <td class="mono">${API.fmtDateTime(r.detected_at)}</td>
        <td>${API.escapeHtml(r.class_name)}</td>
        <td>${API.riskBadge(r.risk_level)}</td>
        <td>${API.statusBadge(r.status)}</td>
        <td>${API.escapeHtml(r.handler_name || "—")}</td>
        <td class="num">${API.fmtDuration(r.response_time_seconds)}</td>
        <td>${API.escapeHtml(r.notes || "—")}</td>
        <td><button class="icon-btn" data-edit="${r.id}">Ubah</button></td>
      </tr>`).join("");

    body.querySelectorAll("[data-edit]").forEach((btn) => {
      btn.addEventListener("click", () => openModal(Number(btn.dataset.edit)));
    });
  }

  /** KPI counts come from the loaded rows, so they always match the table. */
  function renderKpis() {
    const count = (status) => rows.filter((r) => r.status === status).length;
    el("kOpen").textContent = count("open");
    el("kProses").textContent = count("proses");
    el("kSelesai").textContent = count("selesai");
    el("kCritical").textContent =
      rows.filter((r) => r.risk_level === "Critical" && r.status === "open").length;
  }

  function openModal(id) {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    editing = row;

    el("handleTitle").textContent = `${row.class_name} — inspeksi #${row.id}`;
    el("handleContext").style.setProperty(
      "--accent", API.RISK_COLORS[row.risk_level] || "var(--amber)"
    );
    el("handleContext").innerHTML = `
      <strong>${API.escapeHtml(row.class_name)}</strong> ·
      ${API.riskBadge(row.risk_level)}
      ${row.risk_score ? `<span class="mono"> ${row.risk_score}/25</span>` : ""}<br>
      Terdeteksi ${API.fmtDateTime(row.detected_at)}`;

    el("hStatus").value = row.status;
    el("hNotes").value = row.notes || "";
    el("handleMeta").textContent = [
      row.started_at ? `Mulai ditangani ${API.fmtDateTime(row.started_at)}` : null,
      row.completed_at ? `Selesai ${API.fmtDateTime(row.completed_at)}` : null,
      row.response_time_seconds !== null
        ? `Waktu respon ${API.fmtDuration(row.response_time_seconds)}`
        : null,
    ].filter(Boolean).join(" · ") || "Belum ada penanganan tercatat.";

    el("handleModal").classList.add("open");
  }

  function closeModal() {
    el("handleModal").classList.remove("open");
    editing = null;
  }

  async function save() {
    if (!editing) return;
    const btn = el("handleSave");
    btn.disabled = true;
    try {
      await API.patch(`/inspections/${editing.id}`, {
        status: el("hStatus").value,
        notes: el("hNotes").value,
      });
      API.toast("Penanganan diperbarui.");
      closeModal();
      await load();
      // The bell is derived from open High/Critical rows — refresh it now.
      Layout.pollNotifications();
    } catch (err) {
      API.toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  (async () => {
    const user = await Layout.mount({
      title: "Inspeksi",
      subtitle: "Tandai penanganan FOD dan catat hasilnya",
    });
    if (!user) return;

    el("fStatus").addEventListener("change", load);
    el("reloadBtn").addEventListener("click", load);
    el("handleClose").addEventListener("click", closeModal);
    el("handleCancel").addEventListener("click", closeModal);
    el("handleSave").addEventListener("click", save);
    el("handleModal").addEventListener("click", (e) => {
      if (e.target === el("handleModal")) closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });

    await load();

    // Deep link from a notification: ?detection=123 opens that inspection.
    const wanted = Number(new URLSearchParams(location.search).get("detection"));
    if (wanted) {
      const match = rows.find((r) => r.detection_id === wanted);
      if (match) openModal(match.id);
    }
  })();
})();
