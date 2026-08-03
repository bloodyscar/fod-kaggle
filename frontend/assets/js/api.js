/* ==========================================================================
   FOD Sentry — API wrapper + shared formatting helpers.
   Loaded first on every page. Exposes a single global: window.API.
   ========================================================================== */

(() => {
  "use strict";

  const LOGIN_PAGE = "login.html";

  /** Thrown for any non-2xx response so callers can show `err.message`. */
  class ApiError extends Error {
    constructor(status, message, payload) {
      super(message);
      this.status = status;
      this.payload = payload;
    }
  }

  function onPage(name) {
    return location.pathname.endsWith(name);
  }

  /** A 401 anywhere means the cookie is gone or expired — bounce to login. */
  function redirectToLogin() {
    if (onPage(LOGIN_PAGE)) return;
    const back = encodeURIComponent(
      location.pathname.split("/").pop() + location.search
    );
    location.replace(`${LOGIN_PAGE}?next=${back}`);
  }

  async function request(path, { method = "GET", body, silent401 = false } = {}) {
    const opts = {
      method,
      credentials: "same-origin",   // send the httpOnly JWT cookie
      headers: {},
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }

    let res;
    try {
      res = await fetch(`/api${path}`, opts);
    } catch {
      throw new ApiError(0, "Tidak bisa menghubungi server.");
    }

    if (res.status === 401) {
      if (!silent401) redirectToLogin();
      throw new ApiError(401, "Sesi berakhir, silakan login ulang.");
    }

    if (res.status === 204) return null;

    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const payload = isJson ? await res.json().catch(() => null) : null;

    if (!res.ok) {
      throw new ApiError(res.status, detailToText(payload) || `Gagal (${res.status})`, payload);
    }
    return payload;
  }

  /** FastAPI returns `detail` as a string, or a list of validation errors. */
  function detailToText(payload) {
    const d = payload && payload.detail;
    if (!d) return null;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d.map((e) => e.msg || JSON.stringify(e)).join("; ");
    }
    return JSON.stringify(d);
  }

  function qs(params) {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== "" && v !== null && v !== undefined) sp.set(k, v);
    }
    const s = sp.toString();
    return s ? `?${s}` : "";
  }

  // ------------------------------------------------------------ formatting --

  const RISK_COLORS = {
    Critical: "#ff5457",
    High: "#ff8a3d",
    Medium: "#ffb627",
    Low: "#35e0c7",
    "Very Low": "#8b93a3",
  };

  /** "Very Low" -> "verylow", so it maps onto the .badge-* CSS classes. */
  function slug(text) {
    return String(text || "").toLowerCase().replace(/\s+/g, "");
  }

  function riskBadge(level) {
    if (!level) return `<span class="badge badge-verylow">—</span>`;
    return `<span class="badge badge-${slug(level)}">${escapeHtml(level)}</span>`;
  }

  function statusBadge(status) {
    const label = { open: "Open", proses: "Proses", selesai: "Selesai" }[status] || status;
    return `<span class="badge badge-${slug(status)}">${escapeHtml(label)}</span>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  /** Backend sends naive local datetimes — parse them as local, not UTC. */
  function parseDate(value) {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function fmtDateTime(value) {
    const d = parseDate(value);
    if (!d) return "—";
    return d.toLocaleString("id-ID", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  }

  function fmtTime(value) {
    const d = parseDate(value);
    return d ? d.toLocaleTimeString("id-ID", { hour12: false }) : "—";
  }

  function fmtRelative(value) {
    const d = parseDate(value);
    if (!d) return "—";
    const secs = Math.round((Date.now() - d.getTime()) / 1000);
    if (secs < 60) return "baru saja";
    if (secs < 3600) return `${Math.floor(secs / 60)} mnt lalu`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} jam lalu`;
    return `${Math.floor(secs / 86400)} hari lalu`;
  }

  /** Seconds -> "12 mnt", "1j 05m". Used for the response-time KPI. */
  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s} dtk`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} mnt`;
    const h = Math.floor(m / 60);
    return `${h}j ${String(m % 60).padStart(2, "0")}m`;
  }

  /** Degrees -> 8-point compass, for the wind readout. */
  function windCompass(deg) {
    if (deg === null || deg === undefined) return "";
    const points = ["U", "TL", "T", "TG", "S", "BD", "B", "BL"];
    return points[Math.round(deg / 45) % 8];
  }

  let toastTimer = null;
  function toast(message, isError = false) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.toggle("err", !!isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
  }

  window.API = {
    ApiError,
    get: (path) => request(path),
    post: (path, body) => request(path, { method: "POST", body }),
    patch: (path, body) => request(path, { method: "PATCH", body }),
    del: (path) => request(path, { method: "DELETE" }),
    request,
    qs,
    redirectToLogin,
    RISK_COLORS,
    slug,
    riskBadge,
    statusBadge,
    escapeHtml,
    fmtDateTime,
    fmtTime,
    fmtRelative,
    fmtDuration,
    windCompass,
    toast,
  };
})();
