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

let stations = [];
let keys = [];
let bindings = [];
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

function statusBadge(label, tone = "neutral") {
  return `<span class="status-badge ${tone}">${escapeHtml(label)}</span>`;
}

function renderBindingPill(binding) {
  const tone = binding.status === "ok" ? "ok" : binding.status === "partial" ? "partial" : binding.status === "error" ? "error" : "";
  const availability = binding.available_model_count || 0;
  return `
    <span class="protocol-pill ${tone}">
      <strong>${escapeHtml(binding.label)}</strong>
      <span>${binding.supported ? "支持" : "未通过"}</span>
      <span>${availability}/${binding.model_count || 0}</span>
    </span>
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
            <code>${escapeHtml(key.api_key_masked || "-")}</code> · 协议 ${key.supported_binding_count || 0}/${key.binding_count || 0}
            · 可用模型 ${key.available_model_count || 0}
          </p>
        </div>
        <div class="actions">
          <button type="button" class="button small" data-action="detect-key" data-key-id="${key.id}">探测协议</button>
          <button type="button" class="button small" data-action="audit-key" data-key-id="${key.id}">全量校验</button>
          <button type="button" class="button small" data-action="force-audit-key" data-key-id="${key.id}">强制校验</button>
          <button type="button" class="button small" data-action="edit-key" data-key-id="${key.id}">编辑</button>
          <button type="button" class="button small danger" data-action="delete-key" data-key-id="${key.id}">删除</button>
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

  return `
    <article class="station-card">
      <div class="station-card-top">
        <div class="station-card-copy">
          <div class="station-card-title-row">
            <h2>${escapeHtml(station.name)}</h2>
            ${statusBadge(station.enabled ? "启用" : "停用", station.enabled ? "ok" : "neutral")}
          </div>
          <p class="station-card-url"><code>${escapeHtml(station.base_url)}</code></p>
        </div>
        <div class="metric-grid">
          <article class="metric-card">
            <span>Key</span>
            <strong>${stats.keyCount}</strong>
          </article>
          <article class="metric-card">
            <span>支持协议</span>
            <strong>${stats.supportedBindings}/${stats.bindingCount}</strong>
          </article>
          <article class="metric-card">
            <span>可用模型</span>
            <strong>${stats.availableModels}</strong>
          </article>
        </div>
      </div>

      <div class="detail-grid">
        <article class="detail-card">
          <span>网络策略</span>
          <p>${escapeHtml(station.network_mode || "auto")}</p>
        </article>
        <article class="detail-card">
          <span>代理地址</span>
          <p>${station.proxy_url_masked ? `<code>${escapeHtml(station.proxy_url_masked)}</code>` : "-"}</p>
        </article>
        <article class="detail-card">
          <span>备注</span>
          <p>${escapeHtml(station.notes || "无")}</p>
        </article>
      </div>

      <div class="station-card-actions">
        <button type="button" class="button primary" data-action="toggle-keys" data-station-id="${station.id}">
          ${expanded ? "收起 Keys" : "查看 Keys"}
        </button>
        <button type="button" class="button" data-action="create-key" data-station-id="${station.id}">新增 Key</button>
        <button type="button" class="button" data-action="edit-station" data-station-id="${station.id}">编辑站点</button>
        <button type="button" class="button danger" data-action="delete-station" data-station-id="${station.id}">删除站点</button>
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

async function trackJob(job) {
  setKeyFeedback(`任务已启动 #${job.id}，可在 History 页面查看进度。`, "success");
  log(pageLog, {
    message: `任务已启动 #${job.id}`,
    title: job.title,
    status: job.status,
  });
  await refreshAll();
  await pollJob(job.id, {
    onUpdate(currentJob) {
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
      await refreshAll();
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
  const target = event.target.closest("button[data-action]");
  if (!target) return;

  const action = target.dataset.action;
  const stationId = Number(target.dataset.stationId);
  const keyId = Number(target.dataset.keyId);

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

createStationBtn.addEventListener("click", openCreateStationModal);
stationSearch.addEventListener("input", renderStations);
stationEnabledOnly.addEventListener("change", renderStations);
keyEnabledSelect.addEventListener("change", () => {
  keyEnabledHidden.value = keyEnabledSelect.value;
});

resetStationForm();
resetKeyForm();
refreshAll().catch((error) => log(pageLog, error.message));
