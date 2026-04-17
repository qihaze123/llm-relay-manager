const { request, formToObject, log, pollJob, escapeHtml, createModal } = window.RelayCommon;

const stationsList = document.getElementById("stations-list");
const pageLog = document.getElementById("page-log");
const createStationBtn = document.getElementById("create-station-btn");
const stationSearch = document.getElementById("station-search");
const stationEnabledOnly = document.getElementById("station-enabled-only");

const stationForm = document.getElementById("station-form");
const stationFormFeedback = document.getElementById("station-form-feedback");
const stationModal = createModal(document.getElementById("station-modal"));

const keyForm = document.getElementById("key-form");
const keyFormFeedback = document.getElementById("key-form-feedback");
const keyStationMeta = document.getElementById("key-station-meta");
const keyEnabledSelect = keyForm.elements.enabled_select;
const keyEnabledHidden = keyForm.elements.enabled;
const keyModal = createModal(document.getElementById("key-modal"));

const bindingDetailRoot = document.getElementById("binding-detail-modal");
const bindingDetailModal = createModal(bindingDetailRoot);
const bindingDetailTitle = document.getElementById("binding-detail-title");
const bindingDetailSubtitle = document.getElementById("binding-detail-subtitle");
const bindingDetailMeta = document.getElementById("binding-detail-meta");
const bindingDetailStats = document.getElementById("binding-detail-stats");
const bindingDetailError = document.getElementById("binding-detail-error");
const bindingDetailModels = document.getElementById("binding-detail-models");
const bindingDetailRefreshBtn = document.getElementById("binding-detail-refresh");

let stations = [];
let keys = [];
let bindings = [];
let activeJob = null;
let bindingDetailState = {
  bindingId: null,
  keyId: null,
  loading: false,
  detailData: null,
  pendingModelId: "",
};

const expandedStationIds = new Set();

function setStationFeedback(message, type = "") {
  if (!message) {
    stationFormFeedback.hidden = true;
    stationFormFeedback.textContent = "";
    stationFormFeedback.className = "form-feedback";
    return;
  }
  stationFormFeedback.hidden = false;
  stationFormFeedback.textContent = message;
  stationFormFeedback.className = `form-feedback ${type}`.trim();
}

function setKeyFeedback(message, type = "") {
  if (!message) {
    keyFormFeedback.hidden = true;
    keyFormFeedback.textContent = "";
    keyFormFeedback.className = "form-feedback";
    return;
  }
  keyFormFeedback.hidden = false;
  keyFormFeedback.textContent = message;
  keyFormFeedback.className = `form-feedback ${type}`.trim();
}

function resetStationForm() {
  stationForm.reset();
  stationForm.elements.id.value = "";
  stationForm.elements.network_mode.value = "auto";
  stationForm.elements.proxy_url.value = "";
  stationForm.elements.detect_max_concurrency.value = 2;
  stationForm.elements.detect_min_interval_ms.value = 800;
  stationForm.elements.detect_cooldown_seconds.value = 60;
  stationForm.elements.enabled.checked = true;
  setStationFeedback("");
}

function resetKeyForm(stationId = "") {
  keyForm.reset();
  keyForm.elements.id.value = "";
  keyForm.elements.station_id.value = stationId ? String(stationId) : "";
  keyForm.elements.network_mode.value = "";
  keyForm.elements.proxy_url.value = "";
  keyForm.elements.timeout_seconds.value = 30;
  keyEnabledSelect.value = "true";
  keyEnabledHidden.value = "true";
  setKeyFeedback("");
}

function findStation(stationId) {
  return stations.find((row) => row.id === Number(stationId));
}

function findKey(keyId) {
  return keys.find((row) => row.id === Number(keyId));
}

function findBinding(bindingId) {
  return bindings.find((row) => row.id === Number(bindingId));
}

function stationKeys(stationId) {
  return keys.filter((row) => row.station_id === stationId);
}

function keyBindings(keyId) {
  return bindings.filter((row) => row.key_id === keyId);
}

function stationStats(stationId) {
  const rows = stationKeys(stationId);
  return rows.reduce(
    (summary, row) => {
      summary.keyCount += 1;
      summary.enabledKeyCount += row.enabled ? 1 : 0;
      summary.supportedBindings += row.supported_binding_count || 0;
      summary.bindingCount += row.binding_count || 0;
      summary.availableModels += row.available_model_count || 0;
      return summary;
    },
    { keyCount: 0, enabledKeyCount: 0, supportedBindings: 0, bindingCount: 0, availableModels: 0 }
  );
}

function badgeToneForStatus(value) {
  if (value === "ok" || value === true) return "ok";
  if (value === "partial" || value === "supported") return "partial";
  if (value === "rate_limited") return "warn";
  if (value === "error" || value === false || value === "unsupported") return "error";
  return "neutral";
}

function statusBadge(label, tone = "neutral") {
  const displayLabel = label === "rate_limited" ? "限流" : label;
  return `<span class="status-badge ${tone}">${escapeHtml(displayLabel)}</span>`;
}

function availabilityBadge(value) {
  if (value == null) {
    return statusBadge("未检查", "neutral");
  }
  return statusBadge(value ? "可用" : "不可用", value ? "ok" : "error");
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatLatency(value) {
  return value ? `${value} ms` : "-";
}

function jobProgress(job) {
  const total = Number(job?.total_steps || 0);
  const completed = Number(job?.completed_steps || 0);
  if (!total) return job?.status === "ok" ? 100 : 0;
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

function renderInlineJob(job, keyId) {
  if (!job || Number(job.scope_id) !== Number(keyId)) {
    return "";
  }
  const percent = jobProgress(job);
  const statusTone = badgeToneForStatus(job.status);
  return `
    <section class="key-job-inline">
      <div class="key-job-inline-head">
        <strong>${escapeHtml(job.status === "ok" ? "任务完成" : "正在执行")}</strong>
        ${statusBadge(job.status || "idle", statusTone)}
      </div>
      <div class="key-job-inline-copy">
        <p>${escapeHtml(job.current_step || job.title || "等待任务开始…")}</p>
      </div>
      <div class="key-job-inline-head">
        <span>${percent}%</span>
        <span>${job.completed_steps || 0} / ${job.total_steps || 0}</span>
      </div>
      <div class="key-job-inline-bar"><div style="width:${percent}%"></div></div>
    </section>
  `;
}

function renderBindingPill(binding) {
  const tone = badgeToneForStatus(binding.status);
  const availability = binding.available_model_count || 0;
  return `
    <button
      type="button"
      class="protocol-pill ${tone}"
      data-action="open-binding-detail"
      data-binding-id="${binding.id}"
      aria-label="查看 ${escapeHtml(binding.label)} 模型详情"
    >
      <strong>${escapeHtml(binding.label)}</strong>
      <span>${binding.supported ? "支持" : "未通过"}</span>
      <span>${availability}/${binding.model_count || 0}</span>
    </button>
  `;
}

function renderKeyCard(key) {
  const rows = keyBindings(key.id);
  const protocolMarkup = rows.length
    ? rows.map(renderBindingPill).join("")
    : `<span class="chip">尚未产出协议结果</span>`;
  return `
    <article class="key-card">
      <div class="key-card-top">
        <div>
          <div class="key-card-title">
            <h4>${escapeHtml(key.name)}</h4>
            ${statusBadge(key.enabled ? "启用" : "停用", key.enabled ? "ok" : "neutral")}
          </div>
          <p class="subline">
            分组 ${escapeHtml(key.group_name || "-")} · 网络 ${escapeHtml(key.effective_network_mode || "-")}
            ${key.effective_proxy_url_masked ? ` · <code>${escapeHtml(key.effective_proxy_url_masked)}</code>` : ""}
          </p>
          <p class="subline">
            <code class="copyable-key" data-copy-text="${escapeHtml(key.api_key || key.api_key_masked || "")}" title="点击复制">${escapeHtml(key.api_key_masked || "-")}</code> · 协议 ${key.supported_binding_count || 0}/${key.binding_count || 0}
            · 可用模型 ${key.available_model_count || 0}
          </p>
        </div>
        <div class="key-card-side">
          ${renderInlineJob(activeJob, key.id)}
          <div class="actions">
            <button type="button" class="button small" data-action="detect-key" data-key-id="${key.id}">探测协议</button>
            <button type="button" class="button small" data-action="audit-key" data-key-id="${key.id}">全量校验</button>
            <button type="button" class="button small" data-action="force-audit-key" data-key-id="${key.id}">强制校验</button>
            <button type="button" class="button small" data-action="edit-key" data-key-id="${key.id}">编辑</button>
            <button type="button" class="button small danger" data-action="delete-key" data-key-id="${key.id}">删除</button>
          </div>
        </div>
      </div>
      <div class="chip-row">${protocolMarkup}</div>
    </article>
  `;
}

function renderStationCard(station) {
  const stats = stationStats(station.id);
  const expanded = expandedStationIds.has(station.id);
  const rows = stationKeys(station.id);
  const keyMarkup = rows.length
    ? `<div class="key-list">${rows.map(renderKeyCard).join("")}</div>`
    : `
      <div class="empty-state">
        <p>这个站点下还没有 Key。</p>
        <button type="button" class="button primary" data-action="create-key" data-station-id="${station.id}">新增第一个 Key</button>
      </div>
    `;

  const metaParts = [
    `网络 ${escapeHtml(station.network_mode || "auto")}`,
    station.proxy_url_masked ? `代理 <code>${escapeHtml(station.proxy_url_masked)}</code>` : null,
    `并发 ${station.detect_max_concurrency ?? 2} · 间隔 ${station.detect_min_interval_ms ?? 800}ms · 冷却 ${station.detect_cooldown_seconds ?? 60}s`,
    station.notes ? escapeHtml(station.notes) : null,
  ].filter(Boolean);

  return `
    <article class="station-card">
      <div class="station-card-top">
        <div class="station-card-copy">
          <div class="station-card-header">
            <div class="station-card-title-row">
              <h2>${escapeHtml(station.name)}</h2>
              ${statusBadge(station.enabled ? "启用" : "停用", station.enabled ? "ok" : "neutral")}
            </div>
            <p class="station-card-url"><code>${escapeHtml(station.base_url)}</code></p>
          </div>
          ${metaParts.length ? `<div class="station-card-meta">${metaParts.map((p) => `<span>${p}</span>`).join('<span style="opacity:.3">|</span>')}</div>` : ""}
        </div>
        <div class="station-card-stats">
          <div class="station-stat">
            <span class="station-stat-value">${stats.keyCount}</span>
            <span class="station-stat-label">Key</span>
          </div>
          <div class="station-stat">
            <span class="station-stat-value">${stats.supportedBindings}/${stats.bindingCount}</span>
            <span class="station-stat-label">协议</span>
          </div>
          <div class="station-stat">
            <span class="station-stat-value">${stats.availableModels}</span>
            <span class="station-stat-label">模型</span>
          </div>
        </div>
        <div class="station-card-actions">
          <button type="button" class="button small${expanded ? " primary" : ""}" data-action="toggle-keys" data-station-id="${station.id}">
            ${expanded ? "收起" : "Keys"}
          </button>
          <button type="button" class="button small" data-action="create-key" data-station-id="${station.id}">+Key</button>
          <button type="button" class="button small" data-action="edit-station" data-station-id="${station.id}">编辑</button>
          <button type="button" class="button small danger" data-action="delete-station" data-station-id="${station.id}">删除</button>
        </div>
      </div>

      ${
        expanded
          ? `
            <div class="station-card-body">
              <div class="section-intro">
                <div>
                  <h3>${escapeHtml(station.name)} 的 Keys</h3>
                  <p>当前 ${stats.enabledKeyCount}/${stats.keyCount} 个 Key 处于启用状态。</p>
                </div>
                <div class="actions">
                  <button type="button" class="button small" data-action="create-key" data-station-id="${station.id}">新增 Key</button>
                  <a href="/models?station_id=${station.id}" class="button small">查看模型</a>
                </div>
              </div>
              ${keyMarkup}
            </div>
          `
          : ""
      }
    </article>
  `;
}

function renderStations() {
  const query = stationSearch.value.trim().toLowerCase();
  const enabledOnly = stationEnabledOnly.checked;
  const filtered = stations.filter((station) => {
    if (enabledOnly && !station.enabled) return false;
    if (!query) return true;
    return [station.name, station.base_url].some((value) => String(value || "").toLowerCase().includes(query));
  });

  stationsList.innerHTML = filtered.length
    ? filtered.map(renderStationCard).join("")
    : `
      <div class="empty-state">
        <p>没有匹配的站点。</p>
        <button type="button" class="button primary" id="empty-create-station-btn">新增站点</button>
      </div>
    `;

  const emptyCreateStationBtn = document.getElementById("empty-create-station-btn");
  if (emptyCreateStationBtn) {
    emptyCreateStationBtn.addEventListener("click", openCreateStationModal);
  }
}

async function refreshAll() {
  const [stationRows, keyRows, bindingRows] = await Promise.all([
    request("/api/stations"),
    request("/api/keys"),
    request("/api/bindings"),
  ]);
  stations = stationRows;
  keys = keyRows;
  bindings = bindingRows;
  renderStations();
}

function validateProxyUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (!text.startsWith("http://") && !text.startsWith("https://") && !text.startsWith("socks5://") && !text.startsWith("socks5h://")) {
    return "代理地址必须以 http://、https://、socks5:// 或 socks5h:// 开头。";
  }
  return "";
}

function validateKeyPayload(payload, isEdit) {
  if (!payload.station_id) {
    return "缺少所属站点。";
  }
  if (!String(payload.name || "").trim()) {
    return "名称不能为空。";
  }
  if (!isEdit && !String(payload.api_key || "").trim()) {
    return "新增 Key 时必须填写 API Key。";
  }
  return validateProxyUrl(payload.proxy_url);
}

function openCreateStationModal() {
  resetStationForm();
  stationModal.open({
    title: "新增站点",
    subtitle: "录入一个中转站的基础信息和网络策略。",
  });
}

function openEditStationModal(station) {
  resetStationForm();
  stationForm.elements.id.value = station.id;
  stationForm.elements.name.value = station.name;
  stationForm.elements.base_url.value = station.base_url;
  stationForm.elements.network_mode.value = station.network_mode || "auto";
  stationForm.elements.proxy_url.value = station.proxy_url || "";
  stationForm.elements.detect_max_concurrency.value = station.detect_max_concurrency ?? 2;
  stationForm.elements.detect_min_interval_ms.value = station.detect_min_interval_ms ?? 800;
  stationForm.elements.detect_cooldown_seconds.value = station.detect_cooldown_seconds ?? 60;
  stationForm.elements.notes.value = station.notes || "";
  stationForm.elements.enabled.checked = Boolean(station.enabled);
  stationModal.open({
    title: `编辑站点 #${station.id}`,
    subtitle: "更新站点地址、网络策略和备注。",
  });
}

function writeKeyStationMeta(station) {
  keyStationMeta.innerHTML = `
    <strong>${escapeHtml(station.name)}</strong>
    <div>${escapeHtml(station.base_url)}</div>
    <div>默认网络 ${escapeHtml(station.network_mode || "auto")}${station.proxy_url_masked ? ` · <code>${escapeHtml(station.proxy_url_masked)}</code>` : ""}</div>
  `;
}

function openCreateKeyModal(station) {
  resetKeyForm(station.id);
  writeKeyStationMeta(station);
  keyModal.open({
    title: `为 ${station.name} 新增 Key`,
    subtitle: "保存后会自动进入后台全量校验。",
  });
}

function openEditKeyModal(key) {
  const station = findStation(key.station_id);
  if (!station) return;
  resetKeyForm(station.id);
  writeKeyStationMeta(station);
  keyForm.elements.id.value = key.id;
  keyForm.elements.name.value = key.name;
  keyForm.elements.api_key.value = "";
  keyForm.elements.group_name.value = key.group_name || "";
  keyForm.elements.network_mode.value = key.network_mode || "";
  keyForm.elements.proxy_url.value = key.proxy_url || "";
  keyForm.elements.seed_models.value = (key.seed_models_list || []).join("\n");
  keyForm.elements.timeout_seconds.value = key.timeout_seconds || 30;
  keyForm.elements.notes.value = key.notes || "";
  keyEnabledSelect.value = key.enabled ? "true" : "false";
  keyEnabledHidden.value = key.enabled ? "true" : "false";
  keyModal.open({
    title: `编辑 Key #${key.id}`,
    subtitle: "保持所属站点不变，在当前站点下更新 Key 配置。",
  });
}

function renderBindingStats(summary) {
  const cards = [
    ["模型总数", summary.model_count],
    ["已检查", summary.checked_count],
    ["可用", summary.available_count],
    ["部分可用", summary.partial_count],
    ["空响应", summary.empty_count],
    ["错误", summary.error_count],
    ["限流", summary.rate_limited_count],
  ];
  return cards
    .map(
      ([label, value]) => `
        <article class="binding-stat-card">
          <span>${label}</span>
          <strong>${value ?? 0}</strong>
        </article>
      `
    )
    .join("");
}

function renderBindingDetail(data) {
  const { binding, models, summary } = data;
  bindingDetailState.detailData = data;
  bindingDetailState.bindingId = binding.id;
  bindingDetailState.keyId = binding.key_id;
  bindingDetailTitle.textContent = binding.label;
  bindingDetailSubtitle.textContent = `${binding.station_name} / ${binding.key_name} · ${binding.adapter_type}`;
  bindingDetailMeta.innerHTML = `
    ${statusBadge(binding.status || "unknown", badgeToneForStatus(binding.status))}
    ${statusBadge(binding.supported ? "协议通过" : "协议未通过", binding.supported ? "ok" : "error")}
    <span class="chip">探针 ${escapeHtml(binding.probe_model || "-")}</span>
    <span class="chip">最近探测 ${escapeHtml(formatDate(binding.detected_at))}</span>
    <span class="chip">最近校验 ${escapeHtml(formatDate(binding.last_checked_at))}</span>
  `;
  bindingDetailStats.innerHTML = renderBindingStats(summary);
  if (binding.last_error) {
    bindingDetailError.hidden = false;
    bindingDetailError.textContent = binding.last_error;
  } else {
    bindingDetailError.hidden = true;
    bindingDetailError.textContent = "";
  }
  bindingDetailModels.innerHTML = models.length
    ? models
        .map(
          (row) => `
            <tr>
              <td>
                <div class="binding-model-id">
                  <strong>${escapeHtml(row.model_id)}</strong>
                  <span>来源 ${escapeHtml(row.source || "-")}</span>
                </div>
              </td>
              <td>${statusBadge(row.status || "unchecked", badgeToneForStatus(row.status))}</td>
              <td>${availabilityBadge(row.available == null ? null : Boolean(row.available))}</td>
              <td>${escapeHtml(formatLatency(row.latency_ms))}</td>
              <td>${escapeHtml(formatDate(row.checked_at))}</td>
              <td class="cell-preview">${escapeHtml(row.preview || "-")}</td>
              <td class="cell-error">${escapeHtml(row.error || "-")}</td>
              <td>
                <button
                  type="button"
                  class="button small"
                  data-action="check-single-model"
                  data-binding-id="${binding.id}"
                  data-model-id="${escapeHtml(row.model_id)}"
                  ${bindingDetailState.pendingModelId ? "disabled" : ""}
                >
                  ${bindingDetailState.pendingModelId === row.model_id ? "检测中…" : "检测此模型"}
                </button>
              </td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="8" class="binding-empty">这个协议暂时没有模型。</td></tr>`;
}

async function openBindingDetail(bindingId) {
  const binding = findBinding(bindingId);
  if (!binding) return;
  bindingDetailModal.open({
    title: binding.label,
    subtitle: `${binding.station_name} / ${binding.key_name}`,
  });
  bindingDetailMeta.innerHTML = `<span class="chip">正在加载模型详情…</span>`;
  bindingDetailStats.innerHTML = "";
  bindingDetailError.hidden = true;
  bindingDetailModels.innerHTML = `<tr><td colspan="8" class="binding-empty">正在加载…</td></tr>`;
  bindingDetailState.bindingId = bindingId;
  bindingDetailState.keyId = binding.key_id;
  bindingDetailState.detailData = null;
  bindingDetailState.pendingModelId = "";
  try {
    const detail = await request(`/api/bindings/${bindingId}/models`);
    renderBindingDetail(detail);
  } catch (error) {
    bindingDetailModels.innerHTML = `<tr><td colspan="8" class="binding-empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function refreshBindingDetailIfOpen() {
  if (bindingDetailRoot.hidden || !bindingDetailState.bindingId || bindingDetailState.loading) {
    return;
  }
  bindingDetailState.loading = true;
  try {
    const detail = await request(`/api/bindings/${bindingDetailState.bindingId}/models`);
    renderBindingDetail(detail);
  } catch (error) {
    log(pageLog, error.message);
  } finally {
    bindingDetailState.loading = false;
  }
}

function rerenderBindingDetailWithPendingState() {
  if (bindingDetailState.detailData) {
    renderBindingDetail(bindingDetailState.detailData);
  }
}

async function trackJob(job) {
  activeJob = job;
  setKeyFeedback(`任务 #${job.id} 已启动，当前页会持续显示进度。`, "success");
  log(pageLog, {
    message: `任务已启动 #${job.id}`,
    title: job.title,
    status: job.status,
    scope_type: job.scope_type,
    scope_id: job.scope_id,
  });
  await refreshAll();
  await refreshBindingDetailIfOpen();
  await pollJob(job.id, {
    onUpdate(currentJob) {
      activeJob = currentJob;
      renderStations();
      log(pageLog, {
        id: currentJob.id,
        title: currentJob.title,
        status: currentJob.status,
        progress_percent: currentJob.progress_percent,
        completed_steps: currentJob.completed_steps,
        total_steps: currentJob.total_steps,
        current_step: currentJob.current_step,
        detail: currentJob.detail,
      });
    },
    async onFinish(currentJob) {
      activeJob = currentJob;
      await refreshAll();
      await refreshBindingDetailIfOpen();
      setKeyFeedback(
        currentJob.status === "ok"
          ? `任务 #${currentJob.id} 已完成。`
          : `任务 #${currentJob.id} 执行失败：${currentJob.error_text || "unknown error"}`,
        currentJob.status === "ok" ? "success" : "error"
      );
      log(pageLog, currentJob);
    },
  });
}

stationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = formToObject(stationForm);
    const validationError = validateProxyUrl(payload.proxy_url);
    if (validationError) {
      setStationFeedback(validationError, "error");
      return;
    }
    const id = payload.id;
    delete payload.id;
    const result = await request(id ? `/api/stations/${id}` : "/api/stations", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    await refreshAll();
    setStationFeedback("站点已保存。", "success");
    log(pageLog, result);
    window.setTimeout(() => {
      stationModal.close();
      resetStationForm();
    }, 300);
  } catch (error) {
    setStationFeedback(error.message, "error");
    log(pageLog, error.message);
  }
});

keyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    keyEnabledHidden.value = keyEnabledSelect.value;
    const payload = formToObject(keyForm);
    delete payload.enabled_select;
    const id = payload.id;
    const isEdit = Boolean(id);
    const validationError = validateKeyPayload(payload, isEdit);
    if (validationError) {
      setKeyFeedback(validationError, "error");
      return;
    }
    delete payload.id;
    expandedStationIds.add(Number(payload.station_id));
    const result = await request(isEdit ? `/api/keys/${id}` : "/api/keys", {
      method: isEdit ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    await refreshAll();
    keyModal.close();
    resetKeyForm(payload.station_id);
    if (result.job) {
      log(pageLog, result);
      await trackJob(result.job);
    } else {
      log(pageLog, result);
    }
  } catch (error) {
    setKeyFeedback(error.message, "error");
    log(pageLog, error.message);
  }
});

stationsList.addEventListener("click", async (event) => {
  const copyNode = event.target.closest(".copyable-key");
  if (copyNode) {
    const text = copyNode.dataset.copyText;
    if (text) {
      try {
        await navigator.clipboard.writeText(text);
        const tip = document.createElement("span");
        tip.className = "copy-toast";
        tip.textContent = "已复制";
        copyNode.style.position = "relative";
        copyNode.appendChild(tip);
        setTimeout(() => tip.remove(), 1200);
      } catch {
        log(pageLog, "复制失败，请手动复制。");
      }
    }
    return;
  }

  const actionNode = event.target.closest("[data-action]");
  if (!actionNode) return;

  const action = actionNode.dataset.action;
  const stationId = Number(actionNode.dataset.stationId);
  const keyId = Number(actionNode.dataset.keyId);
  const bindingId = Number(actionNode.dataset.bindingId);

  try {
    if (action === "toggle-keys") {
      if (expandedStationIds.has(stationId)) {
        expandedStationIds.delete(stationId);
      } else {
        expandedStationIds.add(stationId);
      }
      renderStations();
      return;
    }

    if (action === "edit-station") {
      const station = findStation(stationId);
      if (station) openEditStationModal(station);
      return;
    }

    if (action === "delete-station") {
      const station = findStation(stationId);
      if (!station) return;
      if (!window.confirm(`删除中转站 ${station.name}？关联 Key 也会一起删除。`)) return;
      const result = await request(`/api/stations/${stationId}`, { method: "DELETE" });
      expandedStationIds.delete(stationId);
      await refreshAll();
      log(pageLog, result);
      return;
    }

    if (action === "create-key") {
      const station = findStation(stationId);
      if (station) openCreateKeyModal(station);
      return;
    }

    if (action === "open-binding-detail") {
      await openBindingDetail(bindingId);
      return;
    }

    const key = findKey(keyId);
    if (!key) return;
    expandedStationIds.add(key.station_id);

    if (action === "edit-key") {
      openEditKeyModal(key);
      return;
    }
    if (action === "delete-key") {
      if (!window.confirm(`删除 Key ${key.name}？`)) return;
      const result = await request(`/api/keys/${keyId}`, { method: "DELETE" });
      await refreshAll();
      log(pageLog, result);
      return;
    }
    if (action === "detect-key") {
      const job = await request(`/api/keys/${keyId}/detect`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await trackJob(job);
      return;
    }
    if (action === "audit-key") {
      const job = await request(`/api/keys/${keyId}/audit`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await trackJob(job);
      return;
    }
    if (action === "force-audit-key") {
      const job = await request(`/api/keys/${keyId}/force-audit`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await trackJob(job);
    }
  } catch (error) {
    log(pageLog, error.message);
  }
});

bindingDetailRefreshBtn.addEventListener("click", async () => {
  if (!bindingDetailState.bindingId) return;
  await refreshBindingDetailIfOpen();
});

bindingDetailModels.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action='check-single-model']");
  if (!button) return;
  if (bindingDetailState.pendingModelId) return;

  const bindingId = Number(button.dataset.bindingId);
  const modelId = String(button.dataset.modelId || "").trim();
  if (!bindingId || !modelId) return;

  try {
    bindingDetailState.pendingModelId = modelId;
    rerenderBindingDetailWithPendingState();
    const job = await request(`/api/bindings/${bindingId}/check`, {
      method: "POST",
      body: JSON.stringify({ model_id: modelId }),
    });
    await trackJob(job);
  } catch (error) {
    log(pageLog, error.message);
  } finally {
    bindingDetailState.pendingModelId = "";
    rerenderBindingDetailWithPendingState();
    await refreshBindingDetailIfOpen();
  }
});

createStationBtn.addEventListener("click", openCreateStationModal);
stationSearch.addEventListener("input", renderStations);
stationEnabledOnly.addEventListener("change", renderStations);
keyEnabledSelect.addEventListener("change", () => {
  keyEnabledHidden.value = keyEnabledSelect.value;
});

resetStationForm();
resetKeyForm();
activeJob = null;
refreshAll().catch((error) => log(pageLog, error.message));
