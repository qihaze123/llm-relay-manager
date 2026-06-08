const { request, renderSchedulerStatus, escapeHtml } = window.RelayCommon;

const schedulerForm = document.getElementById("scheduler-form");
const schedulerStatus = document.getElementById("scheduler-status");
const jobsTable = document.getElementById("jobs-table");
const historyTable = document.getElementById("history-table");
const runCycleBtn = document.getElementById("run-cycle-btn");

let refreshTimer = 0;

function statusLabel(value) {
  const normalized = String(value ?? "").toLowerCase();
  const labels = {
    ok: "可用",
    supported: "支持",
    partial: "部分可用",
    empty: "空响应",
    unchecked: "未检查",
    error: "错误",
    unsupported: "不支持",
    rate_limited: "限流",
  };
  return labels[normalized] || value || "-";
}

function jobStatusLabel(value) {
  const normalized = String(value ?? "").toLowerCase();
  const labels = {
    queued: "排队中",
    running: "运行中",
    ok: "完成",
    error: "错误",
    interrupted: "已中断",
  };
  return labels[normalized] || value || "-";
}

function renderJobs(rows) {
  jobsTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${new Date(row.created_at).toLocaleString()}</td>
              <td>${row.id}</td>
              <td>${escapeHtml(row.title)}</td>
              <td>${escapeHtml(jobStatusLabel(row.status))}</td>
              <td>${row.total_steps ? `${row.completed_steps}/${row.total_steps} (${row.progress_percent}%)` : "-"}</td>
              <td>${escapeHtml(row.current_step || row.detail || "-")}</td>
              <td>${escapeHtml(row.error_text || "-")}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="7">暂无后台任务</td></tr>`;
}

function renderHistory(rows) {
  historyTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${new Date(row.checked_at).toLocaleString()}</td>
              <td><code>${escapeHtml(row.model_id)}</code></td>
              <td>${escapeHtml(row.station_name)}</td>
              <td>${escapeHtml(row.key_name)}</td>
              <td>${escapeHtml(row.protocol_label)}</td>
              <td>${escapeHtml(statusLabel(row.status))}</td>
              <td>${row.available ? "是" : "否"}</td>
              <td>${escapeHtml(row.network_route || "-")}${row.proxy_url_masked ? ` · <code>${escapeHtml(row.proxy_url_masked)}</code>` : ""}</td>
              <td>${row.latency_ms ? `${row.latency_ms} ms` : "-"}</td>
              <td>${escapeHtml(row.error || "-")}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="10">暂无历史</td></tr>`;
}

async function refresh() {
  const [scheduler, jobs, history] = await Promise.all([
    request("/api/settings/scheduler"),
    request("/api/jobs?limit=30"),
    request("/api/history?limit=100"),
  ]);
  schedulerForm.elements.enabled.checked = Boolean(scheduler.enabled);
  schedulerForm.elements.interval_minutes.value = scheduler.interval_minutes || 60;
  renderSchedulerStatus(schedulerStatus, scheduler);
  renderJobs(jobs);
  renderHistory(history);
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
  }
  if (jobs.some((job) => ["queued", "running"].includes(job.status))) {
    refreshTimer = window.setTimeout(() => refresh().catch((error) => console.error(error)), 2000);
  }
}

schedulerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = {
      enabled: schedulerForm.elements.enabled.checked,
      interval_minutes: schedulerForm.elements.interval_minutes.value,
    };
    const result = await request("/api/settings/scheduler", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    await refresh();
    ;
  } catch (error) {
    console.error(error);
  }
});

runCycleBtn.addEventListener("click", async () => {
  try {
    const result = await request("/api/run-cycle", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refresh();
    ;
  } catch (error) {
    console.error(error);
  }
});

refresh().catch((error) => console.error(error));
