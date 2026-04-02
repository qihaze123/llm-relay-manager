const { request, log, renderSchedulerStatus } = window.RelayCommon;

const schedulerForm = document.getElementById("scheduler-form");
const schedulerStatus = document.getElementById("scheduler-status");
const jobsTable = document.getElementById("jobs-table");
const historyTable = document.getElementById("history-table");
const runCycleBtn = document.getElementById("run-cycle-btn");
const pageLog = document.getElementById("page-log");

let refreshTimer = 0;

function renderJobs(rows) {
  jobsTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${new Date(row.created_at).toLocaleString()}</td>
              <td>${row.id}</td>
              <td>${row.title}</td>
              <td>${row.status}</td>
              <td>${row.total_steps ? `${row.completed_steps}/${row.total_steps} (${row.progress_percent}%)` : "-"}</td>
              <td>${row.current_step || row.detail || "-"}</td>
              <td>${row.error_text || "-"}</td>
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
              <td><code>${row.model_id}</code></td>
              <td>${row.station_name}</td>
              <td>${row.key_name}</td>
              <td>${row.protocol_label}</td>
              <td>${row.status}</td>
              <td>${row.available ? "是" : "否"}</td>
              <td>${row.network_route || "-"}${row.proxy_url_masked ? ` · <code>${row.proxy_url_masked}</code>` : ""}</td>
              <td>${row.latency_ms ? `${row.latency_ms} ms` : "-"}</td>
              <td>${row.error || "-"}</td>
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
    refreshTimer = window.setTimeout(() => refresh().catch((error) => log(pageLog, error.message)), 2000);
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
    log(pageLog, result);
  } catch (error) {
    log(pageLog, error.message);
  }
});

runCycleBtn.addEventListener("click", async () => {
  try {
    const result = await request("/api/run-cycle", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refresh();
    log(pageLog, result);
  } catch (error) {
    log(pageLog, error.message);
  }
});

refresh().catch((error) => log(pageLog, error.message));
