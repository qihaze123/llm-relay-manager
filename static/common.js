window.RelayCommon = {
  escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  },

  async request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  },

  formToObject(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
      data[input.name] = input.checked;
    });
    return data;
  },

  log(element, payload) {
    element.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  },

  renderSummary(grid, summary) {
    const cards = [
      ["Stations", summary.station_count],
      ["Keys", summary.key_count],
      ["Protocols", summary.binding_count],
      ["Supported", summary.supported_binding_count],
      ["Models", summary.model_count],
      ["Available", summary.available_count],
      ["Checks", summary.checked_count],
      ["Active Jobs", summary.active_job_count],
      ["History", summary.history_count],
    ];
    grid.innerHTML = cards
      .map(
        ([label, value]) => `
          <article class="summary-card">
            <span>${label}</span>
            <strong>${value ?? 0}</strong>
          </article>
        `
      )
      .join("");
  },

  async pollJob(jobId, options = {}) {
    const intervalMs = options.intervalMs || 2000;
    while (true) {
      const job = await window.RelayCommon.request(`/api/jobs/${jobId}`);
      if (options.onUpdate) {
        options.onUpdate(job);
      }
      if (!["queued", "running"].includes(job.status)) {
        if (options.onFinish) {
          options.onFinish(job);
        }
        return job;
      }
      await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
    }
  },

  renderSchedulerStatus(element, scheduler) {
    element.innerHTML = `
      <div><strong>启用：</strong>${scheduler.enabled ? "是" : "否"}</div>
      <div><strong>间隔：</strong>${scheduler.interval_minutes} 分钟</div>
      <div><strong>上次开始：</strong>${scheduler.last_cycle_started_at || "-"}</div>
      <div><strong>上次结束：</strong>${scheduler.last_cycle_finished_at || "-"}</div>
      <div><strong>状态：</strong>${scheduler.last_cycle_status || "-"}</div>
      <div><strong>备注：</strong>${scheduler.last_cycle_note || "-"}</div>
    `;
  },

  activateNav() {
    const page = document.body.dataset.page;
    const hints = {
      dashboard: "总览：运行状态、概要指标、快速入口",
      stations: "站点：录入中转站，展开站点管理 Key",
      keys: "Keys：跨站点全局视角，批量探测/强制校验",
      models: "模型：按模型×站点×Key×协议 查询可用性",
      history: "历史：后台调度设置与巡检历史",
    };
    document.querySelectorAll("[data-nav]").forEach((node) => {
      const key = node.dataset.nav;
      if (hints[key]) node.setAttribute("title", hints[key]);
      if (key === page) node.classList.add("active");
    });
  },

  createModal(root) {
    if (!root) return null;
    const titleNode = root.querySelector("[data-modal-title]");
    const subtitleNode = root.querySelector("[data-modal-subtitle]");
    const close = () => {
      root.hidden = true;
      document.body.classList.remove("modal-open");
    };
    const open = ({ title = "", subtitle = "" } = {}) => {
      if (titleNode) titleNode.textContent = title;
      if (subtitleNode) subtitleNode.textContent = subtitle;
      root.hidden = false;
      document.body.classList.add("modal-open");
    };
    root.querySelectorAll("[data-modal-close]").forEach((node) => {
      node.addEventListener("click", close);
    });
    root.addEventListener("click", (event) => {
      if (event.target === root) {
        close();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !root.hidden) {
        close();
      }
    });
    return { open, close };
  },
};

window.RelayCommon.activateNav();
