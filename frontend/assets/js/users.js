/* ==========================================================================
   Admin page: user CRUD + the 31-class severity weight editor.
   The server enforces admin-only on every call here; hiding the menu item is
   only a convenience.
   ========================================================================== */

(() => {
  "use strict";

  const el = (id) => document.getElementById(id);
  let me = null;
  let editingId = null;      // null = create mode

  // --------------------------------------------------------------- users ----
  async function loadUsers() {
    const body = el("userBody");
    let users;
    try {
      users = await API.get("/users");
    } catch (err) {
      if (err.status === 401) return;
      body.innerHTML = `<tr><td colspan="6" class="row-empty">${API.escapeHtml(err.message)}</td></tr>`;
      return;
    }

    body.innerHTML = users.map((u) => {
      const self = u.id === me.id;
      return `
        <tr>
          <td class="mono">${API.escapeHtml(u.username)}${self ? " <span class=\"badge badge-low\">Anda</span>" : ""}</td>
          <td>${API.escapeHtml(u.full_name || "—")}</td>
          <td><span class="badge ${u.role === "admin" ? "badge-high" : "badge-proses"}">${API.escapeHtml(u.role)}</span></td>
          <td>${u.is_active
              ? '<span class="badge badge-selesai">Aktif</span>'
              : '<span class="badge badge-verylow">Nonaktif</span>'}</td>
          <td class="mono">${API.fmtDateTime(u.created_at)}</td>
          <td style="white-space:nowrap">
            <button class="icon-btn" data-edit="${u.id}">Ubah</button>
            <button class="icon-btn danger" data-del="${u.id}" ${self ? "disabled title=\"Tidak bisa menghapus akun sendiri\"" : ""}>Hapus</button>
          </td>
        </tr>`;
    }).join("");

    body.querySelectorAll("[data-edit]").forEach((btn) => {
      const u = users.find((x) => x.id === Number(btn.dataset.edit));
      btn.addEventListener("click", () => openModal(u));
    });
    body.querySelectorAll("[data-del]").forEach((btn) => {
      const u = users.find((x) => x.id === Number(btn.dataset.del));
      btn.addEventListener("click", () => removeUser(u));
    });
  }

  function openModal(user) {
    editingId = user ? user.id : null;
    el("userModalTitle").textContent = user ? `Ubah ${user.username}` : "Tambah pengguna";
    el("uUsername").value = user ? user.username : "";
    el("uUsername").disabled = !!user;          // username is the login key
    el("uFullName").value = user ? user.full_name || "" : "";
    el("uPassword").value = "";
    el("uPasswordHint").textContent = user
      ? "kosongkan bila tidak diganti"
      : "minimal 6 karakter";
    el("uRole").value = user ? user.role : "petugas";
    el("uActive").checked = user ? user.is_active : true;
    el("userModal").classList.add("open");
  }

  function closeModal() {
    el("userModal").classList.remove("open");
    editingId = null;
  }

  async function saveUser() {
    const btn = el("userSave");
    const password = el("uPassword").value;
    const payload = {
      full_name: el("uFullName").value.trim(),
      role: el("uRole").value,
      is_active: el("uActive").checked,
    };

    btn.disabled = true;
    try {
      if (editingId === null) {
        if (password.length < 6) throw new API.ApiError(400, "Password minimal 6 karakter.");
        await API.post("/users", {
          username: el("uUsername").value.trim(),
          password,
          ...payload,
        });
        API.toast("Pengguna dibuat.");
      } else {
        if (password) {
          if (password.length < 6) throw new API.ApiError(400, "Password minimal 6 karakter.");
          payload.password = password;
        }
        await API.patch(`/users/${editingId}`, payload);
        API.toast("Pengguna diperbarui.");
      }
      closeModal();
      await loadUsers();
    } catch (err) {
      API.toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function removeUser(user) {
    if (!confirm(`Hapus pengguna "${user.username}"? Tindakan ini permanen.`)) return;
    try {
      await API.del(`/users/${user.id}`);
      API.toast("Pengguna dihapus.");
      await loadUsers();
    } catch (err) {
      API.toast(err.message, true);
    }
  }

  // ---------------------------------------------------- severity weights ----
  async function loadSeverity() {
    const grid = el("sevGrid");
    let classes;
    try {
      classes = await API.get("/fod-classes");
    } catch (err) {
      if (err.status === 401) return;
      grid.innerHTML = `<p class="hint">${API.escapeHtml(err.message)}</p>`;
      return;
    }

    grid.innerHTML = classes.map((c) => `
      <div class="sev-row">
        <span class="sev-id">${c.id}</span>
        <span class="sev-name" title="${API.escapeHtml(c.name)}">${API.escapeHtml(c.name)}</span>
        <select class="select slim" data-class="${c.id}" aria-label="Severity ${API.escapeHtml(c.name)}">
          ${[1, 2, 3, 4, 5].map((v) =>
            `<option value="${v}"${v === c.severity_weight ? " selected" : ""}>${v}</option>`
          ).join("")}
        </select>
      </div>`).join("");

    grid.querySelectorAll("select[data-class]").forEach((sel) => {
      let previous = sel.value;
      sel.addEventListener("change", async () => {
        try {
          await API.patch(`/fod-classes/${sel.dataset.class}`, {
            severity_weight: Number(sel.value),
          });
          previous = sel.value;
          API.toast("Bobot severity disimpan.");
        } catch (err) {
          sel.value = previous;              // roll the UI back on failure
          API.toast(err.message, true);
        }
      });
    });
  }

  // ---------------------------------------------------------------- boot ----
  (async () => {
    me = await Layout.mount({
      title: "Pengguna & Konfigurasi",
      subtitle: "Kelola akun petugas dan bobot severity kelas FOD",
    });
    if (!me) return;

    if (me.role !== "admin") {
      // Reachable only by typing the URL — the server would refuse anyway.
      el("pageContent").innerHTML = `
        <section class="panel"><div class="panel-body">
          <h2 style="margin-top:0">Akses ditolak</h2>
          <p class="hint">Halaman ini hanya untuk administrator.
          Anda masuk sebagai <strong>${API.escapeHtml(me.role)}</strong>.</p>
          <div class="btn-row"><a class="primary-btn" href="dashboard.html"
             style="text-decoration:none;text-align:center">Kembali ke dashboard</a></div>
        </div></section>`;
      return;
    }

    el("addUserBtn").addEventListener("click", () => openModal(null));
    el("userClose").addEventListener("click", closeModal);
    el("userCancel").addEventListener("click", closeModal);
    el("userSave").addEventListener("click", saveUser);
    el("userModal").addEventListener("click", (e) => {
      if (e.target === el("userModal")) closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });

    await Promise.all([loadUsers(), loadSeverity()]);
  })();
})();
