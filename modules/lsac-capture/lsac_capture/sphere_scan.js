(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const faces = [
    { id: "front", name: "前面", hint: "将标定 Tracker A 的接触面贴合箱体前面，外法线朝箱体外，保持静止约 1.5 秒。" },
    { id: "back", name: "后面", hint: "将标定 Tracker A 的接触面贴合箱体后面，外法线朝箱体外，保持静止约 1.5 秒。" },
    { id: "left", name: "左面", hint: "将标定 Tracker A 的接触面贴合箱体左面，外法线朝箱体外，保持静止约 1.5 秒。" },
    { id: "right", name: "右面", hint: "将标定 Tracker A 的接触面贴合箱体右面，外法线朝箱体外，保持静止约 1.5 秒。" },
    { id: "top", name: "顶面", hint: "将标定 Tracker A 的接触面贴合箱体顶面，外法线朝箱体外，保持静止约 1.5 秒。" },
    { id: "bottom", name: "底面", hint: "将标定 Tracker A 的接触面贴合箱体底面，外法线朝箱体外，保持静止约 1.5 秒。" },
  ];
  const layerNames = ["内层", "中层", "外层"];
  const phaseNames = { calibration: "标定中", ready: "准备就绪", preparing: "正在准备", scanning: "采集中", paused: "已暂停", finished: "已完成" };
  let state = null;
  let busy = false;
  let pollRunning = false;
  let actionEpoch = 0;
  let connected = false;
  let selectedFace = null;
  let selectedLayer = 0;
  let contactDirty = false;
  let planDirty = false;
  let lastReceipt = 0;
  let lastError = "";
  let noticeTimer = 0;
  let actionStarted = 0;
  let previousProbe = null;
  let probeSpeed = 0;
  let viewDirty = true;
  let lastDraw = 0;
  const camera = { yaw: 2.52, pitch: 0.32, zoom: 1.02 };
  const canvas = $("scan-canvas");
  const ctx = canvas.getContext("2d");
  const size = { width: 1, height: 1, dpr: 1 };

  function finite(value, fallback = 0) { return Number.isFinite(Number(value)) ? Number(value) : fallback; }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  function vec(value) { return Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(Number.isFinite) ? value : null; }
  function text(id, value) { $(id).textContent = value; }
  function disabled(id, value) { $(id).disabled = Boolean(value); }
  function length3(p) { return Math.hypot(p[0], p[1], p[2]); }
  function distance3(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]); }
  function formatTime(seconds) {
    const total = Math.max(0, Math.floor(finite(seconds)));
    const minutes = Math.floor(total / 60);
    return `${String(minutes).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }
  function showNotice(message, kind = "error", autoClose = false) {
    window.clearTimeout(noticeTimer);
    text("notice-text", message);
    $("notice").className = `notice ${kind}`;
    $("notice").hidden = false;
    if (autoClose) noticeTimer = window.setTimeout(() => { $("notice").hidden = true; }, 6500);
  }
  function currentFace() {
    const recorded = state?.calibration?.faces || {};
    return faces.find((f) => f.id === selectedFace) || faces.find((f) => f.id === state?.calibration?.next_face) || faces.find((f) => !recorded[f.id]) || faces[0];
  }
  function isStale() { return !connected || Boolean(state?.source?.stale) || (lastReceipt > 0 && performance.now() - lastReceipt > 2500); }
  function isActive() { return state?.phase === "scanning" || state?.phase === "preparing"; }
  function radiusAt(i) { return finite(state?.plan?.radii_m?.[i], 0); }

  function buildStaticUI() {
    for (let index = 0; index < faces.length; index += 1) {
      const face = faces[index];
      const row = document.createElement("div");
      row.className = "face-row";
      row.dataset.face = face.id;
      const step = document.createElement("span"); step.className = "face-index"; step.textContent = String(index + 1);
      const name = document.createElement("span"); name.className = "face-name"; name.textContent = face.name;
      const status = document.createElement("span"); status.className = "face-state"; status.textContent = "待记录";
      const redo = document.createElement("button"); redo.className = "face-redo"; redo.type = "button"; redo.textContent = "↺"; redo.title = `重做${face.name}`; redo.setAttribute("aria-label", `重做${face.name}`); redo.hidden = true;
      redo.addEventListener("click", (event) => { event.stopPropagation(); selectedFace = face.id; perform("undo_face", { face: face.id }, redo); });
      row.append(step, name, status, redo);
      $("face-list").append(row);
    }
    for (let i = 0; i < 3; i += 1) {
      const row = document.createElement("div"); row.className = "coverage-row";
      const name = document.createElement("span"); name.textContent = layerNames[i];
      const bar = document.createElement("div"); bar.className = "bar"; const fill = document.createElement("i"); fill.id = `coverage-fill-${i}`; fill.style.width = "0%"; bar.append(fill);
      const amount = document.createElement("strong"); amount.id = `coverage-value-${i}`; amount.textContent = "0%";
      row.append(name, bar, amount); $("coverage-bars").append(row);
    }
  }

  async function readJson(response) {
    let payload;
    try { payload = await response.json(); } catch (_) { throw new Error(`服务端返回了无法读取的响应（HTTP ${response.status}）。`); }
    if (!response.ok || payload?.ok === false || payload?.success === false) {
      throw new Error(payload?.error?.message || payload?.error || payload?.message || `请求未完成（HTTP ${response.status}）。`);
    }
    return payload;
  }
  async function poll() {
    if (pollRunning || busy) return;
    const epoch = actionEpoch;
    pollRunning = true;
    try {
      const response = await fetch("/api/state", { cache: "no-store", signal: AbortSignal.timeout(5000) });
      const payload = await readJson(response);
      if (epoch !== actionEpoch || busy) return;
      acceptState(payload.state || payload);
      lastError = "";
    } catch (error) {
      if (epoch !== actionEpoch || busy) return;
      connected = false;
      lastError = error.name === "TimeoutError" ? "状态读取超时，请检查本地服务。" : String(error.message || error);
      updateUI();
    } finally {
      pollRunning = false;
    }
  }
  function acceptState(next) {
    if (!next || typeof next !== "object" || !next.mode || !next.phase) throw new Error("服务端状态缺少模式或阶段信息。");
    const now = performance.now();
    const nextProbe = vec(next.probe?.position_local_m);
    if (nextProbe && previousProbe && now - previousProbe.time > 40) {
      const rawSpeed = distance3(nextProbe, previousProbe.point) / ((now - previousProbe.time) / 1000);
      probeSpeed = rawSpeed < 15 ? probeSpeed * 0.6 + rawSpeed * 0.4 : 0;
    }
    if (nextProbe) previousProbe = { point: nextProbe.slice(), time: now };
    state = next;
    connected = true;
    lastReceipt = now;
    updateUI();
    viewDirty = true;
  }
  async function perform(action, args = {}, button = null) {
    if (busy) return false;
    actionEpoch += 1;
    busy = true;
    actionStarted = performance.now();
    if (button) button.classList.add("busy");
    updateUI();
    try {
      const response = await fetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, ...args }), signal: AbortSignal.timeout(60000) });
      const payload = await readJson(response);
      if (payload.state?.mode) acceptState(payload.state);
      else if (payload.mode) acceptState(payload);
      else await poll();
      if (action === "capture_face" || action === "undo_face") selectedFace = null;
      if (action === "configure_contact") contactDirty = false;
      if (action === "configure_plan") planDirty = false;
      if (action === "reset_calibration") { selectedFace = null; planDirty = false; }
      if (action === "demo_calibration") { contactDirty = false; planDirty = false; }
      if (payload.message) showNotice(payload.message, "success", true);
      return true;
    } catch (error) {
      const prefix = error.name === "TimeoutError" ? "操作仍可能在服务端进行；请查看当前状态后再尝试。" : "操作未完成：";
      showNotice(prefix + (error.name === "TimeoutError" ? "" : String(error.message || error)));
      return false;
    } finally {
      busy = false;
      if (button) button.classList.remove("busy");
      updateUI();
      poll();
    }
  }

  function updateUI() {
    const stale = isStale();
    const phase = state?.phase || "";
    const demo = state?.mode === "demo";
    const active = isActive();
    const paused = phase === "paused";
    const calibration = state?.calibration || {};
    const recorded = calibration.faces || {};
    const count = faces.filter((face) => recorded[face.id]).length;
    const face = currentFace();
    const model = calibration.model;
    const geometryReady = Boolean(model && phase !== "calibration");
    const actionsBlocked = busy || stale;
    const controlsBlocked = busy || !connected;
    text("mode-badge", state ? (demo ? "演示 · 非真实采集" : "真实采集") : "正在连接");
    $("mode-badge").className = `pill ${state ? (demo ? "demo" : "live") : "neutral"}`;
    const sourceGood = state?.source?.connected && !stale;
    const sourceLabel = !connected ? "服务未连接" : stale ? "位姿已过期" : sourceGood ? "位姿在线" : "等待位姿";
    $("source-badge").className = `pill ${sourceGood ? "live" : stale && state ? "error" : "neutral"}`;
    $("source-badge").replaceChildren(Object.assign(document.createElement("i"), {}), document.createTextNode(sourceLabel));
    text("session-badge", phaseNames[phase] || "尚未开始采集");
    const captureStatus = state?.capture?.status || "";
    const captureLabels = { preparing: "准备录音", starting: "准备录音", recording: "录音中", running: "录音中", finishing: "录音收尾中", saved: "录音已保存", error: "录音错误", failed: "录音错误" };
    text("capture-status", !demo && captureLabels[captureStatus] ? captureLabels[captureStatus] : phaseNames[phase] || "未开始");
    $("capture-status").className = `pill small ${["recording", "running"].includes(captureStatus) || demo && phase === "scanning" ? "live" : ["error", "failed"].includes(captureStatus) ? "error" : "neutral"}`;
    $("demo-toggle").hidden = Boolean(state && !demo);
    if (state && !demo) $("demo-panel").hidden = true;
    disabled("demo-toggle", !state || busy);
    disabled("demo-calibration", !demo || actionsBlocked || active || paused);
    disabled("demo-autopath", !demo || actionsBlocked || !model);
    $("demo-autopath").setAttribute("aria-pressed", String(Boolean(state?.demo_motion)));
    text("demo-autopath", state?.demo_motion ? "停止自动演示" : "自动演示轨迹");
    disabled("export-button", !connected || busy || !state);

    text("calibration-count", `${count} / 6`);
    for (const row of $("face-list").children) {
      const entry = faces.find((f) => f.id === row.dataset.face);
      const done = Boolean(recorded[entry.id]);
      const current = entry.id === face.id && !done && count < 6;
      row.className = `face-row${done ? " done" : ""}${current ? " active" : ""}`;
      row.querySelector(".face-index").textContent = done ? "✓" : String(faces.indexOf(entry) + 1);
      row.querySelector(".face-state").textContent = done ? "已记录" : current ? "当前面" : "待记录";
      const redo = row.querySelector(".face-redo"); redo.hidden = !done; redo.disabled = controlsBlocked || active || paused;
    }
    text("face-current-title", geometryReady ? "几何标定已完成" : count === 6 ? "六面已记录" : `接触${face.name}`);
    text("face-current-help", calibration.error || (geometryReady ? "坐标已确认，可以开始扫描。" : count === 6 ? "确认坐标后计算箱体尺寸与球面范围。" : demo ? "演示模式会填入该面的示例位置。" : !calibration.contact_configured ? "先核对下方接触偏移和法向，再记录此面。" : face.hint));
    $("capture-face").replaceChildren(document.createTextNode(count === 6 ? "六面记录完成" : `＋ 记录${face.name}`));
    const captureKey = document.createElement("kbd"); captureKey.textContent = "空格"; $("capture-face").append(captureKey);
    disabled("capture-face", controlsBlocked || active || paused || count === 6 || !calibration.can_capture);
    disabled("calibration-reset", controlsBlocked || active || paused || count === 0);
    disabled("calibration-finish", controlsBlocked || active || paused || count !== 6 || geometryReady);
    text("calibration-finish", geometryReady ? "坐标已确认 ✓" : "确认坐标 →");
    $("dimensions-card").hidden = !model;
    if (model) {
      const d = model.dimensions_m || {};
      text("box-dimensions", `${Math.round(finite(d.width) * 1000)} × ${Math.round(finite(d.height) * 1000)} × ${Math.round(finite(d.depth) * 1000)} mm`);
      text("calibration-status", demo ? "演示几何 · 按真实比例显示" : "已建立箱体坐标 · 按真实比例显示");
    }
    text("contact-status", calibration.contact_configured ? "已确认" : "待确认");
    $("contact-status").className = `tiny-label ${calibration.contact_configured ? "good" : "warning"}`;
    if (!contactDirty && calibration.contact_transform) {
      const p = calibration.contact_transform.position_m;
      if (vec(p)) ["x", "y", "z"].forEach((key, i) => { $("contact-" + key).value = String(Math.round(p[i] * 10000) / 10); });
      const q = calibration.contact_transform.quaternion_wxyz;
      if (Array.isArray(q) && q.length === 4) {
        const [w, x, y, z] = q;
        const n = [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)];
        const largest = n.reduce((best, value, i) => Math.abs(value) > Math.abs(n[best]) ? i : best, 0);
        $("contact-normal").value = (n[largest] >= 0 ? "+" : "-") + ["X", "Y", "Z"][largest];
      }
      $("contact-verified").checked = Boolean(calibration.contact_configured);
    }
    disabled("contact-save", busy || !connected || active || paused || !$("contact-verified").checked);
    for (const field of $("contact-form").querySelectorAll("input,select")) field.disabled = busy || active || paused;

    const segments = Array.isArray(state?.segments) ? state.segments : [];
    const latest = segments[segments.length - 1];
    const currentSeconds = latest ? finite(latest.active_seconds, finite(state?.elapsed_active_seconds)) : finite(state?.elapsed_active_seconds);
    text("capture-timer", formatTime(currentSeconds));
    $("recording-dot").hidden = phase !== "scanning";
    text("segment-start", paused ? "▶ 继续扫描" : phase === "preparing" ? "正在准备…" : demo ? "● 开始演示分段" : "● 录音并播放激励");
    text("capture-note", demo ? "演示采集不会开启录音设备或播放激励。" : "开始后录音并播放已配置激励。暂停仅停止扫描计数，录音与激励继续；结束本轮后停止激励并保存录音。");
    text("segment-finish", demo ? "结束本轮演示 ✓" : "结束本轮并保存 ✓");
    disabled("segment-start", actionsBlocked || active || !model || !["ready", "paused"].includes(phase));
    disabled("segment-pause", controlsBlocked || !["scanning", "preparing"].includes(phase));
    disabled("segment-finish", controlsBlocked || !["scanning", "paused", "preparing"].includes(phase));
    $("new-scan").hidden = phase !== "finished";
    disabled("new-scan", controlsBlocked || active || paused || !demo && captureStatus !== "saved");
    text("segment-count", String(segments.filter((segment) => ["paused", "finished", "complete", "completed", "saved"].includes(segment.status)).length));
    let totalLength = 0;
    for (const trail of state?.trails || []) {
      let previous = null;
      for (const point of trail.points || []) { if (!vec(point)) { previous = null; continue; } if (previous) totalLength += distance3(previous, point); previous = point; }
    }
    text("trajectory-length", totalLength.toFixed(2));

    const recommendation = state?.navigation?.recommendation;
    const recLayer = clamp(finite(recommendation?.layer_index, selectedLayer), 0, 2);
    text("navigation-layer", layerNames[recLayer]);
    let recLength = 0;
    const path = recommendation?.path_local_m || [];
    for (let i = 1; i < path.length; i += 1) if (vec(path[i]) && vec(path[i - 1])) recLength += distance3(path[i], path[i - 1]);
    text("navigation-title", !model ? "先建立箱体坐标" : recommendation ? `沿${layerNames[recLayer]}补扫短弧` : "等待下一段建议");
    const recReason = recommendation?.reason;
    text("navigation-description", typeof recReason === "string" && /[\u3400-\u9fff]/.test(recReason) ? recReason : recommendation ? "补充当前球层尚未充分约束的方向。" : !model ? "完成标定后生成可达的补扫短弧。" : "规划根据已记录的几何约束更新。");
    $("navigation-length").replaceChildren(document.createTextNode(recommendation ? recLength.toFixed(2) : "—"));
    const lengthUnit = document.createElement("small"); lengthUnit.textContent = " m"; $("navigation-length").append(lengthUnit);
    const gain = recommendation?.information_gain;
    text("navigation-gain", Number.isFinite(gain) ? gain.toFixed(2) : "—");
    disabled("navigation-skip", controlsBlocked || !recommendation || phase === "preparing");
    const layers = state?.navigation?.layers || [];
    for (let i = 0; i < 3; i += 1) {
      const layer = layers.find((item) => item.layer_index === i) || layers[i];
      const fraction = clamp(finite(layer?.coverage_fraction), 0, 1);
      $("coverage-fill-" + i).style.width = `${fraction * 100}%`;
      text("coverage-value-" + i, `${Math.round(fraction * 100)}%`);
      text("layer-radius-" + i, radiusAt(i) ? `${radiusAt(i).toFixed(2)} m` : "—");
    }
    text("quality-points", finite(state?.navigation?.recording_bin_count).toLocaleString("zh-CN"));
    const poseAge = state?.source?.pose_age_ms ?? state?.probe?.age_ms;
    text("quality-pose-age", Number.isFinite(poseAge) ? `${Math.round(poseAge)} ms` : stale ? "已过期" : sourceGood ? "在线" : "—");
    const currentLayer = layers.find((item) => item.layer_index === selectedLayer) || layers[selectedLayer];
    text("quality-badge", currentLayer ? `几何秩 ${finite(currentLayer.geometric_rank)} / ${finite(currentLayer.mode_count)}` : "等待数据");
    const qualityWarning = stale ? "扫描探针位姿不可用，暂不能开始扫描。六面标定以 Tracker A 的可用状态为准。" : state?.capture?.error || state?.source?.error;
    text("quality-note", qualityWarning || "覆盖量与球谐约束只用于几何导航，声学置信度尚未验证。");
    $("quality-note").className = `quality-note${qualityWarning ? " warning" : ""}`;

    if (!planDirty && state?.plan) {
      for (let i = 0; i < 3; i += 1) $("plan-radius-" + i).value = radiusAt(i) ? String(Math.round(radiusAt(i) * 1000) / 1000) : "";
      $("plan-min-height").value = state.plan.min_z_m == null ? "" : String(state.plan.min_z_m);
      $("plan-max-height").value = state.plan.max_z_m == null ? "" : String(state.plan.max_z_m);
      $("plan-order").value = String(finite(state.plan.order, 3));
      $("plan-seconds").value = String(finite(state.plan.segment_seconds, 120));
    }
    disabled("plan-save", controlsBlocked || !model || active);
    const hasCoverage = finite(state?.navigation?.recording_bin_count) > 0 || (state?.trails || []).some((trail) => (trail.points || []).length > 0);
    for (const field of $("plan-form").querySelectorAll("input")) {
      const structureField = field.id.startsWith("plan-radius-") || field.id === "plan-order";
      field.disabled = busy || active || (structureField && hasCoverage);
      field.title = structureField && hasCoverage ? "已有扫描数据，当前计划的半径与阶数已锁定" : "";
    }
    const p = vec(state?.probe?.position_local_m);
    text("probe-position", p ? `X ${p[0].toFixed(3)}　Y ${p[1].toFixed(3)}　Z ${p[2].toFixed(3)} m` : "X —　Y —　Z —");
    text("probe-speed", p && !stale ? `${probeSpeed.toFixed(2)} m/s` : "等待有效位姿");
    $("service-dot").className = `status-dot ${!connected ? "error" : stale ? "warning" : ""}`;
    text("service-status", connected ? (demo ? "演示服务" : "采集服务") : "服务未连接");
    text("status-message", busy ? `正在处理操作 · ${((performance.now() - actionStarted) / 1000).toFixed(1)} 秒` : lastError || state?.message || (state ? "已同步最新状态" : "正在读取扫描状态"));
    text("view-subtitle", model ? `${demo ? "演示" : "实际"}尺寸比例 · 拖动旋转 · 滚轮缩放` : "完成标定后显示实际箱体尺寸");
    $("connection-overlay").hidden = Boolean(connected && model && !stale);
    text("connection-title", !connected ? "正在连接测量工作台" : !model ? "从六面标定开始" : stale ? "位姿暂不可用" : "准备扫描");
    text("connection-help", !connected ? lastError || "等待服务端状态与位姿。" : !model ? (demo ? "可逐面体验标定，或在演示工具中载入示例。" : "先确认 Tracker A 的接触偏移与向外法向，再逐面贴合记录。") : "已保留现有轨迹，等待追踪恢复。");
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    size.width = Math.max(1, rect.width); size.height = Math.max(1, rect.height); size.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(size.width * size.dpr); canvas.height = Math.round(size.height * size.dpr);
    viewDirty = true;
  }
  function rotate(p) {
    const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw), cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch);
    const x = cy * p[0] - sy * p[1]; const y = sy * p[0] + cy * p[1];
    return [x, cp * p[2] + sp * y, cp * y - sp * p[2]];
  }
  function sceneRadius() {
    const r = state?.plan?.radii_m || [];
    const max = Math.max(0, ...r.map((value) => finite(value)), finite(state?.calibration?.model?.enclosing_radius_m));
    return max > 0 ? max : 1;
  }
  function project(p) {
    const r = rotate(p), extent = sceneRadius();
    const scale = Math.min(size.width * 0.38, size.height * 0.35) / extent * camera.zoom;
    const perspective = 1 / (1 + r[2] / (extent * 6));
    return [size.width * 0.5 + r[0] * scale * perspective, size.height * 0.48 - r[1] * scale * perspective, r[2], perspective];
  }
  function line(points, color, width = 1, dash = null) {
    if (points.length < 2) return;
    ctx.beginPath(); let started = false;
    for (const point of points) {
      if (!vec(point)) { started = false; continue; }
      const p = project(point); if (!started) { ctx.moveTo(p[0], p[1]); started = true; } else ctx.lineTo(p[0], p[1]);
    }
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []); ctx.stroke(); ctx.setLineDash([]);
  }
  function label(position, value, color = "#738ca1", align = "center") {
    const p = project(position); ctx.font = '10px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.textAlign = align; ctx.textBaseline = "middle"; ctx.fillStyle = color; ctx.fillText(value, p[0], p[1]);
  }
  function drawFloor() {
    const extent = sceneRadius();
    const height = state?.calibration?.model ? -finite(state.calibration.model.dimensions_m?.height) / 2 : -extent * 0.35;
    const grid = extent / 5;
    for (let i = -7; i <= 7; i += 1) {
      const color = i === 0 ? "#49687938" : "#3a566520";
      line([[-extent * 1.4, i * grid, height], [extent * 1.4, i * grid, height]], color);
      line([[i * grid, -extent * 1.4, height], [i * grid, extent * 1.4, height]], color);
    }
    const axisLength = extent * 0.38;
    const origin = [0, 0, height];
    line([origin, [axisLength, 0, height]], "#bf71618c", 1.2);
    line([origin, [0, axisLength, height]], "#658fbd8c", 1.2);
    line([origin, [0, 0, height + axisLength]], "#75b1928c", 1.2);
    label([axisLength * 1.1, 0, height], "X", "#b9867a"); label([0, axisLength * 1.1, height], "Y", "#7d9eb9"); label([0, 0, height + axisLength * 1.1], "Z", "#80ac97");
  }
  function drawShell(radius, layer, front) {
    const chosen = layer === selectedLayer;
    const base = chosen ? [104, 171, 173] : layer === 2 ? [105, 101, 158] : [85, 122, 156];
    const alpha = chosen ? (front ? 0.27 : 0.12) : (front ? 0.12 : 0.06);
    const color = `rgba(${base.join(",")},${alpha})`;
    const curves = [];
    for (let lat = -60; lat <= 60; lat += 30) {
      const z = radius * Math.sin(lat * Math.PI / 180), rr = radius * Math.cos(lat * Math.PI / 180), points = [];
      for (let j = 0; j <= 96; j += 1) { const angle = j / 96 * Math.PI * 2; points.push([rr * Math.cos(angle), rr * Math.sin(angle), z]); }
      curves.push(points);
    }
    for (let lon = 0; lon < 180; lon += 30) {
      const angle = lon * Math.PI / 180, points = [];
      for (let j = 0; j <= 96; j += 1) { const a = j / 96 * Math.PI * 2; points.push([radius * Math.cos(a) * Math.cos(angle), radius * Math.cos(a) * Math.sin(angle), radius * Math.sin(a)]); }
      curves.push(points);
    }
    for (const curve of curves) {
      let segment = [];
      for (const p of curve) {
        if ((rotate(p)[2] < 0) === front) segment.push(p);
        else { if (segment.length > 1) line(segment, color, chosen ? 0.8 : 0.6); segment = []; }
      }
      if (segment.length > 1) line(segment, color, chosen ? 0.8 : 0.6);
    }
  }
  function drawBox() {
    const d = state?.calibration?.model?.dimensions_m;
    if (!d) return;
    const x = finite(d.width) / 2, y = finite(d.depth) / 2, z = finite(d.height) / 2;
    const vertices = [[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z], [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]];
    const sides = [
      { indices: [0, 1, 5, 4], fill: "#263d49" }, { indices: [1, 2, 6, 5], fill: "#1c303d" },
      { indices: [2, 3, 7, 6], fill: "#203540" }, { indices: [3, 0, 4, 7], fill: "#203743" },
      { indices: [4, 5, 6, 7], fill: "#304b56" }, { indices: [3, 2, 1, 0], fill: "#172932" },
    ];
    for (const side of sides) side.depth = side.indices.reduce((sum, i) => sum + rotate(vertices[i])[2], 0) / 4;
    sides.sort((a, b) => b.depth - a.depth);
    for (const side of sides) {
      ctx.beginPath(); side.indices.forEach((index, i) => { const p = project(vertices[index]); if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); }); ctx.closePath();
      ctx.fillStyle = side.fill; ctx.fill(); ctx.strokeStyle = "#6d9ca575"; ctx.lineWidth = 1; ctx.stroke();
    }
    const center = project([0, 0, 0]); ctx.strokeStyle = "#a5cecb85"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(center[0] - 4, center[1]); ctx.lineTo(center[0] + 4, center[1]); ctx.moveTo(center[0], center[1] - 4); ctx.lineTo(center[0], center[1] + 4); ctx.stroke();
    label([0, 0, z + sceneRadius() * 0.075], "箱体参考中心", "#91abb6");
    const dimensionY = -y - sceneRadius() * 0.065;
    line([[-x, dimensionY, -z], [x, dimensionY, -z]], "#6a829b80", 0.7);
    label([0, dimensionY, -z - sceneRadius() * 0.04], `${Math.round(x * 2000)} mm`, "#718a9c");
    if (rotate([0, y, 0])[2] < 0) label([0, y + sceneRadius() * 0.012, 0], "前面 +Y", "#a4c7cb");
  }
  function gainColor(value, alpha) {
    const stops = [[43, 88, 121], [69, 164, 172], [166, 196, 133], [241, 171, 99]];
    const p = clamp(value, 0, 1) * 3, index = Math.min(2, Math.floor(p)), fraction = p - index;
    const color = stops[index].map((v, channel) => Math.round(v + (stops[index + 1][channel] - v) * fraction));
    return `rgba(${color.join(",")},${alpha})`;
  }
  function drawCells(front) {
    if (!$("show-gain").checked) return;
    const layers = state?.navigation?.layers || [];
    const layer = layers.find((item) => item.layer_index === selectedLayer) || layers[selectedLayer];
    const cells = layer?.cells || [];
    const values = cells.filter((cell) => cell.reachable !== false).map((cell) => Math.max(0, finite(cell.information_gain))).sort((a, b) => a - b);
    const scale = values[Math.max(0, Math.floor(values.length * 0.93) - 1)] || 1;
    for (const cell of cells) {
      const point = vec(cell.point_local_m) || (vec(cell.direction) ? cell.direction.map((component) => component * radiusAt(selectedLayer)) : null);
      if (!point || ((rotate(point)[2] < 0) !== front)) continue;
      const p = project(point), dot = clamp(Math.min(size.width, size.height) * 0.0055 * p[3] * camera.zoom, 1.6, 5.5);
      ctx.beginPath(); ctx.arc(p[0], p[1], dot, 0, Math.PI * 2);
      if (cell.reachable === false) {
        ctx.strokeStyle = `rgba(95,111,129,${front ? 0.3 : 0.1})`; ctx.lineWidth = 0.7; ctx.stroke();
      } else if (cell.visited) {
        ctx.fillStyle = `rgba(100,183,166,${front ? 0.62 : 0.24})`; ctx.fill();
      } else {
        const v = Math.log1p(Math.max(0, finite(cell.information_gain)) / scale * 4) / Math.log(5);
        ctx.fillStyle = gainColor(v, front ? 0.62 : 0.2); ctx.fill();
      }
    }
  }
  function drawTrails() {
    if (!$("show-history").checked) return;
    for (let index = 0; index < (state?.trails || []).length; index += 1) {
      const points = state.trails[index].points || [];
      if (points.length > 1) line(points, index === state.trails.length - 1 && state.phase === "scanning" ? "#74b7e3a6" : "#568ab275", 1.4);
    }
  }
  function drawRecommendation() {
    const rec = state?.navigation?.recommendation;
    if (!rec) return;
    const points = (rec.path_local_m || []).filter((point) => vec(point));
    if (points.length > 1) {
      ctx.save(); ctx.shadowColor = "#5fe5bd60"; ctx.shadowBlur = 7; line(points, "#6ce6bf", 2.3); ctx.restore();
      const end = project(points[points.length - 1]), before = project(points[Math.max(0, points.length - 3)]);
      const angle = Math.atan2(end[1] - before[1], end[0] - before[0]);
      ctx.beginPath(); ctx.moveTo(end[0], end[1]); ctx.lineTo(end[0] - 9 * Math.cos(angle - 0.45), end[1] - 9 * Math.sin(angle - 0.45)); ctx.lineTo(end[0] - 9 * Math.cos(angle + 0.45), end[1] - 9 * Math.sin(angle + 0.45)); ctx.closePath(); ctx.fillStyle = "#8aefce"; ctx.fill();
    }
    const target = vec(rec.point_local_m) || points[0];
    if (target) {
      const p = project(target); ctx.beginPath(); ctx.arc(p[0], p[1], 8, 0, Math.PI * 2); ctx.strokeStyle = "#8ff0cfbb"; ctx.lineWidth = 1.2; ctx.stroke(); ctx.beginPath(); ctx.arc(p[0], p[1], 2, 0, Math.PI * 2); ctx.fillStyle = "#b5ffe6"; ctx.fill();
    }
  }
  function drawProbe() {
    const p = vec(state?.probe?.position_local_m);
    if (!p) return;
    const view = project(p), color = isStale() || state.probe.fresh === false ? "#927f65" : "#f4ddb0";
    line([[0, 0, 0], p], "#acbfba37", 0.8, [3, 5]);
    ctx.save(); ctx.shadowColor = color; ctx.shadowBlur = 13; ctx.beginPath(); ctx.arc(view[0], view[1], 4.5, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill(); ctx.restore();
    ctx.strokeStyle = color + "88"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(view[0], view[1], 9.5, 0, Math.PI * 2); ctx.stroke();
    ctx.font = '10px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.textAlign = "left"; ctx.fillStyle = color; ctx.fillText("探针", view[0] + 15, view[1] - 10);
  }
  function drawOrientation() {
    const origin = [size.width - 47, size.height - 192];
    const axes = [{ v: [1, 0, 0], label: "X", color: "#c38b7e" }, { v: [0, 1, 0], label: "Y", color: "#759ac5" }, { v: [0, 0, 1], label: "Z", color: "#82b99b" }];
    for (const axis of axes) {
      const p = rotate(axis.v); ctx.beginPath(); ctx.moveTo(origin[0], origin[1]); ctx.lineTo(origin[0] + p[0] * 24, origin[1] - p[1] * 24); ctx.strokeStyle = axis.color; ctx.lineWidth = 1.4; ctx.stroke(); ctx.font = "9px Segoe UI"; ctx.fillStyle = axis.color; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(axis.label, origin[0] + p[0] * 32, origin[1] - p[1] * 32);
    }
  }
  function draw(now) {
    requestAnimationFrame(draw);
    if (!viewDirty && now - lastDraw < 800) return;
    lastDraw = now; viewDirty = false;
    ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0); ctx.clearRect(0, 0, size.width, size.height);
    drawFloor();
    if (state?.calibration?.model) {
      if ($("show-shells").checked) for (let i = 2; i >= 0; i -= 1) if (radiusAt(i)) drawShell(radiusAt(i), i, false);
      drawCells(false); drawBox(); drawTrails();
      if ($("show-shells").checked) for (let i = 2; i >= 0; i -= 1) if (radiusAt(i)) drawShell(radiusAt(i), i, true);
      drawCells(true); drawRecommendation(); drawProbe();
    }
    drawOrientation();
  }

  let pointer = null;
  canvas.addEventListener("pointerdown", (event) => { pointer = { id: event.pointerId, x: event.clientX, y: event.clientY }; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener("pointermove", (event) => {
    if (!pointer || pointer.id !== event.pointerId) return;
    camera.yaw += (event.clientX - pointer.x) * 0.007;
    camera.pitch = clamp(camera.pitch + (event.clientY - pointer.y) * 0.007, -1.48, 1.48);
    pointer.x = event.clientX; pointer.y = event.clientY; viewDirty = true;
  });
  canvas.addEventListener("pointerup", () => { pointer = null; });
  canvas.addEventListener("pointercancel", () => { pointer = null; });
  canvas.addEventListener("wheel", (event) => { event.preventDefault(); camera.zoom = clamp(camera.zoom * Math.exp(-event.deltaY * 0.0012), 0.35, 4); viewDirty = true; }, { passive: false });
  canvas.addEventListener("dblclick", () => { camera.yaw = 2.52; camera.pitch = 0.32; camera.zoom = 1.02; viewDirty = true; });
  $("camera-reset").addEventListener("click", () => { camera.yaw = 2.52; camera.pitch = 0.32; camera.zoom = 1.02; viewDirty = true; });
  $("camera-front").addEventListener("click", () => { camera.yaw = Math.PI; camera.pitch = 0; viewDirty = true; });
  $("camera-top").addEventListener("click", () => { camera.yaw = 0; camera.pitch = 1.48; viewDirty = true; });
  for (const button of document.querySelectorAll("[data-layer]")) button.addEventListener("click", () => {
    selectedLayer = Number(button.dataset.layer);
    for (const item of document.querySelectorAll("[data-layer]")) { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); }
    updateUI(); viewDirty = true;
  });
  for (const id of ["show-shells", "show-history", "show-gain"]) $(id).addEventListener("change", () => { viewDirty = true; });

  async function toggleFullscreen() {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
    catch (error) { showNotice(`未能切换全屏：${error.message || error}`); }
  }
  $("fullscreen-button").addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    const full = Boolean(document.fullscreenElement);
    $("fullscreen-button").setAttribute("aria-label", full ? "退出全屏（ESC）" : "进入全屏（F）");
    $("fullscreen-button").title = full ? "退出全屏 ESC" : "全屏 F";
    text("fullscreen-hint", full ? "ESC 退出全屏" : "F 全屏"); resizeCanvas();
  });
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (target?.matches("input,select,textarea") || target?.isContentEditable || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key.toLowerCase() === "f" && !event.repeat) { event.preventDefault(); toggleFullscreen(); }
    if (event.code === "Space" && !$("capture-face").disabled && !event.repeat && !target?.matches("button")) { event.preventDefault(); $("capture-face").click(); }
  });
  $("notice-close").addEventListener("click", () => { $("notice").hidden = true; });
  $("demo-toggle").addEventListener("click", () => { $("demo-panel").hidden = !$("demo-panel").hidden; });
  $("demo-calibration").addEventListener("click", () => perform("demo_calibration", {}, $("demo-calibration")));
  $("demo-autopath").addEventListener("click", () => perform("demo_motion", { enabled: !state?.demo_motion }, $("demo-autopath")));
  $("capture-face").addEventListener("click", () => perform("capture_face", { face: currentFace().id }, $("capture-face")));
  $("calibration-reset").addEventListener("click", () => perform("reset_calibration", {}, $("calibration-reset")));
  $("calibration-finish").addEventListener("click", () => perform("finish_calibration", {}, $("calibration-finish")));
  $("segment-start").addEventListener("click", () => perform("start", {}, $("segment-start")));
  $("segment-pause").addEventListener("click", () => perform("pause", {}, $("segment-pause")));
  $("segment-finish").addEventListener("click", () => perform("finish", {}, $("segment-finish")));
  $("new-scan").addEventListener("click", () => perform("new_scan", {}, $("new-scan")));
  $("navigation-skip").addEventListener("click", () => perform("skip", {}, $("navigation-skip")));
  $("contact-form").addEventListener("input", () => { contactDirty = true; updateUI(); });
  $("contact-form").addEventListener("change", () => { contactDirty = true; updateUI(); });
  $("contact-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("contact-verified").checked) { showNotice("请先核对实际安装并勾选确认。"); return; }
    const offset = ["x", "y", "z"].map((axis) => Number($("contact-" + axis).value));
    if (!offset.every(Number.isFinite)) { showNotice("请填写三个有效的接触点偏移数值。"); return; }
    perform("configure_contact", { offset_mm: offset, normal_axis: $("contact-normal").value.toLowerCase(), confirmed: true }, $("contact-save"));
  });
  $("plan-form").addEventListener("input", () => { planDirty = true; });
  $("plan-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const radii = [0, 1, 2].map((i) => Number($("plan-radius-" + i).value));
    const low = $("plan-min-height").value === "" ? null : Number($("plan-min-height").value);
    const high = $("plan-max-height").value === "" ? null : Number($("plan-max-height").value);
    if (!radii.every((value) => Number.isFinite(value) && value > 0) || !(radii[0] < radii[1] && radii[1] < radii[2])) { showNotice("三层半径需要为正数，且依次增大。"); return; }
    if (radii[0] <= finite(state?.calibration?.model?.enclosing_radius_m)) { showNotice("内层球面需要完整包围箱体，请增大内层半径。"); return; }
    if (low != null && high != null && low >= high) { showNotice("最低高度需要小于最高高度。"); return; }
    perform("configure_plan", { radii_m: radii, order: Number($("plan-order").value), min_z_m: low, max_z_m: high, segment_seconds: Number($("plan-seconds").value) }, $("plan-save"));
  });
  $("export-button").addEventListener("click", async () => {
    const button = $("export-button"); button.disabled = true; button.classList.add("busy");
    try {
      const response = await fetch("/api/export", { cache: "no-store", signal: AbortSignal.timeout(15000) });
      const data = await readJson(response);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = `lsac-sphere-scan-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      document.body.append(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      showNotice("扫描记录已导出为 JSON。", "success", true);
    } catch (error) { showNotice(`导出未完成：${error.message || error}`); }
    finally { button.classList.remove("busy"); updateUI(); }
  });

  buildStaticUI();
  new ResizeObserver(resizeCanvas).observe(canvas);
  window.addEventListener("resize", resizeCanvas);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { poll(); resizeCanvas(); } });
  resizeCanvas(); updateUI(); requestAnimationFrame(draw); poll();
  window.setInterval(() => { if (!document.hidden) poll(); }, 500);
  window.setInterval(() => { if (busy || isStale()) updateUI(); }, 250);
}());
