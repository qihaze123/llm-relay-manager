const { request, escapeHtml } = window.RelayCommon;

const summaryGrid = document.getElementById("summary-grid");
const healthBadge = document.getElementById("health-badge");
const issueList = document.getElementById("issue-list");
const stationOverview = document.getElementById("station-overview");
const historyTable = document.getElementById("history-table");
const runCycleBtn = document.getElementById("run-cycle-btn");

let refreshTimer = 0;

function statusBadge(label, tone = "neutral") {
  return `<span class="status-badge ${tone}">${escapeHtml(label)}</span>`;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function modelSearchUrl(params) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value != null && value !== "") q.set(key, value);
  });
  return `/models?${q.toString()}`;
}

function stationStats(station, keys, bindings) {
  const stationKeys = keys.filter((key) => key.station_id === station.id);
  const keyIds = new Set(stationKeys.map((key) => key.id));
  const stationBindings = bindings.filter((binding) => keyIds.has(binding.key_id));
  return {
    keyCount: stationKeys.length,
    enabledKeyCount: stationKeys.filter((key) => key.enabled).length,
    bindingCount: stationBindings.length,
    supportedBindings: stationBindings.filter((binding) => binding.supported).length,
    availableModels: stationBindings.reduce((sum, binding) => sum + Number(binding.available_model_count || 0), 0),
  };
}

function buildIssues({ stations, keys, bindings, jobs }) {
  const issues = [];
  jobs
    .filter((job) => ["queued", "running"].includes(job.status))
    .slice(0, 3)
    .forEach((job) => {
      issues.push({
        tone: "partial",
        title: job.title || `任务 #${job.id}`,
        meta: job.current_step || `${job.completed_steps || 0}/${job.total_steps || 0}`,
        href: "/history",
      });
    });

  stations
    .filter((station) => station.enabled && Number(station.key_count || 0) === 0)
    .slice(0, 4)
    .forEach((station) => {
      issues.push({
        tone: "warn",
        title: `${station.name} 没有 Key`,
        meta: station.base_url,
        href: "/stations",
      });
    });

  keys
    .filter((key) => key.enabled && Number(key.binding_count || 0) === 0)
    .slice(0, 4)
    .forEach((key) => {
      issues.push({
        tone: "warn",
        title: `${key.name} 没有协议结果`,
        meta: key.station_name,
        href: "/stations",
      });
    });

  bindings
    .filter((binding) => binding.status === "unsupported" || binding.status === "error" || binding.last_error)
    .slice(0, 8)
    .forEach((binding) => {
      issues.push({
        tone: "error",
        title: `${binding.key_name} · ${binding.label}`,
        meta: binding.last_error || binding.status,
        href: modelSearchUrl({
          station_id: binding.station_id,
          key_id: binding.key_id,
          protocol_label: binding.label,
          available: "0",
        }),
      });
    });

  bindings
    .filter((binding) => binding.supported && Number(binding.model_count || 0) > 0 && Number(binding.available_model_count || 0) === 0)
    .slice(0, 8)
    .forEach((binding) => {
      issues.push({
        tone: "warn",
        title: `${binding.key_name} · ${binding.label} 暂无可用模型`,
        meta: `${binding.checked_model_count || 0}/${binding.model_count || 0} 已检查`,
        href: modelSearchUrl({
          station_id: binding.station_id,
          key_id: binding.key_id,
          protocol_label: binding.label,
          available: "0",
        }),
      });
    });

  const seen = new Set();
  return issues.filter((issue) => {
    const key = `${issue.title}|${issue.meta}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 6);
}

function renderSummary(summary, issueCount) {
  const cards = [
    ["站点", summary.station_count],
    ["Key", summary.key_count],
    ["协议通过", `${summary.supported_binding_count}/${summary.binding_count}`],
    ["模型", summary.model_count],
    ["可用", summary.available_count],
    ["待处理", issueCount],
  ];
  summaryGrid.innerHTML = cards
    .map(
      ([label, value]) => `
        <div class="stat-tile">
          <span class="stat-tile-label">${escapeHtml(label)}</span>
          <strong class="stat-tile-value">${escapeHtml(value ?? 0)}</strong>
        </div>
      `
    )
    .join("");
}

function renderHealth(summary, issues) {
  if (summary.active_job_count > 0) {
    healthBadge.innerHTML = statusBadge(`${summary.active_job_count} 个任务运行中`, "partial");
    return;
  }
  if (!summary.available_count) {
    healthBadge.innerHTML = statusBadge("暂无可用模型", "error");
    return;
  }
  if (issues.length) {
    healthBadge.innerHTML = statusBadge(`${issues.length} 项待处理`, "warn");
    return;
  }
  healthBadge.innerHTML = statusBadge("正常", "ok");
}

function renderIssues(issues) {
  issueList.innerHTML = issues.length
    ? issues
        .map(
          (issue) => `
            <a class="issue-item" href="${escapeHtml(issue.href)}">
              ${statusBadge(issue.tone === "error" ? "处理" : "关注", issue.tone)}
              <strong>${escapeHtml(issue.title)}</strong>
              <span>${escapeHtml(issue.meta || "-")}</span>
            </a>
          `
        )
        .join("")
    : `
      <div class="empty-slim">
        ${statusBadge("正常", "ok")}
        <strong>没有需要立即处理的事项</strong>
      </div>
    `;
}

function renderStations(stations, keys, bindings) {
  const rows = stations
    .map((station) => ({ station, stats: stationStats(station, keys, bindings) }))
    .sort((a, b) => b.stats.availableModels - a.stats.availableModels || b.stats.keyCount - a.stats.keyCount)
    .slice(0, 8);
  stationOverview.innerHTML = rows.length
    ? rows
        .map(({ station, stats }) => {
          const tone = !station.enabled ? "neutral" : stats.availableModels > 0 ? "ok" : "warn";
          const label = !station.enabled ? "停用" : stats.availableModels > 0 ? "可用" : "待检查";
          return `
            <a class="station-overview-row" href="/stations">
              <div>
                <strong>${escapeHtml(station.name)}</strong>
                <span>${escapeHtml(station.base_url)}</span>
              </div>
              <div class="station-overview-meta">
                ${statusBadge(label, tone)}
                <span>${stats.enabledKeyCount}/${stats.keyCount} Key</span>
                <span>${stats.supportedBindings}/${stats.bindingCount} 协议</span>
                <span>${stats.availableModels} 模型</span>
              </div>
            </a>
          `;
        })
        .join("")
    : `<div class="empty-slim"><strong>还没有站点</strong><a href="/stations">新增站点</a></div>`;
}

function renderHistory(rows) {
  historyTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${escapeHtml(formatDate(row.checked_at))}</td>
              <td><code>${escapeHtml(row.model_id)}</code></td>
              <td>${escapeHtml(row.station_name)}</td>
              <td>${escapeHtml(row.key_name)}</td>
              <td>${escapeHtml(row.protocol_label)}</td>
              <td>${escapeHtml(row.status)}</td>
              <td>${row.available ? "是" : "否"}</td>
              <td>${row.latency_ms ? `${escapeHtml(row.latency_ms)} ms` : "-"}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="8">暂无历史</td></tr>`;
}

async function refresh() {
  const [summary, stations, keys, bindings, jobs, history] = await Promise.all([
    request("/api/summary"),
    request("/api/stations"),
    request("/api/keys"),
    request("/api/bindings"),
    request("/api/jobs?limit=10"),
    request("/api/history?limit=8"),
  ]);
  const issues = buildIssues({ stations, keys, bindings, jobs });
  renderSummary(summary, issues.length);
  renderHealth(summary, issues);
  renderIssues(issues);
  renderStations(stations, keys, bindings);
  renderHistory(history);
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
  }
  if (summary.active_job_count > 0 || jobs.some((job) => ["queued", "running"].includes(job.status))) {
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
