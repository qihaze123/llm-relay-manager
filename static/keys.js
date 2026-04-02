const { request, formToObject, log, pollJob, escapeHtml, createModal } = window.RelayCommon;

const keyForm = document.getElementById("key-form");
const stationSelect = document.getElementById("station-select");
const keysTable = document.getElementById("keys-table");
const bindingsTable = document.getElementById("bindings-table");
const pageLog = document.getElementById("page-log");
const keyFormFeedback = document.getElementById("key-form-feedback");
const openKeyModalBtn = document.getElementById("open-key-modal-btn");
const stationFilter = document.getElementById("station-filter");
const keyEnabledFilter = document.getElementById("key-enabled-filter");
const keySearch = document.getElementById("key-search");
const keyEnabledSelect = keyForm.elements.enabled_select;
const keyEnabledHidden = keyForm.elements.enabled;
const keyModal = createModal(document.getElementById("key-modal"));

let stations = [];
let keys = [];
let bindings = [];

function setFormFeedback(message, type = "") {
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

function resetForm() {
  keyForm.reset();
  keyForm.elements.id.value = "";
  keyForm.elements.network_mode.value = "";
  keyForm.elements.proxy_url.value = "";
  keyForm.elements.timeout_seconds.value = 30;
  keyEnabledSelect.value = "true";
  keyEnabledHidden.value = "true";
}

function openCreateModal() {
  resetForm();
  keyModal.open({
    title: "新增 Key",
    subtitle: "录入 Key 后会自动进入后台检测和可用性校验。",
  });
}

function openEditModal(item) {
  resetForm();
  keyForm.elements.id.value = item.id;
  keyForm.elements.station_id.value = item.station_id;
  keyForm.elements.name.value = item.name;
  keyForm.elements.api_key.value = "";
  keyForm.elements.group_name.value = item.group_name || "";
  keyForm.elements.network_mode.value = item.network_mode || "";
  keyForm.elements.proxy_url.value = item.proxy_url || "";
  keyForm.elements.seed_models.value = (item.seed_models_list || []).join("\n");
  keyForm.elements.timeout_seconds.value = item.timeout_seconds || 30;
  keyForm.elements.notes.value = item.notes || "";
  keyEnabledSelect.value = item.enabled ? "true" : "false";
  keyEnabledHidden.value = item.enabled ? "true" : "false";
  keyModal.open({
    title: `编辑 Key #${item.id}`,
    subtitle: "在全局运维页更新 Key 的所属站点、网络策略和检测配置。",
  });
}

function renderStationOptions(rows) {
  stations = rows;
  const currentStationValue = stationSelect.value;
  const currentFilterValue = stationFilter.value;
  const options = rows.length
    ? rows.map((row) => `<option value="${row.id}">${escapeHtml(row.name)} · ${escapeHtml(row.base_url)}</option>`).join("")
    : `<option value="">先添加中转站</option>`;
  stationSelect.innerHTML = options;
  stationFilter.innerHTML = `<option value="">全部站点</option>${rows
    .map((row) => `<option value="${row.id}">${escapeHtml(row.name)}</option>`)
    .join("")}`;
  if (currentStationValue && rows.some((row) => String(row.id) === currentStationValue)) {
    stationSelect.value = currentStationValue;
  }
  if (currentFilterValue && rows.some((row) => String(row.id) === currentFilterValue)) {
    stationFilter.value = currentFilterValue;
  }
}

function filteredKeys() {
  const query = keySearch.value.trim().toLowerCase();
  return keys.filter((row) => {
    if (stationFilter.value && Number(stationFilter.value) !== row.station_id) return false;
    if (keyEnabledFilter.value) {
      const expected = keyEnabledFilter.value === "1";
      if (Boolean(row.enabled) !== expected) return false;
    }
    if (!query) return true;
    return [row.name, row.station_name, row.group_name]
      .some((value) => String(value || "").toLowerCase().includes(query));
  });
}

function renderKeys() {
  const rows = filteredKeys();
  keysTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${row.id}</td>
              <td>${escapeHtml(row.name)}</td>
              <td>${escapeHtml(row.station_name)}</td>
              <td>${escapeHtml(row.group_name || "-")}</td>
              <td>${row.enabled ? "是" : "否"}</td>
              <td>${escapeHtml(row.effective_network_mode || "-")}${row.effective_proxy_url_masked ? ` · <code>${escapeHtml(row.effective_proxy_url_masked)}</code>` : ""}</td>
              <td><code>${escapeHtml(row.api_key_masked || "-")}</code></td>
              <td>${row.supported_binding_count || 0}/${row.binding_count || 0}</td>
              <td>${row.available_model_count || 0}</td>
              <td class="actions">
                <button class="button small" data-entity="key" data-action="detect" data-id="${row.id}">探测协议</button>
                <button class="button small" data-entity="key" data-action="audit" data-id="${row.id}">全量校验</button>
                <button class="button small" data-entity="key" data-action="force-audit" data-id="${row.id}">强制全量校验</button>
                <button class="button small" data-entity="key" data-action="edit" data-id="${row.id}">编辑</button>
                <button class="button small danger" data-entity="key" data-action="delete" data-id="${row.id}">删除</button>
              </td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="10">暂无符合条件的 Key</td></tr>`;
}

function renderBindings(rows) {
  bindings = rows;
  bindingsTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${escapeHtml(row.station_name)}</td>
              <td>${escapeHtml(row.key_name)}</td>
              <td>${escapeHtml(row.label)}</td>
              <td>${row.supported ? "是" : "否"}</td>
              <td>${escapeHtml(row.status)}</td>
              <td>${row.model_count}</td>
              <td>${row.checked_model_count || 0}</td>
              <td>${row.available_model_count || 0}</td>
              <td>${row.history_check_count ? `${Math.round((row.history_success_count / row.history_check_count) * 100)}%` : "-"}</td>
              <td>${escapeHtml(row.last_network_route || "-")}${row.last_proxy_url_masked ? ` · <code>${escapeHtml(row.last_proxy_url_masked)}</code>` : ""}</td>
              <td><code>${escapeHtml(row.probe_model || "-")}</code></td>
              <td>${escapeHtml(row.last_error || "-")}</td>
              <td class="actions">
                <button class="button small" data-entity="binding" data-action="discover" data-id="${row.id}">刷新模型</button>
                <button class="button small" data-entity="binding" data-action="check" data-id="${row.id}">检查模型</button>
              </td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="13">还没有协议探测结果</td></tr>`;
}

function validateKeyPayload(payload, isEdit) {
  if (!payload.station_id) {
    return "请选择所属站点。";
  }
  if (!String(payload.name || "").trim()) {
    return "名称不能为空。";
  }
  if (!isEdit && !String(payload.api_key || "").trim()) {
    return "新增 Key 时必须填写 API Key。";
  }
  if (
    payload.proxy_url &&
    !String(payload.proxy_url).trim().startsWith("http://") &&
    !String(payload.proxy_url).trim().startsWith("https://") &&
    !String(payload.proxy_url).trim().startsWith("socks5://") &&
    !String(payload.proxy_url).trim().startsWith("socks5h://")
  ) {
    return "代理地址必须以 http://、https://、socks5:// 或 socks5h:// 开头。";
  }
  return "";
}

async function refreshAll() {
  const [stationRows, keyRows, bindingRows] = await Promise.all([
    request("/api/stations"),
    request("/api/keys"),
    request("/api/bindings"),
  ]);
  renderStationOptions(stationRows);
  keys = keyRows;
  renderKeys();
  renderBindings(bindingRows);
}

async function trackJob(job) {
  setFormFeedback(`任务已启动 #${job.id}，可在 History 页面查看进度。`, "success");
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
      setFormFeedback(
        currentJob.status === "ok"
          ? `任务 #${currentJob.id} 已完成。`
          : `任务 #${currentJob.id} 执行失败：${currentJob.error_text || "unknown error"}`,
        currentJob.status === "ok" ? "success" : "error"
      );
      log(pageLog, currentJob);
    },
  });
}

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
      setFormFeedback(validationError, "error");
      return;
    }
    delete payload.id;
    const result = await request(isEdit ? `/api/keys/${id}` : "/api/keys", {
      method: isEdit ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    await refreshAll();
    keyModal.close();
    resetForm();
    if (result.job) {
      setFormFeedback("Key 已创建，正在后台执行全量校验。", "success");
      await trackJob(result.job);
    } else {
      setFormFeedback("Key 已保存。", "success");
      log(pageLog, result);
    }
  } catch (error) {
    setFormFeedback(error.message, "error");
    log(pageLog, error.message);
  }
});

keysTable.addEventListener("click", async (event) => {
  const target = event.target.closest("button[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const id = Number(target.dataset.id);
  const item = keys.find((row) => row.id === id);
  if (!item) return;
  try {
    if (action === "edit") {
      openEditModal(item);
      return;
    }
    if (action === "delete") {
      if (!window.confirm(`删除 Key ${item.name}？`)) return;
      const result = await request(`/api/keys/${id}`, { method: "DELETE" });
      await refreshAll();
      log(pageLog, result);
      return;
    }
    if (action === "detect") {
      const job = await request(`/api/keys/${id}/detect`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await trackJob(job);
      return;
    }
    if (action === "audit") {
      const job = await request(`/api/keys/${id}/audit`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await trackJob(job);
      return;
    }
    if (action === "force-audit") {
      const job = await request(`/api/keys/${id}/force-audit`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await trackJob(job);
    }
  } catch (error) {
    log(pageLog, error.message);
  }
});

bindingsTable.addEventListener("click", async (event) => {
  const target = event.target.closest("button[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const id = Number(target.dataset.id);
  try {
    const endpoint = action === "discover" ? `/api/bindings/${id}/discover` : `/api/bindings/${id}/check`;
    const result = await request(endpoint, {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (action === "check") {
      await trackJob(result);
    } else {
      await refreshAll();
      log(pageLog, result);
    }
  } catch (error) {
    log(pageLog, error.message);
  }
});

openKeyModalBtn.addEventListener("click", openCreateModal);
stationFilter.addEventListener("change", renderKeys);
keyEnabledFilter.addEventListener("change", renderKeys);
keySearch.addEventListener("input", renderKeys);
keyEnabledSelect.addEventListener("change", () => {
  keyEnabledHidden.value = keyEnabledSelect.value;
});

resetForm();
refreshAll().catch((error) => log(pageLog, error.message));
