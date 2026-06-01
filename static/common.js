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
      ["站点", summary.station_count],
      ["Key", summary.key_count],
      ["协议", summary.binding_count],
      ["支持", summary.supported_binding_count],
      ["模型", summary.model_count],
      ["可用", summary.available_count],
      ["检查", summary.checked_count],
      ["任务", summary.active_job_count],
      ["历史", summary.history_count],
    ];
    grid.innerHTML = cards
      .map(
        ([label, value]) => `
          <div class="stat-tile">
            <span class="stat-tile-label">${label}</span>
            <strong class="stat-tile-value">${value ?? 0}</strong>
          </div>
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
    const inline = element.classList.contains("inline");
    const items = [
      [`启用`, scheduler.enabled ? `<span class="status-badge ok">是</span>` : `<span class="status-badge neutral">否</span>`],
      [`间隔`, `${scheduler.interval_minutes} 分钟`],
      [`状态`, scheduler.last_cycle_status || "-"],
      [`上次开始`, scheduler.last_cycle_started_at || "-"],
      [`上次结束`, scheduler.last_cycle_finished_at || "-"],
    ];
    if (scheduler.last_cycle_note) items.push([`备注`, scheduler.last_cycle_note]);
    element.innerHTML = items
      .map(([k, v]) => `<div class="sched-item"><span class="sched-k">${k}</span><span class="sched-v">${v}</span></div>`)
      .join(inline ? "" : "");
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
      if (hints[key]) node.setAttribute("data-tip", hints[key]);
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

(function installHoverTip() {
  let tipEl = null;
  const ensure = () => {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.className = "hover-tip";
      tipEl.hidden = true;
      document.body.appendChild(tipEl);
    }
    return tipEl;
  };
  const show = (target) => {
    const text = target.getAttribute("data-tip");
    if (!text) return;
    const el = ensure();
    el.textContent = text;
    el.hidden = false;
    const rect = target.getBoundingClientRect();
    const tipRect = el.getBoundingClientRect();
    const margin = 8;
    const placeRight = target.closest(".sidebar") !== null;
    let top;
    let left;
    if (placeRight) {
      top = rect.top + rect.height / 2 - tipRect.height / 2;
      top = Math.max(margin, Math.min(top, window.innerHeight - tipRect.height - margin));
      left = rect.right + 8;
      if (left + tipRect.width + margin > window.innerWidth) {
        left = Math.max(margin, rect.left - tipRect.width - 8);
      }
    } else {
      top = rect.bottom + 6;
      if (top + tipRect.height + margin > window.innerHeight) {
        top = Math.max(margin, rect.top - tipRect.height - 6);
      }
      left = rect.left;
      if (left + tipRect.width + margin > window.innerWidth) {
        left = Math.max(margin, window.innerWidth - tipRect.width - margin);
      }
    }
    el.style.top = `${top}px`;
    el.style.left = `${left}px`;
  };
  const hide = () => {
    if (tipEl) tipEl.hidden = true;
  };
  document.addEventListener("mouseover", (event) => {
    const el = event.target.closest("[data-tip]");
    if (el) show(el);
  });
  document.addEventListener("mouseout", (event) => {
    const el = event.target.closest("[data-tip]");
    if (el && !el.contains(event.relatedTarget)) hide();
  });
  window.addEventListener("scroll", hide, true);
})();
