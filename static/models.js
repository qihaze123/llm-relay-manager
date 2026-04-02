const { request, log } = window.RelayCommon;

const searchForm = document.getElementById("search-form");
const searchResults = document.getElementById("search-results");
const pageLog = document.getElementById("page-log");
const resetSearchBtn = document.getElementById("reset-search-btn");
const searchSummary = document.getElementById("models-search-summary");
const sortButtons = Array.from(document.querySelectorAll(".table-sort"));
const stationSelect = document.getElementById("models-station-select");
const keySelect = document.getElementById("models-key-select");
const protocolSelect = document.getElementById("models-protocol-select");
const initialParams = new URLSearchParams(window.location.search);

const filterState = {
  stations: [],
  keys: [],
  bindings: [],
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sortIndicator(field) {
  const activeField = searchForm.elements.sort_by.value || "model_id";
  const activeDir = searchForm.elements.sort_dir.value || "asc";
  if (field !== activeField) {
    return "";
  }
  return activeDir === "desc" ? " ↓" : " ↑";
}

function renderSortLabels() {
  sortButtons.forEach((button) => {
    const base = button.dataset.sortLabel || button.textContent.replace(/[↑↓]/g, "").trim();
    button.dataset.sortLabel = base;
    button.textContent = `${base}${sortIndicator(button.dataset.sort)}`;
  });
}

function statusBadgeClass(value, type = "status") {
  const normalized = String(value ?? "").toLowerCase();
  if (type === "boolean") {
    if (value == null) {
      return "neutral";
    }
    return value ? "ok" : "error";
  }
  if (normalized === "ok") {
    return "ok";
  }
  if (normalized === "partial") {
    return "partial";
  }
  if (normalized === "empty" || normalized === "unchecked") {
    return "neutral";
  }
  return "error";
}

function renderBadge(label, variant) {
  return `<span class="status-badge ${variant}">${escapeHtml(label)}</span>`;
}

function renderSearch(rows) {
  searchResults.innerHTML = rows.length
    ? rows
        .map((row) => {
          const rate = row.success_rate != null
            ? `${Number(row.success_rate).toFixed(Number.isInteger(Number(row.success_rate)) ? 0 : 1)}%`
            : "-";
          const supportedLabel = row.supported ? "支持" : "不支持";
          const statusLabel = row.status || "unchecked";
          const availableLabel = row.available == null ? "unchecked" : row.available ? "可用" : "不可用";

          return `
            <tr>
              <td><code>${escapeHtml(row.model_id)}</code></td>
              <td>${escapeHtml(row.station_name)}</td>
              <td>${escapeHtml(row.key_name)}</td>
              <td>${escapeHtml(row.protocol_label)}</td>
              <td>${renderBadge(supportedLabel, statusBadgeClass(row.supported, "boolean"))}</td>
              <td>${renderBadge(statusLabel, statusBadgeClass(statusLabel))}</td>
              <td>${renderBadge(availableLabel, statusBadgeClass(row.available, "boolean"))}</td>
              <td>${escapeHtml(rate)}</td>
              <td>${row.latency_ms ? `${escapeHtml(row.latency_ms)} ms` : "-"}</td>
              <td class="cell-preview" title="${escapeHtml(row.preview || "")}">${escapeHtml(row.preview || "-")}</td>
              <td class="cell-error" title="${escapeHtml(row.error || "")}">${escapeHtml(row.error || "-")}</td>
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="11">没有结果</td></tr>`;
}

function countActiveFilters() {
  const names = [
    "q",
    "station_id",
    "key_id",
    "protocol_label",
    "supported",
    "status",
    "available",
    "min_success_rate",
    "max_success_rate",
    "min_latency_ms",
    "max_latency_ms",
    "preview",
    "error",
  ];
  const active = names.filter((name) => String(searchForm.elements[name]?.value ?? "").trim()).length;
  return active + (searchForm.elements.available_only.checked ? 1 : 0);
}

function updateSearchSummary(rows) {
  const sortBy = searchForm.elements.sort_by.selectedOptions[0]?.textContent || "模型";
  const sortDir = searchForm.elements.sort_dir.value === "desc" ? "降序" : "升序";
  const activeFilters = countActiveFilters();
  searchSummary.textContent = `结果 ${rows.length} 条 · 已启用筛选 ${activeFilters} 个 · 排序 ${sortBy} / ${sortDir}`;
}

function buildQueryString() {
  const params = new URLSearchParams();
  const names = [
    "q",
    "station_id",
    "key_id",
    "protocol_label",
    "supported",
    "status",
    "available",
    "min_success_rate",
    "max_success_rate",
    "min_latency_ms",
    "max_latency_ms",
    "preview",
    "error",
    "sort_by",
    "sort_dir",
  ];
  names.forEach((name) => {
    const value = searchForm.elements[name]?.value ?? "";
    if (String(value).trim()) {
      params.set(name, String(value).trim());
    }
  });
  params.set("available_only", searchForm.elements.available_only.checked ? "1" : "0");
  return params.toString();
}

function fillSelectOptions(select, options, placeholder, currentValue = "") {
  const optionHtml = [`<option value="">${escapeHtml(placeholder)}</option>`]
    .concat(
      options.map(
        (option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`
      )
    )
    .join("");
  select.innerHTML = optionHtml;
  if (currentValue && options.some((option) => option.value === currentValue)) {
    select.value = currentValue;
  }
}

function refreshStationOptions() {
  fillSelectOptions(
    stationSelect,
    filterState.stations.map((station) => ({
      value: String(station.id),
      label: `${station.name} · ${station.base_url}`,
    })),
    "全部站点",
    stationSelect.value
  );
}

function refreshKeyOptions() {
  const selectedStation = stationSelect.value;
  const currentValue = keySelect.value;
  const keys = filterState.keys
    .filter((key) => !selectedStation || String(key.station_id) === selectedStation)
    .map((key) => ({
      value: String(key.id),
      label: selectedStation
        ? `${key.name}${key.group_name ? ` · ${key.group_name}` : ""}`
        : `${key.name} · ${key.station_name}${key.group_name ? ` · ${key.group_name}` : ""}`,
    }));
  fillSelectOptions(keySelect, keys, "全部 Key", currentValue);
}

function refreshProtocolOptions() {
  const selectedStation = stationSelect.value;
  const selectedKey = keySelect.value;
  const currentValue = protocolSelect.value;
  const labels = new Map();

  filterState.bindings
    .filter((binding) => !selectedStation || String(binding.station_id) === selectedStation)
    .filter((binding) => !selectedKey || String(binding.key_id) === selectedKey)
    .forEach((binding) => {
      if (!labels.has(binding.label)) {
        labels.set(binding.label, {
          value: binding.label,
          label: binding.label,
        });
      }
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
    "q",
    "station_id",
    "key_id",
    "protocol_label",
    "supported",
    "status",
    "available",
    "min_success_rate",
    "max_success_rate",
    "min_latency_ms",
    "max_latency_ms",
    "preview",
    "error",
    "sort_by",
    "sort_dir",
  ];
  filterNames.forEach((name) => {
    const value = initialParams.get(name);
    if (value != null && searchForm.elements[name]) {
      searchForm.elements[name].value = value;
    }
  });
  if (initialParams.get("available_only") === "1") {
    searchForm.elements.available_only.checked = true;
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
  renderSortLabels();
  const rows = await request(`/api/models/search?${buildQueryString()}`);
  renderSearch(rows);
  updateSearchSummary(rows);
}

function resetSearchForm() {
  searchForm.reset();
  searchForm.elements.sort_by.value = "model_id";
  searchForm.elements.sort_dir.value = "asc";
  refreshFilterOptions();
  renderSortLabels();
}

async function runSearchWithLogging() {
  try {
    await runSearch();
  } catch (error) {
    log(pageLog, error.message);
  }
}

stationSelect.addEventListener("change", async () => {
  refreshKeyOptions();
  refreshProtocolOptions();
  await runSearchWithLogging();
});

keySelect.addEventListener("change", async () => {
  refreshProtocolOptions();
  await runSearchWithLogging();
});

[
  protocolSelect,
  searchForm.elements.supported,
  searchForm.elements.status,
  searchForm.elements.available,
  searchForm.elements.sort_by,
  searchForm.elements.sort_dir,
  searchForm.elements.available_only,
].forEach((element) => {
  element.addEventListener("change", runSearchWithLogging);
});

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await runSearchWithLogging();
});

resetSearchBtn.addEventListener("click", async () => {
  resetSearchForm();
  await runSearchWithLogging();
});

sortButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    const currentField = searchForm.elements.sort_by.value || "model_id";
    const currentDir = searchForm.elements.sort_dir.value || "asc";
    const nextField = button.dataset.sort;
    searchForm.elements.sort_by.value = nextField;
    searchForm.elements.sort_dir.value = currentField === nextField && currentDir === "asc" ? "desc" : "asc";
    await runSearchWithLogging();
  });
});

async function init() {
  resetSearchForm();
  await loadFilterOptions();
  applyInitialFilters();
  await runSearch();
}

init().catch((error) => log(pageLog, error.message));
