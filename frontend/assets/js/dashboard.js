/* ==========================================================================
   Dashboard: KPI cards, Chart.js bar + doughnut, runway map, weather widget,
   and the 10 most recent inspections. Everything refreshes on one interval.
   ========================================================================== */

(() => {
  "use strict";

  const REFRESH_MS = 30000;
  const AXIS = "#5b6675";
  const GRID = "#e8ecf2";

  // Runway surface rect in SVG units — markers are mapped into this box.
  const RUNWAY = { x: 60, y: 70, w: 680, h: 110 };

  let dailyChart = null;
  let levelChart = null;

  // ---------------------------------------------------------------- KPIs ----
  async function loadSummary() {
    const s = await API.get("/dashboard/summary");
    const alerts = (s.by_level.Critical || 0) + (s.by_level.High || 0);

    document.getElementById("kpiToday").textContent = s.total_today;
    document.getElementById("kpiTotal").textContent = `total keseluruhan ${s.total_all}`;
    document.getElementById("kpiAlert").textContent = alerts;
    document.getElementById("kpiAlertSub").textContent =
      `${s.by_level.Critical || 0} critical · ${s.by_level.High || 0} high`;
    document.getElementById("kpiOpen").textContent = s.open_inspections;
    document.getElementById("kpiOpenSub").textContent =
      s.critical_open ? `${s.critical_open} di antaranya critical` : "status open";
    document.getElementById("kpiResponse").textContent =
      API.fmtDuration(s.avg_response_seconds);
  }

  // -------------------------------------------------------------- charts ----
  async function loadCharts() {
    const data = await API.get("/dashboard/charts");

    if (typeof Chart === "undefined") {
      // CDN blocked (offline). Say so instead of leaving two empty boxes.
      document.querySelectorAll(".chart-box").forEach((box) => {
        box.innerHTML = `<p class="hint">Chart.js tidak bisa dimuat (offline?).
          Unduh chart.umd.min.js ke assets/vendor/ lalu ganti src di dashboard.html.</p>`;
      });
      return;
    }

    const barCfg = {
      type: "bar",
      data: {
        labels: data.daily_labels,
        datasets: [{
          label: "Temuan FOD",
          data: data.daily_counts,
          backgroundColor: "rgba(207,132,0,0.55)",
          borderColor: "#cf8400",
          borderWidth: 1,
          borderRadius: 3,
          maxBarThickness: 44,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: AXIS }, grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { color: AXIS, precision: 0 },
            grid: { color: GRID },
          },
        },
      },
    };

    const doughnutCfg = {
      type: "doughnut",
      data: {
        labels: data.level_labels,
        datasets: [{
          data: data.level_counts,
          backgroundColor: data.level_labels.map((l) => API.RISK_COLORS[l] || "#8b93a3"),
          borderColor: "#ffffff",
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: {
            position: "right",
            labels: { color: "#5b6675", boxWidth: 12, padding: 12 },
          },
        },
      },
    };

    // Update in place after the first render so the charts don't flash.
    if (dailyChart) {
      dailyChart.data = barCfg.data;
      dailyChart.update();
    } else {
      dailyChart = new Chart(document.getElementById("dailyChart"), barCfg);
    }

    if (levelChart) {
      levelChart.data = doughnutCfg.data;
      levelChart.update();
    } else {
      levelChart = new Chart(document.getElementById("levelChart"), doughnutCfg);
    }
  }

  // ---------------------------------------------------------- runway map ----
  function renderLegend() {
    document.getElementById("mapLegend").innerHTML = Object.entries(API.RISK_COLORS)
      .map(([level, color]) => `
        <span class="legend-item">
          <span class="legend-swatch" style="background:${color}"></span>${level}
        </span>`)
      .join("");
  }

  async function loadMap() {
    const points = await API.get("/detections/map?hours=24");
    const layer = document.getElementById("markerLayer");
    const tip = document.getElementById("mapTip");

    if (!points.length) {
      layer.innerHTML = "";
      tip.textContent = "Tidak ada temuan dalam 24 jam terakhir.";
      return;
    }

    const ns = "http://www.w3.org/2000/svg";
    layer.innerHTML = "";
    for (const p of points) {
      // Linear mapping only — normalised frame coords onto the runway box.
      const cx = RUNWAY.x + Math.min(Math.max(p.cx, 0), 1) * RUNWAY.w;
      const cy = RUNWAY.y + Math.min(Math.max(p.cy, 0), 1) * RUNWAY.h;
      const color = API.RISK_COLORS[p.risk_level] || "#8b93a3";

      const dot = document.createElementNS(ns, "circle");
      dot.setAttribute("class", "marker");
      dot.setAttribute("cx", cx.toFixed(1));
      dot.setAttribute("cy", cy.toFixed(1));
      dot.setAttribute("r", p.risk_level === "Critical" ? 6.5 : 5);
      dot.setAttribute("fill", color);
      dot.setAttribute("fill-opacity", "0.85");
      dot.setAttribute("stroke", color);
      dot.setAttribute("stroke-opacity", "0.45");
      dot.setAttribute("stroke-width", "5");

      const label =
        `${p.class_name} · ${p.risk_level} ${p.risk_score}/25 · ${API.fmtTime(p.detected_at)}`;
      const title = document.createElementNS(ns, "title");
      title.textContent = label;                 // native SVG tooltip on hover
      dot.appendChild(title);

      dot.addEventListener("click", () => {
        tip.textContent = label;
        location.href = `detections.html?id=${p.id}`;
      });
      dot.addEventListener("mouseenter", () => { tip.textContent = label; });

      layer.appendChild(dot);
    }
    tip.textContent = `${points.length} temuan dipetakan — klik penanda untuk detail.`;
  }

  // ------------------------------------------------------------- weather ----
  async function loadWeather() {
    const set = (id, text) => { document.getElementById(id).textContent = text; };
    let w;
    try {
      w = await API.get("/weather");
    } catch {
      set("wCond", "gagal memuat");
      return;
    }

    const stale = document.getElementById("weatherStale");
    stale.classList.toggle("show", !!w.stale);

    if (w.error && w.temperature === null) {
      set("wTemp", "—");
      set("wCond", "data cuaca tidak tersedia");
      ["wHumidity", "wWind", "wVisibility", "wPrecip"].forEach((id) => set(id, "—"));
      document.getElementById("wObserved").textContent =
        "Open-Meteo tidak terjangkau dan belum ada data tersimpan.";
      return;
    }

    set("wTemp", w.temperature !== null ? `${Math.round(w.temperature)}°` : "—");
    set("wCond", w.condition || "—");
    set("wHumidity", w.humidity !== null ? `${Math.round(w.humidity)} %` : "—");
    set("wWind", w.wind_speed !== null
      ? `${w.wind_speed.toFixed(1)} m/s ${API.windCompass(w.wind_direction)}`
      : "—");
    set("wVisibility", w.visibility_km !== null ? `${w.visibility_km} km` : "—");
    set("wPrecip", w.precipitation !== null ? `${w.precipitation.toFixed(1)} mm` : "—");

    document.getElementById("wObserved").textContent = w.stale
      ? `Data lama — pengamatan terakhir ${API.fmtDateTime(w.observed_at)}.`
      : `Sumber: Open-Meteo · pengamatan ${API.fmtDateTime(w.observed_at)} (WIT).`;
  }

  // -------------------------------------------------- recent inspections ----
  async function loadRecent() {
    const rows = await API.get("/inspections?limit=10");
    const body = document.getElementById("recentBody");

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="6" class="row-empty">Belum ada inspeksi.</td></tr>`;
      return;
    }

    body.innerHTML = rows.map((r) => `
      <tr>
        <td class="mono">${API.fmtDateTime(r.detected_at)}</td>
        <td>${API.escapeHtml(r.class_name)}</td>
        <td>${API.riskBadge(r.risk_level)}</td>
        <td>${API.statusBadge(r.status)}</td>
        <td>${API.escapeHtml(r.handler_name || "—")}</td>
        <td class="num">${API.fmtDuration(r.response_time_seconds)}</td>
      </tr>`).join("");
  }

  // ---------------------------------------------------------------- boot ----
  async function refresh() {
    // Settle all of them: one failing widget shouldn't blank the others.
    const results = await Promise.allSettled([
      loadSummary(), loadCharts(), loadMap(), loadWeather(), loadRecent(),
    ]);
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length && failed.every((f) => f.reason?.status !== 401)) {
      console.warn("dashboard: sebagian widget gagal", failed.map((f) => f.reason));
    }
  }

  (async () => {
    const user = await Layout.mount({
      title: "Dashboard",
      subtitle: "Ringkasan deteksi FOD & status penanganan",
    });
    if (!user) return;

    renderLegend();
    await refresh();
    setInterval(refresh, REFRESH_MS);
  })();
})();
