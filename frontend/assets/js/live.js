/* ==========================================================================
   FOD Sentry — live detection client
   Captures the webcam, streams JPEG frames to the backend over a WebSocket
   (one frame in flight at a time = automatic backpressure so the stream
   never lags behind a slow network or a slow model), and paints detections
   on a canvas every animation frame regardless of when the next server
   reply arrives — so the video always looks smooth even if detections
   update a little slower than the camera.

   Carried over from the prototype's app.js. New here: the risk badge on each
   box, the three server-side pre-processing toggles, and the camera label that
   gets stored with every recorded detection.
   ========================================================================== */

(() => {
  "use strict";

  // ---- DOM ----
  const videoEl         = document.getElementById("video");
  const stageCanvas     = document.getElementById("stage");
  const stageCtx        = stageCanvas.getContext("2d");
  const sendCanvas      = document.getElementById("sendCanvas");
  const sendCtx         = sendCanvas.getContext("2d");
  const stageEmpty      = document.getElementById("stageEmpty");
  const scanlineEl      = document.getElementById("scanline");
  const recIndicator    = document.getElementById("recIndicator");

  const cameraSelect    = document.getElementById("cameraSelect");
  const startBtn        = document.getElementById("startBtn");
  const stopBtn         = document.getElementById("stopBtn");

  const confSlider      = document.getElementById("confSlider");
  const confVal         = document.getElementById("confVal");
  const iouSlider       = document.getElementById("iouSlider");
  const iouVal          = document.getElementById("iouVal");
  const resSlider       = document.getElementById("resSlider");
  const resVal          = document.getElementById("resVal");
  const qualSlider      = document.getElementById("qualSlider");
  const qualVal         = document.getElementById("qualVal");
  const fpsCapSlider    = document.getElementById("fpsCapSlider");
  const fpsCapVal       = document.getElementById("fpsCapVal");

  const cbDenoise       = document.getElementById("cbDenoise");
  const cbClahe         = document.getElementById("cbClahe");
  const cbSharpen       = document.getElementById("cbSharpen");

  const roSent          = document.getElementById("roSent");
  const roDetect        = document.getElementById("roDetect");
  const roLatency       = document.getElementById("roLatency");
  const roInfer         = document.getElementById("roInfer");

  const eventLogEl      = document.getElementById("eventLog");
  const clearLogBtn     = document.getElementById("clearLog");
  const detectionListEl = document.getElementById("detectionList");
  const activeCountEl   = document.getElementById("activeCount");

  // Injected by layout.js into the topbar.
  let connChip, connDot, connLabel, providerLabel, classCountLabel;

  // ---- State ----
  let ws = null;
  let mediaStream = null;
  let isStreaming = false;
  let sending = false;          // true while a frame is in flight (backpressure gate)
  let sendTimeoutId = null;
  let lastSendTime = 0;
  let reconnectAttempt = 0;
  let reconnectTimer = null;

  let latestDetections = [];
  let previousClassSet = new Set();

  let sendWidth = parseInt(resSlider.value, 10);
  let jpegQuality = parseFloat(qualSlider.value);
  let minSendInterval = 1000 / parseInt(fpsCapSlider.value, 10);
  let confThreshold = parseFloat(confSlider.value);
  let iouThreshold = parseFloat(iouSlider.value);

  // rolling counters for fps readouts
  let sentTimestamps = [];
  let detectTimestamps = [];

  // ---- Colors: deterministic, evenly spaced hue per class id ----
  function classHue(classId) {
    return ((classId * 137.508) % 360).toFixed(0); // golden angle -> good spread
  }

  /** Bright variant — reticles and chips drawn over the dark video frame. */
  function colorForClass(classId) {
    return `hsl(${classHue(classId)}, 82%, 62%)`;
  }

  /** Darkened variant — the same hue used as text on the white panels. */
  function textColorForClass(classId) {
    return `hsl(${classHue(classId)}, 72%, 32%)`;
  }

  // ==========================================================================
  // Camera enumeration
  // ==========================================================================

  async function populateCameraList() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const cams = devices.filter((d) => d.kind === "videoinput");
      cameraSelect.innerHTML = "";

      if (cams.length === 0) {
        const opt = document.createElement("option");
        opt.textContent = "Kamera tidak ditemukan";
        cameraSelect.appendChild(opt);
        startBtn.disabled = true;
        return;
      }

      cams.forEach((cam, i) => {
        const opt = document.createElement("option");
        opt.value = cam.deviceId;
        const label = cam.label || `Camera ${i + 1}`;
        const isExternal = /usb|external|capture/i.test(label);
        opt.textContent = isExternal ? `${label} (external)` : label;
        cameraSelect.appendChild(opt);
      });
      startBtn.disabled = false;
    } catch (err) {
      console.error("enumerateDevices failed", err);
    }
  }

  async function unlockCameraLabelsThenList() {
    // Labels are blank until permission is granted once.
    try {
      const tmp = await navigator.mediaDevices.getUserMedia({ video: true });
      tmp.getTracks().forEach((t) => t.stop());
    } catch (err) {
      // user may deny — list will just show generic labels
    }
    await populateCameraList();
  }

  /** Whatever the dropdown shows is what gets stored on each detection row. */
  function selectedCameraLabel() {
    const opt = cameraSelect.selectedOptions[0];
    return opt ? opt.textContent.trim() : null;
  }

  // ==========================================================================
  // WebSocket
  // ==========================================================================

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws`;
  }

  function connectWS() {
    setConn("connecting");
    ws = new WebSocket(wsUrl());
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      reconnectAttempt = 0;
      setConn("live");
      sendConfig();
    };

    ws.onclose = (evt) => {
      sending = false;
      // 4401 = the server rejected our cookie. Reconnecting would just loop.
      if (evt.code === 4401) {
        isStreaming = false;
        setConn("error");
        API.toast("Sesi berakhir — mengalihkan ke halaman login…", true);
        setTimeout(() => API.redirectToLogin(), 1200);
        return;
      }
      if (isStreaming) {
        setConn("connecting");
        scheduleReconnect();
      } else {
        setConn("offline");
      }
    };

    ws.onerror = () => setConn("error");

    ws.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch {
        return;
      }

      if (msg.type === "hello") {
        providerLabel.textContent = msg.provider || "—";
        if (msg.classes) classCountLabel.textContent = Object.keys(msg.classes).length;
      } else if (msg.type === "detections") {
        sending = false;
        clearTimeout(sendTimeoutId);
        latestDetections = msg.detections || [];
        const now = performance.now();
        roLatency.textContent = Math.max(0, Math.round(now - msg.ts));
        roInfer.textContent = Math.round(msg.infer_ms || 0);
        detectTimestamps.push(now);
        updateDetectionPanel(latestDetections);
        logNewDetections(latestDetections);
      } else if (msg.type === "error") {
        sending = false;
        clearTimeout(sendTimeoutId);
        console.warn("server error:", msg.message);
      }
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectAttempt += 1;
    const delay = Math.min(4000, 400 * reconnectAttempt);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (isStreaming) connectWS();
    }, delay);
  }

  function sendConfig() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "config",
        conf: confThreshold,
        iou: iouThreshold,
        denoise: cbDenoise.checked,
        clahe: cbClahe.checked,
        sharpen: cbSharpen.checked,
        camera_label: selectedCameraLabel(),
      }));
    }
  }

  function setConn(state) {
    connChip.classList.remove("live", "err");
    if (state === "live") {
      connChip.classList.add("live");
      connLabel.textContent = "LIVE";
    } else if (state === "connecting") {
      connLabel.textContent = "CONNECTING…";
    } else if (state === "error") {
      connChip.classList.add("err");
      connLabel.textContent = "ERROR";
    } else {
      connLabel.textContent = "OFFLINE";
    }
  }

  // ==========================================================================
  // Capture / render loop
  // ==========================================================================

  function onLoadedMetadata() {
    const vw = videoEl.videoWidth || 1280;
    const vh = videoEl.videoHeight || 720;
    stageCanvas.width = vw;
    stageCanvas.height = vh;
    stageEmpty.style.display = "none";
  }

  function drawLoop() {
    if (!isStreaming) return;

    if (videoEl.readyState >= 2) {
      stageCtx.drawImage(videoEl, 0, 0, stageCanvas.width, stageCanvas.height);
      drawDetections();
    }

    maybeSendFrame();
    requestAnimationFrame(drawLoop);
  }

  function drawDetections() {
    const w = stageCanvas.width;
    const h = stageCanvas.height;

    for (const det of latestDetections) {
      const x1 = det.x1 * w, y1 = det.y1 * h;
      const x2 = det.x2 * w, y2 = det.y2 * h;
      const bw = x2 - x1, bh = y2 - y1;
      const color = colorForClass(det.class_id);
      const armLen = Math.max(10, Math.min(24, Math.min(bw, bh) * 0.3));

      stageCtx.save();
      stageCtx.strokeStyle = color;
      stageCtx.lineWidth = Math.max(2, w / 480);
      stageCtx.lineCap = "square";

      // corner-bracket reticle (signature detection marker)
      const corners = [
        [x1, y1, 1, 1], [x2, y1, -1, 1], [x1, y2, 1, -1], [x2, y2, -1, -1],
      ];
      for (const [cx, cy, dx, dy] of corners) {
        stageCtx.beginPath();
        stageCtx.moveTo(cx, cy + armLen * dy);
        stageCtx.lineTo(cx, cy);
        stageCtx.lineTo(cx + armLen * dx, cy);
        stageCtx.stroke();
      }

      // faint fill so overlapping boxes stay legible
      stageCtx.globalAlpha = 0.06;
      stageCtx.fillStyle = color;
      stageCtx.fillRect(x1, y1, bw, bh);
      stageCtx.globalAlpha = 1;

      // label chip
      const label = `${det.class_name} ${(det.conf * 100).toFixed(0)}%`;
      const fontSize = Math.max(12, Math.round(w / 90));
      stageCtx.font = `600 ${fontSize}px "JetBrains Mono", monospace`;
      const textW = stageCtx.measureText(label).width;
      const padX = 6, padY = 4;
      const chipH = fontSize + padY * 2;
      const chipY = Math.max(0, y1 - chipH - 3);

      stageCtx.fillStyle = color;
      stageCtx.fillRect(x1, chipY, textW + padX * 2, chipH);
      stageCtx.fillStyle = "#0a0d12";
      stageCtx.fillText(label, x1 + padX, chipY + chipH - padY - 2);

      // risk badge chip, right after the class label
      if (det.risk_level) {
        const riskText = `${det.risk_level} ${det.risk_score}/25`;
        const riskW = stageCtx.measureText(riskText).width;
        const riskX = x1 + textW + padX * 2 + 4;
        stageCtx.fillStyle = API.RISK_COLORS[det.risk_level] || "#8b93a3";
        stageCtx.fillRect(riskX, chipY, riskW + padX * 2, chipH);
        stageCtx.fillStyle = "#0a0d12";
        stageCtx.fillText(riskText, riskX + padX, chipY + chipH - padY - 2);
      }

      stageCtx.restore();
    }
  }

  function maybeSendFrame() {
    if (!isStreaming || sending || !ws || ws.readyState !== WebSocket.OPEN) return;

    const now = performance.now();
    if (now - lastSendTime < minSendInterval) return;
    lastSendTime = now;

    const vw = videoEl.videoWidth, vh = videoEl.videoHeight;
    if (!vw || !vh) return;

    const targetW = Math.min(sendWidth, vw);
    const targetH = Math.round((targetW / vw) * vh);
    sendCanvas.width = targetW;
    sendCanvas.height = targetH;
    sendCtx.drawImage(videoEl, 0, 0, targetW, targetH);

    sending = true; // lock immediately — released on server reply, error, or timeout
    sendTimeoutId = setTimeout(() => { sending = false; }, 3000);

    sendCanvas.toBlob(
      (blob) => {
        if (!blob) { sending = false; return; }
        blob.arrayBuffer().then((buf) => {
          if (!ws || ws.readyState !== WebSocket.OPEN) { sending = false; return; }
          const header = new ArrayBuffer(8);
          new DataView(header).setFloat64(0, performance.now(), false);
          const payload = new Uint8Array(8 + buf.byteLength);
          payload.set(new Uint8Array(header), 0);
          payload.set(new Uint8Array(buf), 8);
          ws.send(payload);
          sentTimestamps.push(performance.now());
        });
      },
      "image/jpeg",
      jpegQuality
    );
  }

  // ==========================================================================
  // Telemetry
  // ==========================================================================

  function trimRolling(arr, windowMs) {
    const cutoff = performance.now() - windowMs;
    while (arr.length && arr[0] < cutoff) arr.shift();
    return arr;
  }

  function tickTelemetry() {
    trimRolling(sentTimestamps, 2000);
    trimRolling(detectTimestamps, 2000);
    roSent.textContent = (sentTimestamps.length / 2).toFixed(1);
    roDetect.textContent = (detectTimestamps.length / 2).toFixed(1);
  }

  // ==========================================================================
  // Detection panel + event log
  // ==========================================================================

  function updateDetectionPanel(detections) {
    activeCountEl.textContent = detections.length;

    if (detections.length === 0) {
      detectionListEl.innerHTML = `<li class="det-empty">Belum ada objek terdeteksi.</li>`;
      return;
    }

    const byClass = new Map();
    for (const d of detections) {
      const cur = byClass.get(d.class_id);
      if (!cur || d.conf > cur.conf) {
        byClass.set(d.class_id, {
          name: d.class_name,
          conf: d.conf,
          level: d.risk_level,
          score: d.risk_score,
          count: (cur ? cur.count : 0) + 1,
        });
      } else {
        cur.count += 1;
      }
    }

    const rows = [...byClass.entries()].sort((a, b) => b[1].conf - a[1].conf);
    detectionListEl.innerHTML = rows.map(([classId, info]) => `
      <li class="det-row">
        <span class="det-swatch" style="background:${colorForClass(classId)}"></span>
        <span class="det-name">${API.escapeHtml(info.name)}</span>
        ${info.count > 1 ? `<span class="det-count">×${info.count}</span>` : ""}
        ${info.level ? API.riskBadge(info.level) : ""}
        <span class="det-conf">${(info.conf * 100).toFixed(0)}%</span>
      </li>
    `).join("");
  }

  function logNewDetections(detections) {
    const currentSet = new Set(detections.map((d) => d.class_id));
    const newlyAppeared = [...currentSet].filter((id) => !previousClassSet.has(id));

    if (newlyAppeared.length > 0) {
      const emptyEl = eventLogEl.querySelector(".event-empty");
      if (emptyEl) emptyEl.remove();

      for (const classId of newlyAppeared) {
        const det = detections.find((d) => d.class_id === classId);
        const li = document.createElement("li");
        const time = new Date().toLocaleTimeString([], { hour12: false });
        li.innerHTML = `
          <span class="ev-time">${time}</span>
          <span class="ev-class" style="color:${textColorForClass(classId)}">${API.escapeHtml(det.class_name)}</span>
          ${det.risk_level ? `<span class="ev-risk">${API.riskBadge(det.risk_level)}</span>` : ""}
          <span class="ev-conf">${(det.conf * 100).toFixed(0)}%</span>
        `;
        eventLogEl.prepend(li);
      }
      while (eventLogEl.children.length > 60) {
        eventLogEl.removeChild(eventLogEl.lastChild);
      }
    }
    previousClassSet = currentSet;
  }

  // ==========================================================================
  // Start / Stop
  // ==========================================================================

  async function start() {
    startBtn.disabled = true;
    try {
      const deviceId = cameraSelect.value;
      const constraints = {
        video: deviceId
          ? { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
          : { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      };
      mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      videoEl.srcObject = mediaStream;
      await videoEl.play();

      isStreaming = true;
      stopBtn.disabled = false;
      cameraSelect.disabled = true;
      scanlineEl.classList.add("active");
      recIndicator.classList.add("on");

      connectWS();
      requestAnimationFrame(drawLoop);
    } catch (err) {
      console.error("getUserMedia failed", err);
      API.toast("Kamera tidak bisa diakses. Cek izin dan pastikan tidak dipakai aplikasi lain.", true);
      startBtn.disabled = false;
    }
  }

  function stop() {
    isStreaming = false;
    sending = false;
    clearTimeout(sendTimeoutId);
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

    if (mediaStream) {
      mediaStream.getTracks().forEach((t) => t.stop());
      mediaStream = null;
    }
    videoEl.srcObject = null;

    if (ws) {
      ws.onclose = null;
      ws.close();
      ws = null;
    }

    stageCtx.clearRect(0, 0, stageCanvas.width, stageCanvas.height);
    stageEmpty.style.display = "flex";
    scanlineEl.classList.remove("active");
    recIndicator.classList.remove("on");
    setConn("offline");

    startBtn.disabled = false;
    stopBtn.disabled = true;
    cameraSelect.disabled = false;

    latestDetections = [];
    previousClassSet = new Set();
    updateDetectionPanel([]);
    roSent.textContent = "0.0";
    roDetect.textContent = "0.0";
    roLatency.textContent = "0";
    roInfer.textContent = "0";

    // Newly recorded detections are now in the DB — refresh the bell.
    Layout.pollNotifications();
  }

  // ==========================================================================
  // Boot
  // ==========================================================================

  (async () => {
    const user = await Layout.mount({
      title: "Deteksi Live",
      subtitle: "Streaming webcam → YOLOv8 ONNX → skor risiko",
      topbarExtra: `
        <div class="status-chip" id="connChip">
          <span class="dot" id="connDot"></span>
          <span id="connLabel">OFFLINE</span>
        </div>
        <div class="status-chip mono">
          <span class="chip-label">ENGINE</span>
          <span id="providerLabel">—</span>
        </div>
        <div class="status-chip mono">
          <span class="chip-label">CLASSES</span>
          <span id="classCountLabel">31</span>
        </div>`,
    });
    if (!user) return;

    connChip        = document.getElementById("connChip");
    connDot         = document.getElementById("connDot");
    connLabel       = document.getElementById("connLabel");
    providerLabel   = document.getElementById("providerLabel");
    classCountLabel = document.getElementById("classCountLabel");

    videoEl.addEventListener("loadedmetadata", onLoadedMetadata);
    startBtn.addEventListener("click", start);
    stopBtn.addEventListener("click", stop);

    confSlider.addEventListener("input", () => {
      confThreshold = parseFloat(confSlider.value);
      confVal.textContent = confThreshold.toFixed(2);
      sendConfig();
    });
    iouSlider.addEventListener("input", () => {
      iouThreshold = parseFloat(iouSlider.value);
      iouVal.textContent = iouThreshold.toFixed(2);
      sendConfig();
    });
    resSlider.addEventListener("input", () => {
      sendWidth = parseInt(resSlider.value, 10);
      resVal.textContent = `${sendWidth}px`;
    });
    qualSlider.addEventListener("input", () => {
      jpegQuality = parseFloat(qualSlider.value);
      qualVal.textContent = jpegQuality.toFixed(2);
    });
    fpsCapSlider.addEventListener("input", () => {
      const fps = parseInt(fpsCapSlider.value, 10);
      minSendInterval = 1000 / fps;
      fpsCapVal.textContent = `${fps} fps`;
    });

    [cbDenoise, cbClahe, cbSharpen].forEach((cb) =>
      cb.addEventListener("change", sendConfig)
    );

    clearLogBtn.addEventListener("click", () => {
      eventLogEl.innerHTML =
        `<li class="event-empty">Kejadian akan muncul di sini setelah pemindaian dimulai.</li>`;
    });

    window.addEventListener("beforeunload", () => { if (isStreaming) stop(); });
    setInterval(tickTelemetry, 500);

    // Fetch model info before the user even starts, so the provider/class
    // count chips aren't blank on first paint.
    try {
      const res = await fetch("/health", { credentials: "same-origin" });
      const info = await res.json();
      providerLabel.textContent = info.provider;
      classCountLabel.textContent = info.num_classes;
    } catch {
      // backend not reachable yet — chips stay at defaults, /ws hello fills them
    }
    await unlockCameraLabelsThenList();
    navigator.mediaDevices.addEventListener?.("devicechange", populateCameraList);
  })();
})();
