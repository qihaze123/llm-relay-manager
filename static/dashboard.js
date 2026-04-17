const { request, renderSummary, renderSchedulerStatus } = window.RelayCommon;

const summaryGrid = document.getElementById("summary-grid");
const historyTable = document.getElementById("history-table");
const schedulerStatus = document.getElementById("scheduler-status");
const runCycleBtn = document.getElementById("run-cycle-btn");
let refreshTimer = 0;

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
              <td>${row.latency_ms ? `${row.latency_ms} ms` : "-"}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="8">暂无历史</td></tr>`;
}

async function refresh() {
  const [summary, history] = await Promise.all([request("/api/summary"), request("/api/history?limit=12")]);
  renderSummary(summaryGrid, summary);
  renderSchedulerStatus(schedulerStatus, summary.scheduler);
  renderHistory(history);
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
  }
  if (summary.active_job_count > 0) {
    refreshTimer = window.setTimeout(() => refresh().catch(console.error), 2000);
  }
}

runCycleBtn.addEventListener("click", async () => {
  try {
    await request("/api/run-cycle", { method: "POST", body: JSON.stringify({}) });
    await refresh();
  } catch (error) {
    console.error(error);
  }
});

refresh().catch(console.error);
