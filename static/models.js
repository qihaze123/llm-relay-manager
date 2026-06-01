const { request, log } = window.RelayCommon;

const searchForm = document.getElementById("search-form");
const detailTable = document.getElementById("models-detail-table");
const groupTable = document.getElementById("models-group-table");
const detailBody = document.getElementById("search-results");
const groupBody = document.getElementById("group-results");
const resetSearchBtn = document.getElementById("reset-search-btn");
const searchSummary = document.getElementById("models-search-summary");
const stationSelect = document.getElementById("models-station-select");
const keySelect = document.getElementById("models-key-select");
const protocolSelect = document.getElementById("models-protocol-select");
const pageSizeSelect = document.getElementById("models-page-size");
const pagination = document.getElementById("models-pagination");
const pageInfo = document.getElementById("models-page-info");
const prevBtn = pagination.querySelector('[data-page="prev"]');
const nextBtn = pagination.querySelector('[data-page="next"]');
const viewTabs = Array.from(document.querySelectorAll(".view-tab"));
const quickChips = Array.from(document.querySelectorAll(".quick-chip"));
const detailSortButtons = Array.from(detailTable.querySelectorAll("[data-sort]"));
const groupSortButtons = Array.from(groupTable.querySelectorAll("[data-group-sort]"));
const initialParams = new URLSearchParams(window.location.search);

const filterState = { stations: [], keys: [], bindings: [] };
let currentRows = [];
let viewMode = "detail";
let pageIndex = 0;
let pageSize = 50;
let groupSort = { field: "model_id", dir: "asc" };
const expandedModels = new Set();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function debounce(fn, wait = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

function statusBadgeClass(value, type = "status") {
  const normalized = String(value ?? "").toLowerCase();
  if (type === "boolean") {
    if (value == null) return "neutral";
    return value ? "ok" : "error";
  }
  if (normalized === "ok") return "ok";
  if (normalized === "partial") return "partial";
  if (normalized === "empty" || normalized === "unchecked") return "neutral";
  return "error";
}

function renderBadge(label, variant) {
  return `<span class="status-badge ${variant}">${escapeHtml(label)}</span>`;
}

function sortIndicator(field, activeField, activeDir) {
  if (field !== activeField) return "";
  return activeDir === "desc" ? " ↓" : " ↑";
}

function renderDetailSortLabels() {
  const activeField = searchForm.elements.sort_by.value || "model_id";
  const activeDir = searchForm.elements.sort_dir.value || "asc";
  detailSortButtons.forEach((btn) => {
    const base = btn.dataset.sortLabel || btn.textContent.replace(/[↑↓]/g, "").trim();
    btn.dataset.sortLabel = base;
    btn.textContent = `${base}${sortIndicator(btn.dataset.sort, activeField, activeDir)}`;
  });
}

function renderGroupSortLabels() {
  groupSortButtons.forEach((btn) => {
    const base = btn.dataset.sortLabel || btn.textContent.replace(/[↑↓]/g, "").trim();
    btn.dataset.sortLabel = base;
    btn.textContent = `${base}${sortIndicator(btn.dataset.groupSort, groupSort.field, groupSort.dir)}`;
  });
}

function formatRate(value) {
  if (value == null) return "-";
  const n = Number(value);
  return `${n.toFixed(Number.isInteger(n) ? 0 : 1)}%`;
}

function renderDetailRow(row) {
  const rate = formatRate(row.success_rate);
  const supportedLabel = row.supported ? "支持" : "不支持";
  const enabledLabel = row.model_enabled === 0 ? "禁用" : "启用";
  const statusLabel = row.status || "unchecked";
  const availableLabel = row.available == null ? "unchecked" : row.available ? "可用" : "不可用";
  return `
    <tr>
      <td><code class="clickable-cell" data-filter="q" data-value="${escapeHtml(row.model_id)}" title="点击按此模型筛选">${escapeHtml(row.model_id)}</code></td>
      <td><a href="#" class="clickable-cell" data-filter="station_id" data-value="${escapeHtml(row.station_id)}" title="点击按此站点筛选">${escapeHtml(row.station_name)}</a></td>
      <td><a href="#" class="clickable-cell" data-filter="key_id" data-value="${escapeHtml(row.key_id)}" title="点击按此 Key 筛选">${escapeHtml(row.key_name)}</a></td>
      <td><a href="#" class="clickable-cell" data-filter="protocol_label" data-value="${escapeHtml(row.protocol_label)}" title="点击按此协议筛选">${escapeHtml(row.protocol_label)}</a></td>
      <td>${renderBadge(enabledLabel, row.model_enabled === 0 ? "neutral" : "ok")}</td>
      <td>${renderBadge(supportedLabel, statusBadgeClass(row.supported, "boolean"))}</td>
      <td>${renderBadge(statusLabel, statusBadgeClass(statusLabel))}</td>
      <td>${renderBadge(availableLabel, statusBadgeClass(row.available, "boolean"))}</td>
      <td>${escapeHtml(rate)}</td>
      <td>${row.latency_ms ? `${escapeHtml(row.latency_ms)} ms` : "-"}</td>
      <td class="cell-preview" data-tip="${escapeHtml(row.preview || "")}">${escapeHtml(row.preview || "-")}</td>
      <td class="cell-error" data-tip="${escapeHtml(row.error || "")}">${escapeHtml(row.error || "-")}</td>
    </tr>
  `;
}

function groupRows(rows) {
  const map = new Map();
  rows.forEach((row) => {
    const id = row.model_id;
    if (!map.has(id)) {
      map.set(id, {
        model_id: id,
        entries: [],
        stations: new Set(),
        keys: new Set(),
        protocols: new Set(),
        available: 0,
        total: 0,
        best_rate: null,
        best_latency: null,
      });
    }
    const g = map.get(id);
    g.entries.push(row);
    g.total += 1;
    g.stations.add(row.station_id);
    g.keys.add(row.key_id);
    g.protocols.add(row.protocol_label);
    if (row.available) g.available += 1;
    const r = row.success_rate == null ? null : Number(row.success_rate);
    if (r != null && (g.best_rate == null || r > g.best_rate)) g.best_rate = r;
    const l = row.latency_ms == null ? null : Number(row.latency_ms);
    if (l != null && l > 0 && row.available && (g.best_latency == null || l < g.best_latency)) g.best_latency = l;
  });
  return Array.from(map.values()).map((g) => ({
    ...g,
    stations: g.stations.size,
    keys: g.keys.size,
    protocols: g.protocols.size,
  }));
}

function sortGroups(groups) {
  const { field, dir } = groupSort;
  const mul = dir === "desc" ? -1 : 1;
  return groups.slice().sort((a, b) => {
    const av = a[field];
    const bv = b[field];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "string") return av.localeCompare(bv) * mul;
    return (av - bv) * mul;
  });
}

function renderGroupRow(g) {
  const expanded = expandedModels.has(g.model_id);
  const availLabel = `${g.available}/${g.total}`;
  const availClass = g.available === 0 ? "error" : g.available === g.total ? "ok" : "partial";
  const rate = g.best_rate == null ? "-" : formatRate(g.best_rate);
  const latency = g.best_latency == null ? "-" : `${g.best_latency} ms`;
  const head = `
    <tr class="group-row" data-model="${escapeHtml(g.model_id)}">
      <td><button type="button" class="group-toggle" data-model="${escapeHtml(g.model_id)}">${expanded ? "▾" : "▸"} <code>${escapeHtml(g.model_id)}</code></button></td>
      <td>${g.total}</td>
      <td>${g.stations}</td>
      <td>${g.keys}</td>
      <td>${g.protocols}</td>
      <td>${renderBadge(availLabel, availClass)}</td>
      <td>${escapeHtml(rate)}</td>
      <td>${escapeHtml(latency)}</td>
    </tr>
  `;
  if (!expanded) return head;
  const inner = g.entries.map((row) => {
    const rate2 = formatRate(row.success_rate);
    const statusLabel = row.status || "unchecked";
    const availableLabel = row.available == null ? "unchecked" : row.available ? "可用" : "不可用";
    return `
      <tr class="group-detail-row">
        <td></td>
        <td colspan="7">
          <div class="group-detail-line">
            <a href="#" class="clickable-cell" data-filter="station_id" data-value="${escapeHtml(row.station_id)}">${escapeHtml(row.station_name)}</a>
            · <a href="#" class="clickable-cell" data-filter="key_id" data-value="${escapeHtml(row.key_id)}">${escapeHtml(row.key_name)}</a>
            · <a href="#" class="clickable-cell" data-filter="protocol_label" data-value="${escapeHtml(row.protocol_label)}">${escapeHtml(row.protocol_label)}</a>
            · ${renderBadge(statusLabel, statusBadgeClass(statusLabel))}
            · ${renderBadge(availableLabel, statusBadgeClass(row.available, "boolean"))}
            · 成功率 ${escapeHtml(rate2)}
            · 延迟 ${row.latency_ms ? `${row.latency_ms} ms` : "-"}
            ${row.error ? `· <span class="group-detail-error" data-tip="${escapeHtml(row.error)}">${escapeHtml(row.error)}</span>` : ""}
          </div>
        </td>
      </tr>
    `;
  }).join("");
  return head + inner;
}

function renderDetailView() {
  detailTable.hidden = false;
  groupTable.hidden = true;
  const size = pageSize > 0 ? pageSize : currentRows.length;
  const start = pageIndex * size;
  const slice = currentRows.slice(start, start + size);
  detailBody.innerHTML = slice.length
    ? slice.map(renderDetailRow).join("")
    : `<tr><td colspan="12">没有结果</td></tr>`;
  renderPagination(currentRows.length);
  renderDetailSortLabels();
}

function renderGroupView() {
  detailTable.hidden = true;
  groupTable.hidden = false;
  const groups = sortGroups(groupRows(currentRows));
  const size = pageSize > 0 ? pageSize : groups.length;
  const start = pageIndex * size;
  const slice = groups.slice(start, start + size);
  groupBody.innerHTML = slice.length
    ? slice.map(renderGroupRow).join("")
    : `<tr><td colspan="8">没有结果</td></tr>`;
  renderPagination(groups.length);
  renderGroupSortLabels();
}

function renderPagination(total) {
  if (pageSize === 0 || total <= pageSize) {
    pagination.hidden = true;
    return;
  }
  pagination.hidden = false;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pageIndex >= pages) pageIndex = pages - 1;
  pageInfo.textContent = `${pageIndex + 1} / ${pages} (共 ${total} 条)`;
  prevBtn.disabled = pageIndex === 0;
  nextBtn.disabled = pageIndex >= pages - 1;
}

function render() {
  if (viewMode === "group") renderGroupView();
  else renderDetailView();
}

function updateSummary(rows) {
  let ok = 0, error = 0, unchecked = 0;
  rows.forEach((r) => {
    if (r.available === 1 || r.available === true) ok += 1;
    else if (r.available == null) unchecked += 1;
    else error += 1;
  });
  searchSummary.innerHTML = `
    <span>共 <strong>${rows.length}</strong> 条</span>
    <span class="summary-pill ok">可用 ${ok}</span>
    <span class="summary-pill error">不可用 ${error}</span>
    <span class="summary-pill neutral">未检查 ${unchecked}</span>
  `;
}

function buildQueryString() {
  const params = new URLSearchParams();
  const names = [
    "q", "station_id", "key_id", "protocol_label", "supported", "status", "available", "enabled",
    "min_success_rate", "max_success_rate", "min_latency_ms", "max_latency_ms",
    "preview", "error", "sort_by", "sort_dir",
  ];
  names.forEach((name) => {
    const value = searchForm.elements[name]?.value ?? "";
    if (String(value).trim()) params.set(name, String(value).trim());
  });
  params.set("available_only", searchForm.elements.available_only.value || "0");
  return params.toString();
}

function fillSelectOptions(select, options, placeholder, currentValue = "") {
  const html = [`<option value="">${escapeHtml(placeholder)}</option>`]
    .concat(options.map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`))
    .join("");
  select.innerHTML = html;
  if (currentValue && options.some((o) => o.value === currentValue)) select.value = currentValue;
}

function refreshStationOptions() {
  fillSelectOptions(
    stationSelect,
    filterState.stations.map((s) => ({ value: String(s.id), label: `${s.name} · ${s.base_url}` })),
    "全部站点",
    stationSelect.value
  );
}

function refreshKeyOptions() {
  const selectedStation = stationSelect.value;
  const currentValue = keySelect.value;
  const keys = filterState.keys
    .filter((k) => !selectedStation || String(k.station_id) === selectedStation)
    .map((k) => ({
      value: String(k.id),
      label: selectedStation
        ? `${k.name}${k.group_name ? ` · ${k.group_name}` : ""}`
        : `${k.name} · ${k.station_name}${k.group_name ? ` · ${k.group_name}` : ""}`,
    }));
  fillSelectOptions(keySelect, keys, "全部 Key", currentValue);
}

function refreshProtocolOptions() {
  const selectedStation = stationSelect.value;
  const selectedKey = keySelect.value;
  const currentValue = protocolSelect.value;
  const labels = new Map();
  filterState.bindings
    .filter((b) => !selectedStation || String(b.station_id) === selectedStation)
    .filter((b) => !selectedKey || String(b.key_id) === selectedKey)
    .forEach((b) => {
      if (!labels.has(b.label)) labels.set(b.label, { value: b.label, label: b.label });
    });
  fillSelectOptions(protocolSelect, Array.from(labels.values()), "全部协议", currentValue);
}

function refreshFilterOptions() {
  refreshStationOptions();
  refreshKeyOptions();
  refreshProtocolOptions();
}

function applyInitialFilters() {
  const filterNames = [
    "q", "station_id", "key_id", "protocol_label", "supported", "status", "available", "enabled",
    "min_success_rate", "max_success_rate", "min_latency_ms", "max_latency_ms",
    "preview", "error", "sort_by", "sort_dir",
  ];
  filterNames.forEach((name) => {
    const v = initialParams.get(name);
    if (v != null && searchForm.elements[name]) searchForm.elements[name].value = v;
  });
  if (initialParams.get("available_only") === "1") {
    searchForm.elements.available_only.value = "1";
  }
  refreshFilterOptions();
}

async function loadFilterOptions() {
  const [stations, keys, bindings] = await Promise.all([
    request("/api/stations"),
    request("/api/keys"),
    request("/api/bindings"),
  ]);
  filterState.stations = stations;
  filterState.keys = keys;
  filterState.bindings = bindings;
  refreshFilterOptions();
}

async function runSearch() {
  const rows = await request(`/api/models/search?${buildQueryString()}`);
  currentRows = rows;
  pageIndex = 0;
  updateSummary(rows);
  render();
}

async function runSearchWithLogging() {
  try {
    await runSearch();
  } catch (error) {
    console.error(error);
  }
}

function resetSearchForm() {
  searchForm.reset();
  searchForm.elements.sort_by.value = "model_id";
  searchForm.elements.sort_dir.value = "asc";
  searchForm.elements.available_only.value = "";
  quickChips.forEach((c) => c.classList.remove("active"));
  refreshFilterOptions();
}

function applyPreset(preset, btn) {
  const active = btn.classList.toggle("active");
  // Reset this preset's fields first
  searchForm.elements.available.value = "";
  searchForm.elements.status.value = "";
  searchForm.elements.min_success_rate.value = "";
  searchForm.elements.min_latency_ms.value = "";
  searchForm.elements.available_only.value = "";
  // Clear other chips' active state (presets are mutually exclusive)
  quickChips.forEach((c) => { if (c !== btn) c.classList.remove("active"); });
  if (!active) { runSearchWithLogging(); return; }
  switch (preset) {
    case "available":
      searchForm.elements.available.value = "1";
      break;
    case "error":
      searchForm.elements.status.value = "error";
      break;
    case "unchecked":
      searchForm.elements.available.value = "unchecked";
      break;
    case "slow":
      searchForm.elements.min_latency_ms.value = "3000";
      break;
    case "perfect":
      searchForm.elements.min_success_rate.value = "100";
      break;
  }
  runSearchWithLogging();
}

function switchView(mode) {
  viewMode = mode;
  viewTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === mode));
  pageIndex = 0;
  render();
}

// Event wiring
stationSelect.addEventListener("change", () => {
  refreshKeyOptions();
  refreshProtocolOptions();
  runSearchWithLogging();
});

keySelect.addEventListener("change", () => {
  refreshProtocolOptions();
  runSearchWithLogging();
});

const instantSelects = [protocolSelect, searchForm.elements.supported, searchForm.elements.status, searchForm.elements.available];
instantSelects.forEach((el) => el.addEventListener("change", runSearchWithLogging));

const debouncedSearch = debounce(runSearchWithLogging, 300);
["q", "min_success_rate", "max_success_rate", "min_latency_ms", "max_latency_ms", "preview", "error"]
  .forEach((name) => {
    const el = searchForm.elements[name];
    if (el) el.addEventListener("input", debouncedSearch);
  });

searchForm.addEventListener("submit", (e) => {
  e.preventDefault();
  runSearchWithLogging();
});

resetSearchBtn.addEventListener("click", () => {
  resetSearchForm();
  runSearchWithLogging();
});

quickChips.forEach((btn) => {
  btn.addEventListener("click", () => applyPreset(btn.dataset.preset, btn));
});

viewTabs.forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
});

detailSortButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const currentField = searchForm.elements.sort_by.value || "model_id";
    const currentDir = searchForm.elements.sort_dir.value || "asc";
    const next = btn.dataset.sort;
    searchForm.elements.sort_by.value = next;
    searchForm.elements.sort_dir.value = currentField === next && currentDir === "asc" ? "desc" : "asc";
    runSearchWithLogging();
  });
});

groupSortButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const next = btn.dataset.groupSort;
    if (groupSort.field === next) {
      groupSort.dir = groupSort.dir === "asc" ? "desc" : "asc";
    } else {
      groupSort.field = next;
      groupSort.dir = "asc";
    }
    pageIndex = 0;
    renderGroupView();
  });
});

// Cell click → filter
document.addEventListener("click", (e) => {
  const cell = e.target.closest(".clickable-cell");
  if (cell) {
    e.preventDefault();
    const { filter, value } = cell.dataset;
    if (searchForm.elements[filter]) {
      if (filter === "station_id") {
        searchForm.elements.station_id.value = value;
        refreshKeyOptions();
        refreshProtocolOptions();
      } else if (filter === "key_id") {
        searchForm.elements.key_id.value = value;
        refreshProtocolOptions();
      } else {
        searchForm.elements[filter].value = value;
      }
      runSearchWithLogging();
    }
    return;
  }
  const toggle = e.target.closest(".group-toggle");
  if (toggle) {
    const id = toggle.dataset.model;
    if (expandedModels.has(id)) expandedModels.delete(id);
    else expandedModels.add(id);
    renderGroupView();
  }
});

pageSizeSelect.addEventListener("change", () => {
  pageSize = Number(pageSizeSelect.value) || 0;
  pageIndex = 0;
  render();
});

prevBtn.addEventListener("click", () => {
  if (pageIndex > 0) { pageIndex -= 1; render(); }
});
nextBtn.addEventListener("click", () => {
  pageIndex += 1; render();
});

async function init() {
  resetSearchForm();
  await loadFilterOptions();
  applyInitialFilters();
  pageSize = Number(pageSizeSelect.value) || 50;
  await runSearch();
}

init().catch((error) => console.error(error));
