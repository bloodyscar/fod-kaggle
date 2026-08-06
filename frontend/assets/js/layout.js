/* ==========================================================================
   FOD Sentry — shared shell.
   Injects the sidebar, topbar, notification bell and critical banner into
   every page, so the HTML files only carry their own content. Also owns the
   auth gate: no page renders until /api/auth/me succeeds.
   ========================================================================== */

(() => {
  "use strict";

  const NAV = [
    { href: "dashboard.html",   icon: "▤", label: "Dashboard" },
    { href: "live.html",        icon: "◉", label: "Deteksi Live" },
    { href: "detections.html",  icon: "☰", label: "Riwayat FOD" },
    { href: "inspections.html", icon: "✓", label: "Inspeksi" },
    { href: "dataset.html",     icon: "▦", label: "Dataset" },
    { href: "users.html",       icon: "⚙", label: "Pengguna", adminOnly: true },
  ];

  const POLL_MS = 15000;      // plan §4.5 — polling beats SSE at this scale
  let lastCriticalIds = new Set();
  let firstPoll = true;

  const BRAND = `
    <div class="brand">
      <svg class="brand-mark" width="26" height="26" viewBox="0 0 26 26" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M13 1 L24 7 V19 L13 25 L2 19 V7 Z" stroke="currentColor" stroke-width="1.6"/>
        <path d="M13 8 L18 11 V17 L13 20 L8 17 V11 Z" fill="currentColor" opacity="0.85"/>
      </svg>
      <div class="brand-text">
        <span class="brand-name">FOD&nbsp;SENTRY</span>
        <span class="brand-sub">Runway Debris Detection</span>
      </div>
    </div>`;

  function currentPage() {
    return location.pathname.split("/").pop() || "dashboard.html";
  }

  function buildSidebar(user) {
    const here = currentPage();
    const links = NAV
      .filter((item) => !item.adminOnly || user.role === "admin")
      .map((item) => `
        <a class="nav-link${item.href === here ? " active" : ""}" href="${item.href}">
          <span class="nav-icon">${item.icon}</span>${item.label}
        </a>`)
      .join("");

    return `
      <aside class="sidebar">
        ${BRAND}
        <nav class="nav">
          <div class="nav-section">Menu</div>
          ${links}
        </nav>
        <div class="sidebar-foot">
          <div class="who">
            <span class="who-name">${API.escapeHtml(user.full_name || user.username)}</span>
            <span class="who-role">${API.escapeHtml(user.role)}</span>
          </div>
          <button id="logoutBtn" class="secondary-btn" style="width:100%">Keluar</button>
        </div>
      </aside>`;
  }

  function buildTopbar(title, subtitle, extraHtml) {
    return `
      <header class="topbar">
        <div class="page-title">
          <h1>${API.escapeHtml(title)}</h1>
          <p>${API.escapeHtml(subtitle || "")}</p>
        </div>
        <div class="topbar-actions">
          ${extraHtml || ""}
          <div class="bell-wrap">
            <button class="bell-btn" id="bellBtn" title="Notifikasi" aria-label="Notifikasi">
              🔔<span class="bell-badge" id="bellBadge">0</span>
            </button>
            <div class="bell-panel" id="bellPanel">
              <div class="bell-panel-head">Alert High / Critical belum ditangani</div>
              <div id="bellList">
                <div class="notif-item"><span class="notif-text">Memuat…</span></div>
              </div>
            </div>
          </div>
        </div>
      </header>
      <div class="alert-banner" id="alertBanner">
        <strong>CRITICAL</strong>
        <span id="alertBannerText"></span>
        <button class="ghost-btn" id="alertBannerClose">Tutup</button>
      </div>`;
  }

  /* ---- beep for a newly arrived Critical alert (no audio file needed) ---- */
  let audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      // Autoplay policy: stays suspended until the user has interacted once.
      if (audioCtx.state === "suspended") audioCtx.resume();
      const now = audioCtx.currentTime;
      [0, 0.28].forEach((offset) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = "square";
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.09, now + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.2);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(now + offset);
        osc.stop(now + offset + 0.22);
      });
    } catch {
      /* audio blocked — the badge and banner still tell the story */
    }
  }

  function renderNotifications(data) {
    const list = document.getElementById("bellList");
    const badge = document.getElementById("bellBadge");
    const bell = document.getElementById("bellBtn");
    if (!list) return;

    const count = data.unread_count || 0;
    badge.textContent = count > 99 ? "99+" : count;
    badge.classList.toggle("show", count > 0);
    bell.classList.toggle("alert", count > 0);

    if (!data.items.length) {
      list.innerHTML = `<div class="notif-item"><span class="notif-text">Tidak ada alert yang menunggu. 👍</span></div>`;
    } else {
      list.innerHTML = data.items.map((n) => `
        <a class="notif-item" href="inspections.html?detection=${n.detection_id}">
          <div class="notif-top">
            ${API.riskBadge(n.risk_level)}
            <span class="notif-class">${API.escapeHtml(n.class_name)}</span>
            <span class="notif-time">${API.fmtRelative(n.detected_at)}</span>
          </div>
          <div class="notif-text">${API.escapeHtml(n.recommendation)}</div>
        </a>`).join("");
    }

    // Beep + banner only for Critical alerts we haven't seen before, and never
    // on the very first poll (otherwise every page load screams).
    const criticals = data.items.filter((n) => n.risk_level === "Critical");
    const ids = new Set(criticals.map((n) => n.detection_id));
    const fresh = criticals.filter((n) => !lastCriticalIds.has(n.detection_id));

    if (!firstPoll && fresh.length) {
      beep();
      const banner = document.getElementById("alertBanner");
      document.getElementById("alertBannerText").textContent =
        `${fresh[0].class_name} — ${fresh[0].recommendation}`;
      banner.classList.add("show");
    }
    lastCriticalIds = ids;
    firstPoll = false;
  }

  async function pollNotifications() {
    try {
      renderNotifications(await API.get("/notifications"));
    } catch (err) {
      if (err.status !== 401) {
        const list = document.getElementById("bellList");
        if (list) {
          list.innerHTML = `<div class="notif-item"><span class="notif-text">Gagal memuat notifikasi.</span></div>`;
        }
      }
    }
  }

  function wireShell() {
    document.getElementById("logoutBtn")?.addEventListener("click", async () => {
      try { await API.post("/auth/logout"); } catch { /* clear anyway */ }
      location.replace("login.html");
    });

    const bell = document.getElementById("bellBtn");
    const panel = document.getElementById("bellPanel");
    bell?.addEventListener("click", (e) => {
      e.stopPropagation();
      panel.classList.toggle("open");
    });
    document.addEventListener("click", (e) => {
      if (panel?.classList.contains("open") && !panel.contains(e.target)) {
        panel.classList.remove("open");
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") panel?.classList.remove("open");
    });

    document.getElementById("alertBannerClose")?.addEventListener("click", () => {
      document.getElementById("alertBanner").classList.remove("show");
    });
  }

  /**
   * Mount the shell. Returns the signed-in user, or null if we redirected.
   *
   *   const user = await Layout.mount({ title, subtitle, topbarExtra });
   *   if (!user) return;
   */
  async function mount({ title, subtitle = "", topbarExtra = "", notifications = true } = {}) {
    let user;
    try {
      user = await API.get("/auth/me");
    } catch (err) {
      if (err.status === 401) return null;      // api.js already redirected
      document.body.innerHTML =
        `<div class="login-wrap"><div class="login-card">
           <h1>Server tidak siap</h1>
           <p class="hint">${API.escapeHtml(err.message)}</p>
         </div></div>`;
      return null;
    }

    const page = document.getElementById("pageContent");
    const shell = document.createElement("div");
    shell.className = "shell";
    shell.innerHTML = `
      ${buildSidebar(user)}
      <div class="content">
        ${buildTopbar(title, subtitle, topbarExtra)}
        <main class="page" id="pageMain"></main>
      </div>`;

    document.body.prepend(shell);
    if (page) {
      document.getElementById("pageMain").appendChild(page);
      page.hidden = false;
    }

    wireShell();
    if (notifications) {
      pollNotifications();
      setInterval(pollNotifications, POLL_MS);
    }
    return user;
  }

  window.Layout = { mount, pollNotifications };
})();
