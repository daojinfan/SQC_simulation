const state = {
  overview: null,
  configurations: [],
  management: { current: [], drafts: [], snapshots: [], active: [] },
  experiments: [],
  experimentPage: { page: null, hasMore: false, nextCursor: null, loadingMore: false },
  experimentLoadGeneration: 0,
  experimentFilters: { q: "", status: "" },
  experimentFilterTimer: null,
  experimentFilterGeneration: 0,
  experimentFilterAbortController: null,
  experimentAppliedFilterKey: null,
  storage: null,
  trash: null,
  storageFilter: "all",
  storageRefreshTimer: null,
  detail: null,
  configTab: "current",
  workbenchSection: "overview",
  objectTabs: { q1: "base", q2: "base", c: "gates", control: "clock" },
  draftDirty: false,
  draftEditVersion: 0,
  draftSaveTimer: null,
  draftSavePromise: Promise.resolve(),
  chartObserver: null,
  plotControllers: new Map(),
  currentHash: "#/overview",
  loadedResources: new Set(),
  pendingResources: new Map(),
  routeGeneration: 0,
  routeAbortController: null,
  apiCache: new Map(),
};

const app = document.querySelector("#app");
const actorInput = document.querySelector("#actor-id");
const dialog = document.querySelector("#action-dialog");
const dialogForm = document.querySelector("#dialog-form");
const toastElement = document.querySelector("#toast");
actorInput.value = localStorage.getItem("sqvm.actor") || "project.manager";
actorInput.addEventListener("change", () => localStorage.setItem("sqvm.actor", actorInput.value.trim()));
document.querySelector("#refresh").addEventListener("click", async () => {
  if (state.draftDirty && !(await persistDraft())) return;
  await refresh(true);
});
window.addEventListener("hashchange", handleHashChange);
window.addEventListener("beforeunload", (event) => {
  if (!state.draftDirty) return;
  event.preventDefault();
  event.returnValue = "";
});

const renderers = new Map([
  ["qubit_spectroscopy_calibration_v1", renderSpectroscopy],
  ["qubit_spectroscopy_scan_v1", renderSpectroscopyScan],
  ["qubit_rabi_x2p_amplitude_scan_v1", renderRabiAmplitude],
  ["qubit_spectroscopy", renderSpectroscopy],
  ["qubit_spectroscopy_scan", renderSpectroscopyScan],
  ["qubit_rabi_x2p_amplitude", renderRabiAmplitude],
]);

async function api(path, options = {}) {
  const method = options.method || "GET";
  const cached = method === "GET" ? state.apiCache.get(path) : null;
  const headers = { ...(options.headers || {}) };
  if (cached?.etag) headers["If-None-Match"] = cached.etag;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    headers: Object.keys(headers).length ? headers : undefined,
  });
  if (response.status === 304) {
    if (cached) return structuredClone(cached.payload);
    throw new Error("服务器返回了无法匹配本地缓存的 304 响应");
  }
  const payload = response.headers.get("Content-Type")?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const error = new Error(payload?.error || `请求失败（${response.status}）`);
    error.status = response.status;
    error.code = payload?.code;
    error.retryable = payload?.details?.retryable === true;
    error.fieldErrors = payload?.field_errors || payload?.validation?.field_errors || [];
    throw error;
  }
  const etag = method === "GET" ? response.headers.get("ETag") : null;
  if (etag && payload !== null) cacheApiPayload(path, etag, payload);
  return payload;
}

function cacheApiPayload(path, etag, payload) {
  state.apiCache.delete(path);
  state.apiCache.set(path, { etag, payload: structuredClone(payload) });
  while (state.apiCache.size > 128) state.apiCache.delete(state.apiCache.keys().next().value);
}

async function refresh(showToast = false) {
  setLoading();
  try {
    await route({ force: true });
    if (showToast) toast("数据已刷新");
  } catch (error) {
    markServiceUnavailable();
    renderError(error);
  }
}

function routeParts(hash = location.hash || "#/overview") {
  return hash.replace(/^#\//, "").split("/").filter(Boolean);
}

function routeResources(parts) {
  const view = parts[0] || "overview";
  if (view === "overview") return ["health", "overview"];
  if (view === "configurations") return ["configurations", "management"];
  if (view === "experiments" && !parts[1]) return ["experiments"];
  if (view === "storage") return ["storage"];
  if (view === "trash") return ["trash"];
  return [];
}

const resourceLoaders = {
  health: async () => {
    const health = await api("/api/v1/health");
    markServiceAvailable(health);
  },
  overview: async () => { state.overview = await api("/api/v1/overview"); },
  configurations: async () => { state.configurations = (await api("/api/v1/configurations")).items; },
  management: async () => { state.management = await api("/api/v1/configuration-management"); },
  experiments: async () => {
    const filterKey = experimentListUrl();
    applyExperimentPage(await api(filterKey));
    state.experimentAppliedFilterKey = filterKey;
  },
  storage: async () => { state.storage = await api("/api/v1/experiment-storage"); },
  trash: async () => { state.trash = await api("/api/v1/experiment-trash"); },
};

function resetResource(name) {
  state.loadedResources.delete(name);
  if (name === "overview") state.overview = null;
  else if (name === "configurations") state.configurations = [];
  else if (name === "management") state.management = { current: [], drafts: [], snapshots: [], active: [] };
  else if (name === "experiments") {
    state.experiments = [];
    state.experimentPage = { page: null, hasMore: false, nextCursor: null, loadingMore: false };
    state.experimentAppliedFilterKey = null;
    state.experimentLoadGeneration += 1;
  }
  else if (name === "storage") state.storage = null;
  else if (name === "trash") state.trash = null;
}

function invalidateResources(...names) {
  names.forEach(resetResource);
}

async function loadResource(name, { force = false } = {}) {
  if (!force && state.loadedResources.has(name)) return;
  if (state.pendingResources.has(name)) return state.pendingResources.get(name);
  if (force) resetResource(name);
  const request = resourceLoaders[name]().then(() => {
    state.loadedResources.add(name);
  }).finally(() => {
    state.pendingResources.delete(name);
  });
  state.pendingResources.set(name, request);
  return request;
}

async function loadRouteResources(parts, { force = false } = {}) {
  const resources = routeResources(parts);
  if (
    resources.includes("experiments")
    && state.loadedResources.has("experiments")
    && state.experimentAppliedFilterKey !== experimentListUrl()
  ) resetResource("experiments");
  await Promise.all(resources.map((name) => loadResource(name, { force })));
  if (!state.loadedResources.has("health")) markServiceAvailable();
}

function applyExperimentPage(payload, { append = false } = {}) {
  const response = payload && typeof payload === "object" ? payload : {};
  const items = Array.isArray(response.items) ? response.items : [];
  const page = response.page && typeof response.page === "object" ? { ...response, ...response.page } : response;
  if (append) {
    const seen = new Set(state.experiments.map(experimentIdentity));
    state.experiments = [...state.experiments, ...items.filter((item) => {
      const identity = experimentIdentity(item);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    })];
  } else {
    const seen = new Set();
    state.experiments = items.filter((item) => {
      const identity = experimentIdentity(item);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    });
  }
  state.experimentPage = {
    page: response.page ?? null,
    hasMore: page.has_more === true,
    nextCursor: typeof page.next_cursor === "string" && page.next_cursor ? page.next_cursor : null,
    loadingMore: false,
  };
}

function experimentIdentity(item) {
  return String(item?.run_id || `invalid:${item?.relative_path || item?.workflow_id || "unknown"}`);
}

function experimentListUrl(cursor = null) {
  const entries = [["limit", "50"]];
  const query = state.experimentFilters.q.trim();
  const statusFilter = state.experimentFilters.status;
  if (query) entries.push(["q", query]);
  if (["data-only", "eligible", "blocked"].includes(statusFilter)) {
    entries.push(["recommendation_state", statusFilter]);
  } else if (statusFilter === "invalid") {
    entries.push(["verification_status", "invalid"]);
  }
  if (cursor) entries.push(["cursor", cursor]);
  return `/api/v1/experiments?${entries.map(([name, value]) => `${name}=${encodeURIComponent(value)}`).join("&")}`;
}

function cancelExperimentFilterWork() {
  if (state.experimentFilterTimer) clearTimeout(state.experimentFilterTimer);
  state.experimentFilterTimer = null;
  state.experimentFilterAbortController?.abort();
  state.experimentFilterAbortController = null;
  state.experimentFilterGeneration += 1;
}

function markServiceAvailable(health = null) {
  document.querySelector("#service-status").className = "status status-good";
  document.querySelector("#service-status").textContent = !health || health.status === "ok" ? "配置管理 / 实验结果只读" : health.status;
}

function markServiceUnavailable() {
  document.querySelector("#service-status").className = "status status-bad";
  document.querySelector("#service-status").textContent = "服务不可用";
}

function beginRoute(hash) {
  cancelExperimentFilterWork();
  state.routeAbortController?.abort();
  const controller = new AbortController();
  state.routeAbortController = controller;
  state.routeGeneration += 1;
  return { hash, generation: state.routeGeneration, signal: controller.signal };
}

function routeIsCurrent(context) {
  return !context || (
    !context.signal.aborted
    && context.generation === state.routeGeneration
    && (location.hash || "#/overview") === context.hash
  );
}

function routeRequestOptions(context) {
  return context ? { signal: context.signal } : {};
}

function renderRouteError(error, context) {
  if (!routeIsCurrent(context) || error?.name === "AbortError") return;
  renderError(error);
}

async function route({ force = false } = {}) {
  if (state.storageRefreshTimer) {
    clearTimeout(state.storageRefreshTimer);
    state.storageRefreshTimer = null;
  }
  state.chartObserver?.disconnect();
  state.chartObserver = null;
  state.plotControllers.forEach(clearPlotHover);
  state.plotControllers.clear();
  app.oninput = null;
  const hash = location.hash || "#/overview";
  const context = beginRoute(hash);
  const parts = routeParts(hash);
  const view = parts[0] || "overview";
  document.querySelectorAll("[data-nav]").forEach((link) => link.classList.toggle("active", link.dataset.nav === view));
  try {
    await loadRouteResources(parts, { force });
  } catch (error) {
    if (!routeIsCurrent(context)) return;
    throw error;
  }
  if (!routeIsCurrent(context)) return;
  if (view === "overview") {
    title("平台", "总览");
    renderOverview();
  } else if (view === "configurations" && parts[1] === "drafts" && parts[2]) {
    title("配置", "编辑草稿");
    await renderDraft(parts[2], context);
  } else if (view === "configurations" && parts[1] === "current" && parts[2]) {
    title("配置", "当前配置");
    await renderCurrentConfiguration(parts[2], context);
  } else if (view === "configurations" && parts[1] === "snapshots" && parts[2]) {
    title("配置", "已发布快照");
    await renderPlatformSnapshot(parts[2], context);
  } else if (view === "configurations" && parts[1]) {
    title("配置", "快照详情");
    await renderConfiguration(parts[1], context);
  } else if (view === "configurations") {
    title("平台", "配置管理");
    renderConfigurations();
  } else if (view === "storage") {
    title("实验", "实验存储");
    await renderStorage();
  } else if (view === "trash") {
    title("实验", "回收站");
    await renderTrash();
  } else if (view === "experiments" && parts[1]) {
    title("实验", "运行详情");
    await renderExperiment(parts[1], context);
  } else if (view === "experiments") {
    title("平台", "实验结果");
    renderExperiments(context);
  } else {
    location.hash = "#/overview";
  }
  if (!routeIsCurrent(context)) return;
  app.focus({ preventScroll: true });
  state.currentHash = location.hash || "#/overview";
}

async function handleHashChange() {
  if (state.draftDirty && !(await persistDraft())) {
    history.replaceState(null, "", state.currentHash);
    return;
  }
  setLoading();
  try {
    await route();
  } catch (error) {
    markServiceUnavailable();
    renderError(error);
  }
}

function title(kicker, text) {
  document.querySelector("#view-kicker").textContent = kicker;
  document.querySelector("#view-title").textContent = text;
}

function renderOverview() {
  const o = state.overview;
  app.innerHTML = `
    <section class="metric-strip" aria-label="平台摘要">
      ${metric("配置数量", o.configurations.total)}
      ${metric("已接受", o.configurations.accepted)}
      ${metric("实验数量", o.experiments.total)}
      ${metric("可生成候选", o.experiments.eligible)}
    </section>
    <section class="section">
      <div class="section-head"><div><h2>最近实验</h2><p>基于模型的校准运行结果</p></div></div>
      ${experimentTable(o.latest_experiments)}
    </section>
    <section class="section">
      <div class="section-head"><div><h2>配置状态</h2><p>当前配置与可恢复的历史快照</p></div></div>
      <div class="facts">
        ${fact("当前配置", state.loadedResources.has("management") ? state.management.current.length : "-")}
        ${fact("历史快照", state.loadedResources.has("management") ? state.management.snapshots.length : "-")}
        ${fact("兼容草稿", state.loadedResources.has("management") ? state.management.drafts.length : "-")}
        ${fact("无效产物", o.experiments.invalid)}
      </div>
    </section>`;
  bindExperimentRows();
}

function renderConfigurations() {
  const tabs = `
    <div class="tabs">
      <button data-config-tab="current" class="${state.configTab === "current" ? "active" : ""}">当前配置</button>
      <button data-config-tab="snapshots" class="${state.configTab === "snapshots" ? "active" : ""}">快照历史</button>
      <button data-config-tab="history" class="${state.configTab === "history" ? "active" : ""}">旧版记录</button>
    </div>`;
  app.innerHTML = `
    <section class="configuration-dashboard">
      <div class="section-head">
        <div><p class="kicker">设备配置工作台</p><h2>配置中心</h2><p>当前配置保存后立即用于后续实验；需要留档或回退时使用快照。</p></div>
      </div>
      ${configurationTopology()}
      <section class="version-zone"><div class="section-head"><div><h3>配置与版本</h3><p>当前配置是工作副本，快照是只读历史版本。</p></div></div>
      ${tabs}
      <div id="config-content">${configurationTabContent()}</div></section>
    </section>`;
  document.querySelectorAll("[data-config-tab]").forEach((button) => button.addEventListener("click", () => {
    state.configTab = button.dataset.configTab;
    renderConfigurations();
  }));
  bindConfigurationRows();
}

function configurationTopology() {
  const current = state.management.current[0];
  const snapshot = state.management.snapshots[0];
  const frequencies = current?.reference_frequencies_GHz || {};
  const calibration = { qagents: Object.fromEntries(Object.entries(frequencies).map(([target, value]) => [target, { reference_frequency_authority: { reference_frequency_GHz: value } }])) };
  return `<div class="configuration-hero"><div>${topologyView(calibration)}</div><div class="device-status-list"><div><span>当前配置</span><strong>${current ? esc(current.name) : "无"}</strong></div><div><span>当前修订</span><strong>${current ? `r${current.revision}` : "-"}</strong></div><div><span>最近快照</span><strong>${snapshot ? esc(snapshot.name) : "无"}</strong></div></div></div>`;
}

function configurationTabContent() {
  if (state.configTab === "current") {
    if (!state.management.current.length) return empty("暂无当前配置，请先从已有配置迁移");
    return `<div class="current-config-list">${state.management.current.map((row) => `
      <button class="current-config-row" data-current-device="${esc(row.device_id)}"><span><strong>${esc(row.name)}</strong><small>${esc(row.device_id)} · 修订 r${row.revision}</small></span><span>${status(row.validation_status)}<small>${dateText(row.updated_utc)}</small></span><span class="current-config-action">编辑配置 →</span></button>`).join("")}</div>`;
  }
  if (state.configTab === "history") {
    return configurationTable(state.configurations);
  }
  const platform = state.management.snapshots;
  if (!platform.length) return `<div class="empty">暂无配置快照</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>名称</th><th>设备</th><th>状态</th><th>准入状态</th><th>发布时间</th></tr></thead><tbody>${platform.map((row) => `
    <tr class="clickable" data-platform-id="${esc(row.snapshot_id)}"><td><strong>${esc(row.name)}</strong><br><span class="mono muted">${short(row.snapshot_id)}</span></td><td>${esc(row.device_id)}</td><td>${row.keep ? status("keep") : status("published")}</td><td>${row.experiment_eligible ? status("eligible") : status("requires_requalification")}</td><td>${dateText(row.published_utc)}</td></tr>`).join("")}</tbody></table></div>`;
}

function configurationTable(rows) {
  if (!rows.length) return empty("暂无配置快照");
  return `<div class="table-wrap"><table><thead><tr><th>配置</th><th>状态</th><th>设备</th><th>目标</th><th>路径</th></tr></thead><tbody>${rows.map((row) => `
    <tr class="clickable" data-config-id="${esc(row.configuration_id)}"><td><strong>${esc(row.name || row.state_id || short(row.configuration_id))}</strong></td><td>${status(row.verification_status === "invalid" ? "invalid" : row.status)}</td><td>${esc(row.device_id || "demo_2q1c2r")}</td><td>${esc((row.accepted_targets || []).join(", ") || "-")}</td><td class="mono">${esc(row.relative_path)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function renderConfiguration(id, routeContext = null) {
  setLoading();
  try {
    const item = await api(`/api/v1/configurations/${encodeURIComponent(id)}`, routeRequestOptions(routeContext));
    if (!routeIsCurrent(routeContext)) return;
    app.innerHTML = `
      ${detailHeader(item.name || item.state_id || "配置", item.configuration_id, [item.status, item.verification_status], `<button id="draft-from-config" class="button primary">创建草稿</button>`)}
      <section class="section"><div class="facts">${fact("设备", item.device_id || "demo_2q1c2r")}${fact("已接受", item.accepted ? "是" : "否")}${fact("目标", (item.accepted_targets || []).join(", ") || "-")}${fact("路径", item.relative_path, true)}</div></section>
      ${controlConfigurationView(item.control_values || {})}
      ${calibrationConfigurationView(item.values || {})}
      <section class="section"><details><summary>原始配置证据 JSON</summary><pre>${esc(JSON.stringify(item.raw, null, 2))}</pre></details></section>`;
    document.querySelector("#draft-from-config").addEventListener("click", () => openNewDraft(item.configuration_id));
  } catch (error) { renderRouteError(error, routeContext); }
}

async function renderCurrentConfiguration(deviceId, routeContext = null) {
  setLoading();
  try {
    const item = await api(`/api/v1/current-configurations/${encodeURIComponent(deviceId)}`, routeRequestOptions(routeContext));
    if (!routeIsCurrent(routeContext)) return;
    state.detail = item;
    state.draftDirty = false;
    app.innerHTML = workbenchShell(item, "current");
    app.oninput = markDraftDirty;
    bindStructuredEditor();
    bindWorkbench(item, "current");
    document.querySelector("#save-current").addEventListener("click", () => persistDraft({ notify: true }));
    document.querySelector("#snapshot-current").addEventListener("click", () => openCurrentSnapshot(item));
    document.querySelector("#initialize-calibration")?.addEventListener("click", async () => {
      if (state.draftDirty && !(await persistDraft())) return;
      const current = state.detail;
      try {
        await mutate(`/api/v1/current-configurations/${encodeURIComponent(current.device_id)}/initialize-calibration`, {
          actor_id: actor(),
          expected_content_sha256: current.content_sha256,
        });
        invalidateResources("management", "overview");
        state.draftDirty = false;
        await renderCurrentConfiguration(current.device_id);
        toast("已初始化当前配置的校准参数");
      } catch (error) { showMutationError(error); }
    });
  } catch (error) { renderRouteError(error, routeContext); }
}

async function renderPlatformSnapshot(id, routeContext = null) {
  setLoading();
  try {
    const item = await api(`/api/v1/platform-snapshots/${id}`, routeRequestOptions(routeContext));
    if (!routeIsCurrent(routeContext)) return;
    const actions = `
      <button id="snapshot-keep" class="button secondary">${item.keep ? "取消长期保存" : "长期保存"}</button>
      <button id="snapshot-delete" class="button danger" ${item.keep || item.active ? "disabled" : ""} title="${item.keep ? "请先取消长期保存再删除" : item.active ? "该快照仍被兼容运行上下文引用" : "删除未被引用的快照"}">删除</button>
      <button id="snapshot-apply" class="button primary">恢复到当前配置</button>`;
    app.innerHTML = `${detailHeader(item.name, item.snapshot_id, ["published", item.experiment_eligible ? "eligible" : "requires_requalification"], actions)}
      ${workbenchShell(item, "snapshot")}`;
    document.querySelector("#snapshot-keep").addEventListener("click", async () => {
      await mutate(`/api/v1/platform-snapshots/${id}/keep`, { actor_id: actor(), keep: !item.keep });
      await refreshAfterMutation(location.hash || "#/overview", "management", "configurations", "overview");
    });
    document.querySelector("#snapshot-apply").addEventListener("click", () => openApplySnapshot(item));
    document.querySelector("#snapshot-delete").addEventListener("click", async () => {
      if (!confirm(`确定删除快照“${item.name}”吗？仅未长期保存且未被当前配置引用的快照可以删除。`)) return;
      try {
        await api(`/api/v1/platform-snapshots/${id}`, { method: "DELETE", headers: { "X-SQVM-Actor": actor() } });
        await refreshAfterMutation("#/configurations", "management", "configurations", "overview");
      } catch (error) { toast(error.message, true); }
    });
    bindWorkbench(item, "snapshot");
  } catch (error) { renderRouteError(error, routeContext); }
}

async function renderDraft(id, routeContext = null) {
  setLoading();
  try {
    const item = await api(`/api/v1/drafts/${id}`, routeRequestOptions(routeContext));
    if (!routeIsCurrent(routeContext)) return;
    state.detail = item;
    state.draftDirty = false;
    const capabilities = editorCapabilities(item);
    app.innerHTML = `${detailHeader(item.name, item.draft_id, ["draft", item.validation.status], `<button id="delete-draft" class="button danger">删除</button>`)}
      ${workbenchShell(item, "draft", capabilities)}`;
    app.oninput = markDraftDirty;
    bindStructuredEditor();
    bindWorkbench(item, "draft");
    document.querySelector("#save-draft").addEventListener("click", () => persistDraft({ notify: true }));
    document.querySelector("#validate-draft").addEventListener("click", async () => {
      if (state.draftDirty && !(await persistDraft())) return;
      try {
        const validation = await mutate(`/api/v1/drafts/${id}/validate`, { actor_id: actor() });
        invalidateResources("management", "overview");
        if (validation.field_errors?.length) showFieldErrors(validation.field_errors);
        await renderDraft(id);
      } catch (error) { showMutationError(error); }
    });
    document.querySelector("#publish-draft").addEventListener("click", () => openPublish(item));
    document.querySelector("#initialize-calibration")?.addEventListener("click", async () => {
      if (state.draftDirty && !(await persistDraft())) return;
      const current = state.detail;
      try {
        await mutate(`/api/v1/drafts/${current.draft_id}/initialize-calibration`, {
          actor_id: actor(),
          expected_content_sha256: current.content_sha256,
        });
        invalidateResources("management", "overview");
        state.draftDirty = false;
        await renderDraft(current.draft_id);
        toast("已初始化校准参数，请填写结构化记录");
      } catch (error) { showMutationError(error); }
    });
    document.querySelector("#delete-draft").addEventListener("click", async () => {
      if (!confirm(`确定删除草稿“${item.name}”吗？`)) return;
      await api(`/api/v1/drafts/${id}`, { method: "DELETE", headers: { "X-SQVM-Actor": actor() } });
      await refreshAfterMutation("#/configurations", "management", "configurations", "overview");
    });
  } catch (error) { renderRouteError(error, routeContext); }
}

function draftEditor(item) {
  const editable = editorEditable(item);
  const control = editable.control_values || {};
  const calibration = editable.calibration_values || {};
  return `<div class="config-group"><h3>草稿信息</h3><div class="editor-grid">
    ${textField("草稿名称", "draft-name", item.name)}${textField("备注", "draft-note", item.note || "")}
  </div></div>
  ${controlEditor(control)}
  ${calibrationEditor(calibration)}
  ${readonlyEditor(item)}`;
}

function workbenchShell(item, mode, capabilities = editorCapabilities(item)) {
  const section = state.workbenchSection;
  const actions = mode === "current"
    ? `<div class="workbench-actions"><span class="save-state mono muted">已保存并生效 / 修订 r${item.revision}</span><button id="save-current" class="button secondary">保存并生效</button><button id="snapshot-current" class="button primary" ${item.validation.status === "valid" ? "" : "disabled"}>保存快照</button></div>`
    : mode === "draft"
      ? `<div class="workbench-actions"><span class="save-state mono muted">检查点 ${item.checkpoint}</span><button id="save-draft" class="button secondary" ${capabilities.canSave ? "" : "disabled"}>保存</button><button id="validate-draft" class="button secondary" ${capabilities.canValidate ? "" : "disabled"}>校验</button><button id="publish-draft" class="button primary" ${capabilities.canPublish && item.validation.status === "valid" ? "" : "disabled"}>发布快照</button></div>`
      : `<div class="workbench-actions"><span class="save-state mono muted">只读快照</span></div>`;
  return `<section class="workbench">
    <header class="workbench-bar"><div><p class="kicker">${mode === "current" ? `${esc(item.device_id)} · 当前配置` : mode === "snapshot" ? "历史快照" : "兼容草稿"}</p><h2>${esc(mode === "current" ? item.name : item.device_id || "设备")}</h2><div class="detail-meta">${mode === "snapshot" ? status("published") : status(item.validation?.status)}${item.requires_requalification || item.validation?.requires_requalification ? status("requires_requalification") : ""}</div></div>${actions}</header>
    <div id="field-errors" class="field-errors" hidden></div>
    <div class="workbench-layout"><nav class="workbench-nav" aria-label="配置对象">${workbenchNav(section)}</nav><div class="workbench-main">${workbenchContent(item, mode, section)}</div></div>
  </section>`;
}

function workbenchNav(active) {
  const items = [["overview", "概览", "device"], ["q1", "Q1", "q1"], ["q2", "Q2", "q2"], ["c", "C", "c"], ["control", "控制链", "control"], ["review", "状态与版本", "review"]];
  return items.map(([id, label, tone]) => `<button type="button" data-workbench-section="${id}" class="${active === id ? "active" : ""}"><span class="object-dot ${tone}"></span>${label}</button>`).join("");
}

function workbenchContent(item, mode, section) {
  const editable = item.editable || {};
  const calibration = editable.calibration_values || {};
  const control = editable.control_values || {};
  if (mode === "snapshot") return readonlyWorkbench(item, section);
  if (section === "overview") return editableOverview(item, mode);
  if (section === "q1" || section === "q2") return qubitWorkbench(section.toUpperCase(), calibration, control);
  if (section === "c") return couplerWorkbench(calibration, control);
  if (section === "control") return controlWorkbench(control);
  return reviewWorkbench(item);
}

function editableOverview(item, mode = "draft") {
  const editable = item.editable || {};
  const calibration = editable.calibration_values || {};
  return `<section class="workbench-section"><div class="section-head"><div><h3>设备概览</h3><p>Q1-C-Q2 与读出链路。物理器件参数由设备权威管理。</p></div></div>${topologyView(calibration)}
    <div class="editor-grid workbench-metadata">${textField(mode === "current" ? "配置名称" : "草稿名称", "draft-name", item.name, "用于在配置中心和快照历史中识别该配置。")}${textField("备注", "draft-note", item.note || "", "记录本次配置调整的背景或用途。")}</div>
    <div class="section-head compact-head"><div><h3>只读权威</h3></div></div>${readonlyEditor(item)}</section>`;
}

function qubitWorkbench(target, calibration, control) {
  if (isEmptyCalibration(calibration)) return calibrationBootstrap();
  const registry = calibration.waveform_registry || {}, settings = registry.settings || {}, mappers = registry.mappers || {};
  const value = calibration.qagents?.[target];
  const gate = calibration.gate_configuration?.[target];
  const key = target.toLowerCase(), tab = state.objectTabs[key] || "base";
  const content = tab === "base" ? `<div class="setting-grid">${objectIdleFluxEditor(target, control)}${referenceEditor(target, value)}${gate ? qubitGateEditor(target, gate, settings, mappers) : uncalibrated("未校准：无 Gate Configuration")}</div>` : tab === "pulses" ? settingGroups(settings, [target], false) : tab === "mapper" ? mapperObjectGroups(mappers, "F012ZBIAS_MAPPER", target) : referenceEvidence(value?.reference_frequency_authority);
  return `<section class="workbench-section object-page ${key}"><div class="object-heading"><span class="object-dot ${key}"></span><div><h3>${target}</h3><p>单比特频率、脉冲 Setting、F012ZBIAS Mapper 与校准证据</p></div></div>${objectTabs(key, tab, [["base", "基础"], ["pulses", "脉冲"], ["mapper", "Mapper"], ["evidence", "证据"]])}<div class="object-subsection">${content}</div></section>`;
}

function couplerWorkbench(calibration, control) {
  if (isEmptyCalibration(calibration)) return calibrationBootstrap();
  const registry = calibration.waveform_registry || {}, settings = registry.settings || {}, mappers = registry.mappers || {};
  const gate = calibration.gate_configuration?.C;
  const tab = state.objectTabs.c || "gates";
  const content = tab === "gates" ? `${objectIdleFluxEditor("C", control)}${gate ? couplerGateEditor(gate, settings, mappers) : uncalibrated("未校准：无耦合器 Gate Configuration")}${settingGroups(settings, ["C"], true)}` : tab === "mapper" ? mapperObjectGroups(mappers, "G2ZBIAS_MAPPER", "C") : characterizationView(calibration.fsim_characterizations);
  return `<section class="workbench-section object-page c"><div class="object-heading"><span class="object-dot c"></span><div><h3>C</h3><p>耦合器 CZ / FSIM 波形、G2ZBIAS Mapper 与标定结果</p></div></div>${objectTabs("c", tab, [["gates", "CZ / FSIM"], ["mapper", "G2 Mapper"], ["results", "标定结果"]])}<div class="object-subsection">${content}</div></section>`;
}

function controlWorkbench(control) {
  const tab = state.objectTabs.control || "clock";
  const content = tab === "clock" ? controlClockEditor(control) : tab === "lanes" ? laneEditor(control) : tab === "mixing" ? Object.entries(control.static_mixing || {}).map(([name, value]) => matrixEditor(name, value)).join("") : tab === "simulation" ? simulationModelEditor(control) : acceptanceEditor(control);
  return `<section class="workbench-section"><div class="object-heading"><span class="object-dot control"></span><div><h3>控制链</h3><p>时钟、电子学链、仿真截断与准入阈值</p></div></div>${objectTabs("control", tab, [["clock", "时钟与 DAC"], ["lanes", "通道"], ["mixing", "混合"], ["simulation", "仿真截断"], ["acceptance", "阈值"]])}<div class="object-subsection">${content || uncalibrated("未提供控制链数据")}</div></section>`;
}

function objectTabs(object, active, items) { return `<div class="object-tabs" role="tablist">${items.map(([id, label]) => `<button type="button" data-object="${object}" data-object-tab="${id}" role="tab" aria-selected="${active === id}" class="${active === id ? "active" : ""}">${label}</button>`).join("")}</div>`; }

function controlClockEditor(control) { const clock = control.clock || {}, dac = control.dac || {}; return `<div class="editor-grid">${numberField("采样率（Hz）", "control_values.clock.sample_rate_Hz", clock.sample_rate_Hz)}${numberField("采样间隔（ns）", "control_values.clock.dt_ns", clock.dt_ns, "0.000001")}${numberField("DAC 位数", "control_values.dac.bits", dac.bits, "1")}${selectField("DAC 舍入模式", "control_values.dac.rounding", dac.rounding, ["half_even"])}${numberField("DAC 满量程下限（V）", "control_values.dac.full_scale_min_V", dac.full_scale_min_V)}${numberField("DAC 满量程上限（V，开区间）", "control_values.dac.full_scale_max_exclusive_V", dac.full_scale_max_exclusive_V)}</div>`; }
function objectIdleFluxEditor(target, control) { const key = target.toLowerCase(); return numberField(`${target} 空闲磁通（Phi/Phi0）`, `control_values.idle_flux_phi0.${key}`, control.idle_flux_phi0?.[key], "0.000001"); }
function acceptanceEditor(control) { const acceptance = control.acceptance || {}; return `<div class="editor-grid">${Object.keys(acceptance).map((key) => numberField(parameterLabel(key), `control_values.acceptance.${key}`, acceptance[key], key === "max_formal_samples_per_scenario" ? "1" : "any")).join("")}</div>`; }

function simulationModelEditor(control) {
  const defaults = {
    charge_cutoffs: { q1: 7, c: 7, q2: 7 },
    retained_energy_levels: { q1: 5, c: 3, q2: 5 },
    convergence_charge_cutoffs: { q1: 8, c: 8, q2: 8 },
    convergence_retained_energy_levels: { q1: 6, c: 4, q2: 6 },
  };
  const model = control.simulation?.calibration_model || defaults;
  const modes = [["q1", "Q1"], ["c", "C"], ["q2", "Q2"]];
  const total = (field) => modes.reduce((value, [mode]) => value * Number(model[field]?.[mode] || 0), 1);
  const fields = (name, title, detail) => `<details class="parameter-folder" open><summary><span><strong>${esc(title)}</strong><small>${esc(detail)}</small></span><span class="folder-summary-meta" ${name.includes("retained") ? `data-simulation-field="${esc(name)}"` : ""}>${name.includes("retained") ? `${total(name)} 维` : "电荷基"}</span></summary><div class="folder-body">${modes.map(([mode, label]) => numberField(`${label} ${name.includes("cutoff") ? "电荷截止 N" : "保留能级数"}`, `control_values.simulation.calibration_model.${name}.${mode}`, model[name]?.[mode], "1")).join("")}</div></details>`;
  return `<div class="simulation-dimension-strip"><div class="fact"><span>基线动力学空间</span><strong data-simulation-summary="baseline-dimension">${total("retained_energy_levels")} 维</strong></div><div class="fact"><span>收敛对照空间</span><strong data-simulation-summary="convergence-dimension">${total("convergence_retained_energy_levels")} 维</strong></div><div class="fact"><span>基线张量</span><strong class="mono" data-simulation-summary="baseline-tensor">${modes.map(([mode]) => model.retained_energy_levels?.[mode]).join(" × ")}</strong></div></div><div class="parameter-folders simulation-folders">${fields("charge_cutoffs", "基线电荷截断", "每个模态保留 2N+1 个电荷态")}${fields("retained_energy_levels", "基线动力学投影", "默认 5 × 3 × 5 = 75 维")}${fields("convergence_charge_cutoffs", "收敛电荷截断", "不得低于对应基线")}${fields("convergence_retained_energy_levels", "收敛动力学投影", "用于检查频率与驱动矩阵元漂移")}</div>`;
}

function reviewWorkbench(item) {
  const validation = item.validation || {};
  const controlChanged = validation.requires_requalification || item.requires_requalification;
  const isCurrent = item.artifact_type === "platform_configuration_current";
  const changePanel = isCurrent
    ? `<div class="diff-summary">${fact("当前修订", `r${item.revision}`)}${fact("内容哈希", short(item.content_sha256), true)}${fact("来源快照", short(item.source_snapshot_id), true)}</div>`
    : `<div id="server-diff" class="diff-panel"><span class="muted">正在读取差异...</span></div>`;
  return `<section class="workbench-section"><div class="section-head"><div><h3>${isCurrent ? "状态与版本" : "变更与发布"}</h3><p>${isCurrent ? "检查当前配置状态，并在需要时保存可恢复的历史快照。" : "提交前检查配置影响、校验结果与发布条件。"}</p></div></div>
    <div class="change-summary"><div><span class="object-dot q1"></span><strong>Q1 / Q2</strong><p>参考频率、脉冲 Setting 和 Mapper 的校准记录。</p></div><div><span class="object-dot c"></span><strong>C</strong><p>CZ、FSIM、耦合器 Mapper 与动态相位。</p></div><div><span class="object-dot control"></span><strong>控制链</strong><p>${controlChanged ? "控制参数已变更，发布后需要重新准入。" : "当前未检测到需要重新准入的控制参数变更。"}</p></div></div>
    <div class="object-subsection"><h4>${isCurrent ? "当前版本" : "服务端差异"}</h4>${changePanel}</div>
    <div class="object-subsection"><h4>校验结果</h4>${validationList(validation)}</div>
    <div class="object-subsection"><h4>${isCurrent ? "快照条件" : "发布条件"}</h4><div class="facts">${fact("保存状态", state.draftDirty ? "有未保存修改" : "已保存")}${fact("配置校验", validation.status === "valid" ? "通过" : "未通过")}${fact("重新准入", controlChanged ? "需要" : "不需要")}${fact(isCurrent ? "可保存快照" : "实验准入", isCurrent ? (validation.status === "valid" && !state.draftDirty ? "是" : "否") : (item.experiment_eligible ? "可用" : "待发布后判定"))}</div></div>
    <div class="object-subsection"><h4>只读权威</h4>${readonlyEditor(item)}</div></section>`;
}

function calibrationBootstrap() { return `<section class="workbench-section calibration-bootstrap"><h3>未初始化的校准参数</h3><p class="muted">初始化后将建立 Q1、Q2、C 对象所需的 typed Setting、Mapper 与选择关系。</p><button id="initialize-calibration" type="button" class="button primary">初始化校准参数</button></section>`; }

function topologyView(calibration) {
  const refs = calibration.qagents || {};
  const frequency = (target) => refs[target]?.reference_frequency_authority?.reference_frequency_GHz;
  return `<div class="device-topology" aria-label="Q1-C-Q2 设备拓扑"><div class="topology-node q1"><strong>Q1</strong><small>${frequency("Q1") ? `${fmt(frequency("Q1"), 6)} GHz` : "未校准"}</small></div><span class="topology-link"></span><div class="topology-node c"><strong>C</strong><small>CZ / FSIM</small></div><span class="topology-link"></span><div class="topology-node q2"><strong>Q2</strong><small>${frequency("Q2") ? `${fmt(frequency("Q2"), 6)} GHz` : "未校准"}</small></div><div class="readout-rail"><span>R1 读出</span><span>R2 读出</span></div></div>`;
}

function bindWorkbench(item, mode) {
  document.querySelectorAll("[data-workbench-section]").forEach((button) => button.addEventListener("click", async () => {
    if (mode !== "snapshot" && state.draftDirty && !(await persistDraft())) return;
    state.workbenchSection = button.dataset.workbenchSection;
    if (mode === "current") renderCurrentConfiguration(item.device_id);
    else if (mode === "draft") renderDraft(item.draft_id);
    else renderPlatformSnapshot(item.snapshot_id);
  }));
  document.querySelectorAll("[data-object-tab]").forEach((button) => button.addEventListener("click", async () => {
    if (mode !== "snapshot" && state.draftDirty && !(await persistDraft())) return;
    state.objectTabs[button.dataset.object] = button.dataset.objectTab;
    if (mode === "current") renderCurrentConfiguration(item.device_id);
    else if (mode === "draft") renderDraft(item.draft_id);
    else renderPlatformSnapshot(item.snapshot_id);
  }));
  if (mode === "draft" && state.workbenchSection === "review") loadDraftDiff(item);
}

async function loadDraftDiff(item) {
  const target = document.querySelector("#server-diff");
  if (!target) return;
  try {
    const result = await api(`/api/v1/drafts/${encodeURIComponent(item.draft_id)}/diff`);
    target.innerHTML = renderServerDiff(result);
  } catch (error) {
    if (error.status !== 404) return showMutationError(error);
    target.innerHTML = `<div class="diff-fallback"><strong>本地摘要</strong><span>后端 diff 尚不可用；以下内容依据当前草稿状态生成。</span>${localDiffSummary(item)}</div>`;
  }
}

function renderServerDiff(result) {
  const changes = result.changes || [];
  if (!changes.length) return `<div class="diff-fallback">服务端未报告字段差异。</div>`;
  const grouped = changes.reduce((all, change) => {
    const group = change.group || "其他";
    (all[group] ||= []).push(change);
    return all;
  }, {});
  const summary = `<div class="diff-summary">${fact("变更字段", result.changed_count ?? changes.length)}${fact("控制链变更", result.control_changed ? "是" : "否")}${fact("重新准入", result.requires_requalification ? "需要" : "不需要")}</div>`;
  return `${summary}<div class="diff-list">${Object.entries(grouped).map(([group, rows]) => `<details class="diff-group"><summary><strong>${esc(diffGroupLabel(group))}</strong><span>${rows.length} 个字段</span></summary><div class="diff-group-rows">${rows.map((row) => `<span class="mono">${esc(normalizeFieldPath(row.path || ""))}<br><small>${esc(diffValue(row.before))} -> ${esc(diffValue(row.after))}</small></span>`).join("")}</div></details>`).join("")}</div>`;
}

function diffGroupLabel(group) {
  return { control: "控制链", other: "其他" }[group] || group;
}

function diffValue(value) {
  if (value === undefined || value === null) return "-";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function localDiffSummary(item) {
  const validation = item.validation || {};
  return `<ul><li>草稿检查点：${item.checkpoint}</li><li>控制链：${validation.requires_requalification ? "已变更，需重新准入" : "未报告重新准入"}</li><li>校验状态：${statusText(validation.status || "not_validated")}</li></ul>`;
}

function qubitGateEditor(target, gate, settings, mappers) {
  const path = `calibration_values.gate_configuration.${target}`;
  const settingChoices = (type) => Object.entries(settings).filter(([, row]) => row.target === target && row.gate_type === type).map(([id]) => id);
  const mapperChoices = Object.entries(mappers).filter(([, row]) => row.target === target && row.mapper_type === "F012ZBIAS_MAPPER").map(([id]) => id);
  return `<div class="form-cluster"><h4>活动选择</h4>${selectField("XY", `${path}.active_xy_setting`, gate.active_xy_setting, settingChoices("XY"))}${selectField("XY2", `${path}.active_xy2_setting`, gate.active_xy2_setting, settingChoices("XY2"))}${selectField("X12", `${path}.active_xy12_setting`, gate.active_xy12_setting, settingChoices("X12"))}${selectField("DTN", `${path}.active_detune_setting`, gate.active_detune_setting, settingChoices("DTN"))}${selectField("F012 Mapper", `${path}.active_f012zbias_mapper`, gate.active_f012zbias_mapper, mapperChoices)}${checkboxField("XY 使用 pi Setting", `${path}.xy_pi_impl`, gate.xy_pi_impl)}<div class="readonly-line">Z gate：${esc(gate.z_gate_impl || "VIRTUAL")}</div></div>`;
}

function couplerGateEditor(gate, settings, mappers) {
  const path = "calibration_values.gate_configuration.C";
  const settingChoices = (type) => Object.entries(settings).filter(([, row]) => row.target === "C" && row.gate_type === type).map(([id]) => id);
  const mapperChoices = Object.entries(mappers).filter(([, row]) => row.target === "C" && row.mapper_type === "G2ZBIAS_MAPPER").map(([id]) => id);
  return `<div class="form-cluster compact-form"><div class="editor-grid">${selectField("CZ Setting", `${path}.active_cz_setting`, gate.active_cz_setting, settingChoices("CZ"))}${selectField("FSIM Setting", `${path}.active_fsim_setting`, gate.active_fsim_setting, settingChoices("FSIM"))}${selectField("G2 Mapper", `${path}.active_g2zbias_mapper`, gate.active_g2zbias_mapper, mapperChoices)}</div></div>`;
}

function mapperObjectGroups(mappers, type, target) {
  const rows = Object.entries(mappers).filter(([, row]) => row.mapper_type === type && row.target === target);
  return rows.length ? `<div class="parameter-folders">${rows.map(([id, row], index) => mapperEditor(id, row, index === 0)).join("")}</div>` : uncalibrated(`未校准：尚无 ${type} 记录`);
}

function referenceEvidence(reference) {
  if (!reference) return uncalibrated("暂无参考频率证据");
  return `<div class="facts">${fact("来源", statusText(reference.frequency_source || "-"))}${fact("校准运行", reference.calibration_run_id || "未发布", true)}${fact("基础修订", reference.base_revision ?? "-")}${fact("设置哈希", short(reference.base_setting_hash || reference.setting_hash), true)}</div>`;
}

function readonlyWorkbench(item, section) {
  const editable = item.editable || {}, calibration = editable.calibration_values || {};
  if (section === "overview") return `<section class="workbench-section"><div class="section-head"><div><h3>设备概览</h3><p>已发布快照。所有值均只读。</p></div></div>${topologyView(calibration)}<div class="facts">${fact("设备", item.device_id)}${fact("发布者", item.actor_id)}${fact("发布时间", dateText(item.published_utc))}${fact("内容哈希", short(item.content_sha256), true)}</div>${readonlyEditor(item)}</section>`;
  if (section === "control") return readonlyControlWorkbench(editable.control_values || {});
  if (section === "q1" || section === "q2") return readonlyQubit(section.toUpperCase(), calibration, editable.control_values || {});
  if (section === "c") return readonlyCoupler(calibration, editable.control_values || {});
  return `<section class="workbench-section"><div class="section-head"><div><h3>变更与发布</h3><p>快照血缘、准入与权威依据。</p></div></div><div class="facts">${fact("父配置", short(item.parent?.configuration_id), true)}${fact("重新准入", item.requires_requalification ? "需要" : "不需要")}${fact("实验准入", item.experiment_eligible ? "可用" : "不可用")}${fact("长期保存", item.keep ? "是" : "否")}</div>${readonlyEditor(item)}</section>`;
}

function readonlyQubit(target, calibration, control) {
  const registry = calibration.waveform_registry || {}, settings = registry.settings || {}, mappers = registry.mappers || {};
  const value = calibration.qagents?.[target], gate = calibration.gate_configuration?.[target];
  const key = target.toLowerCase(), tab = state.objectTabs[key] || "base";
  const content = tab === "base" ? readonlySurface(`<div class="setting-grid">${objectIdleFluxEditor(target, control)}${referenceEditor(target, value)}${gate ? qubitGateEditor(target, gate, settings, mappers) : uncalibrated("未校准：无 Gate Configuration")}</div>`) : tab === "pulses" ? readonlySurface(settingGroups(settings, [target], false)) : tab === "mapper" ? readonlySurface(mapperObjectGroups(mappers, "F012ZBIAS_MAPPER", target)) : referenceEvidence(value?.reference_frequency_authority);
  return `<section class="workbench-section object-page ${key}"><div class="object-heading"><span class="object-dot ${key}"></span><div><h3>${target}</h3><p>只读频率、脉冲 Setting、F012ZBIAS Mapper 与校准证据</p></div></div>${objectTabs(key, tab, [["base", "基础"], ["pulses", "脉冲"], ["mapper", "Mapper"], ["evidence", "证据"]])}<div class="object-subsection">${content}</div></section>`;
}

function readonlyCoupler(calibration, control) {
  const registry = calibration.waveform_registry || {}, settings = registry.settings || {}, mappers = registry.mappers || {};
  const gate = calibration.gate_configuration?.C, tab = state.objectTabs.c || "gates";
  const content = tab === "gates" ? readonlySurface(`${objectIdleFluxEditor("C", control)}${gate ? couplerGateEditor(gate, settings, mappers) : uncalibrated("未校准：无耦合器 Gate Configuration")}${settingGroups(settings, ["C"], true)}`) : tab === "mapper" ? readonlySurface(mapperObjectGroups(mappers, "G2ZBIAS_MAPPER", "C")) : characterizationView(calibration.fsim_characterizations);
  return `<section class="workbench-section object-page c"><div class="object-heading"><span class="object-dot c"></span><div><h3>C</h3><p>只读 CZ / FSIM 波形、G2ZBIAS Mapper 与标定结果</p></div></div>${objectTabs("c", tab, [["gates", "CZ / FSIM"], ["mapper", "G2 Mapper"], ["results", "标定结果"]])}<div class="object-subsection">${content}</div></section>`;
}

function readonlyControlWorkbench(control) {
  const tab = state.objectTabs.control || "clock";
  const content = tab === "clock" ? controlClockEditor(control) : tab === "lanes" ? laneEditor(control) : tab === "mixing" ? Object.entries(control.static_mixing || {}).map(([name, value]) => matrixEditor(name, value)).join("") : tab === "simulation" ? simulationModelEditor(control) : acceptanceEditor(control);
  return `<section class="workbench-section"><div class="object-heading"><span class="object-dot control"></span><div><h3>控制链</h3><p>只读时钟、电子学链、仿真截断与准入阈值</p></div></div>${objectTabs("control", tab, [["clock", "时钟与 DAC"], ["lanes", "通道"], ["mixing", "混合"], ["simulation", "仿真截断"], ["acceptance", "阈值"]])}<div class="object-subsection">${readonlySurface(content || uncalibrated("未提供控制链数据"))}</div></section>`;
}

function readonlySurface(content) { return `<fieldset class="readonly-control-surface" disabled>${content}</fieldset>`; }

function controlEditor(control) {
  const clock = control.clock || {}, dac = control.dac || {}, flux = control.idle_flux_phi0 || {};
  const acceptance = control.acceptance || {};
  return `<div class="config-group"><h3>时钟、DAC 与空闲磁通</h3><div class="editor-grid">
    ${numberField("采样率（Hz）", "control_values.clock.sample_rate_Hz", clock.sample_rate_Hz)}${numberField("采样间隔（ns）", "control_values.clock.dt_ns", clock.dt_ns, "0.000001")}
    ${numberField("DAC 位数", "control_values.dac.bits", dac.bits, "1")} ${selectField("DAC 舍入模式", "control_values.dac.rounding", dac.rounding, ["half_even"])}
    ${numberField("DAC 满量程下限（V）", "control_values.dac.full_scale_min_V", dac.full_scale_min_V)}${numberField("DAC 满量程上限（V，开区间）", "control_values.dac.full_scale_max_exclusive_V", dac.full_scale_max_exclusive_V)}
    ${["q1", "q2", "c"].map((name) => numberField(`${name.toUpperCase()} 空闲磁通（Phi/Phi0）`, `control_values.idle_flux_phi0.${name}`, flux[name], "0.000001")).join("")}
  </div></div>
  <div class="config-group"><h3>电子学通道</h3>${laneEditor(control)}</div>
  <div class="config-group"><h3>静态混合矩阵</h3>${Object.entries(control.static_mixing || {}).map(([name, value]) => matrixEditor(name, value)).join("") || uncalibrated("未提供混合矩阵")}</div>
  <div class="config-group"><h3>仿真截断</h3>${simulationModelEditor(control)}</div>
  <div class="config-group"><h3>准入阈值</h3><div class="editor-grid">${Object.keys(acceptance).map((key) => numberField(parameterLabel(key), `control_values.acceptance.${key}`, acceptance[key], key === "max_formal_samples_per_scenario" ? "1" : "any")).join("") || uncalibrated("未提供准入阈值")}</div></div>`;
}

function laneEditor(control) {
  const lanes = control.lanes || {}, order = control.lane_order || Object.keys(lanes);
  return `<div class="parameter-folders">${order.map((lane, index) => {
    const row = lanes[lane] || {};
    return `<details class="parameter-folder" ${index === 0 ? "open" : ""}><summary><span><strong class="mono">${esc(lane)}</strong><small>电子学通道 · ${row.latency_samples ?? "-"} samples 延迟</small></span></summary><div class="folder-body">${numberField("通道延迟（samples）", `control_values.lanes.${lane}.latency_samples`, row.latency_samples, "1")}${arrayField(`control_values.lanes.${lane}.fir`, row.fir)}</div></details>`;
  }).join("")}</div>`;
}

function matrixEditor(name, value) {
  const inputs = value.input_lanes || [], outputs = value.output_coordinates || [], matrix = value.matrix || [];
  const fields = outputs.flatMap((output, row) => inputs.map((lane, column) => numberField(`${output} ← ${lane}`, `control_values.static_mixing.${name}.matrix.${row}.${column}`, matrix[row]?.[column]))).join("");
  return `<details class="parameter-folder matrix-folder"><summary><span><strong>${esc(name.toUpperCase())} 混合矩阵</strong><small>${outputs.length} × ${inputs.length} · 浮点数</small></span></summary><div class="folder-body"><p class="folder-description">描述输入电子学通道到物理输出坐标的静态线性映射。</p>${fields}</div></details>`;
}

function calibrationEditor(calibration) {
  if (isEmptyCalibration(calibration)) {
    return `<div class="config-group calibration-bootstrap"><h3>校准参数</h3><p class="muted">当前草稿尚未初始化校准记录。初始化后将提供 Q1/Q2、Setting、Mapper、CZ/FSIM 的结构化表单。</p><button id="initialize-calibration" type="button" class="button primary">初始化校准参数</button></div>`;
  }
  const registry = calibration.waveform_registry || {};
  const settings = registry.settings || {}, mappers = registry.mappers || {};
  const qagents = calibration.qagents || {};
  const gates = calibration.gate_configuration || {};
  const qagentContent = ["Q1", "Q2"].map((target) => referenceEditor(target, qagents[target])).join("");
  return `<div class="config-group"><h3>Q1 / Q2 参考频率</h3><div class="editor-grid">${qagentContent || uncalibrated("未校准：尚无参考频率权威记录")}</div></div>
  <div class="config-group"><h3>Gate Configuration</h3>${gateConfigurationEditor(gates, settings, mappers)}</div>
  <div class="config-group"><h3>单比特 Setting（XY / XY2 / X12 / DTN）</h3>${settingGroups(settings, ["Q1", "Q2"], false)}</div>
  <div class="config-group"><h3>F012ZBIAS Mapper</h3>${mapperGroups(mappers, "F012ZBIAS_MAPPER")}</div>
  <div class="config-group"><h3>CZ / FSIM Setting</h3>${settingGroups(settings, ["C"], true)}</div>
  <div class="config-group"><h3>G2ZBIAS Mapper</h3>${mapperGroups(mappers, "G2ZBIAS_MAPPER")}</div>
  <div class="config-group"><h3>FSIM Characterization（只读实验产物）</h3>${characterizationView(calibration.fsim_characterizations)}</div>`;
}

function isEmptyCalibration(calibration) {
  return !calibration || (typeof calibration === "object" && Object.keys(calibration).length === 0);
}

function referenceEditor(target, value) {
  const reference = value?.reference_frequency_authority;
  if (!reference) return `<div class="readonly-block"><strong>${target}</strong>${status("uninitialized")}<span class="muted">未校准：尚无参考频率权威记录</span></div>`;
  const path = `calibration_values.qagents.${target}.reference_frequency_authority`;
  return `<div class="form-cluster"><h4>${target}</h4>${numberField("参考频率（GHz）", `${path}.reference_frequency_GHz`, reference.reference_frequency_GHz, "0.000001")}
    ${recordFacts(reference, ["frequency_source", "status", "base_revision", "base_setting_hash", "revision", "setting_hash", "calibration_run_id"])}</div>`;
}

function gateConfigurationEditor(gates, settings, mappers) {
  const availableSettings = Object.entries(settings), availableMappers = Object.entries(mappers);
  return `<div class="setting-grid">${["Q1", "Q2"].map((target) => {
    const gate = gates[target];
    if (!gate) return `<div class="readonly-block"><strong>${target}</strong>${status("uninitialized")}<span class="muted">未校准：尚无 Gate Configuration</span></div>`;
    const path = `calibration_values.gate_configuration.${target}`;
    const choices = (key, type) => availableSettings.filter(([, row]) => row.target === target && row.gate_type === type).map(([id]) => id);
    const mapperChoices = availableMappers.filter(([, row]) => row.target === target && row.mapper_type === "F012ZBIAS_MAPPER").map(([id]) => id);
    return `<div class="form-cluster"><h4>${target}</h4>${selectField("XY Setting", `${path}.active_xy_setting`, gate.active_xy_setting, choices("xy", "XY"))}${selectField("XY2 Setting", `${path}.active_xy2_setting`, gate.active_xy2_setting, choices("xy2", "XY2"))}${selectField("X12 Setting", `${path}.active_xy12_setting`, gate.active_xy12_setting, choices("x12", "X12"))}${selectField("DTN Setting", `${path}.active_detune_setting`, gate.active_detune_setting, choices("dtn", "DTN"))}${selectField("F012 Mapper", `${path}.active_f012zbias_mapper`, gate.active_f012zbias_mapper, mapperChoices)}${checkboxField("XY 使用 pi Setting", `${path}.xy_pi_impl`, gate.xy_pi_impl)}<div class="readonly-line">Z gate：${esc(gate.z_gate_impl || "VIRTUAL")}</div></div>`;
  }).join("")}${(() => { const gate = gates.C; if (!gate) return `<div class="readonly-block"><strong>C</strong>${status("uninitialized")}<span class="muted">未校准：尚无耦合器 Gate Configuration</span></div>`; const path = "calibration_values.gate_configuration.C"; const choices = (type) => availableSettings.filter(([, row]) => row.target === "C" && row.gate_type === type).map(([id]) => id); const mapperChoices = availableMappers.filter(([, row]) => row.target === "C" && row.mapper_type === "G2ZBIAS_MAPPER").map(([id]) => id); return `<div class="form-cluster"><h4>C</h4>${selectField("CZ Setting", `${path}.active_cz_setting`, gate.active_cz_setting, choices("CZ"))}${selectField("FSIM Setting", `${path}.active_fsim_setting`, gate.active_fsim_setting, choices("FSIM"))}${selectField("G2 Mapper", `${path}.active_g2zbias_mapper`, gate.active_g2zbias_mapper, mapperChoices)}</div>`; })()}</div>`;
}

function settingGroups(settings, targets, composite) {
  const order = composite ? ["CZ", "FSIM"] : ["XY", "XY2", "X12", "DTN"];
  const rows = Object.entries(settings)
    .filter(([, row]) => targets.includes(row.target) && order.includes(row.gate_type))
    .sort((left, right) => order.indexOf(left[1].gate_type) - order.indexOf(right[1].gate_type));
  if (!rows.length) return uncalibrated("未校准：尚无匹配的 Setting 记录");
  return `<div class="parameter-folders">${rows.map(([id, record], index) => composite ? compositeSettingEditor(id, record, index === 0) : qubitSettingEditor(id, record, index === 0)).join("")}</div>`;
}

function qubitSettingEditor(id, record, expanded = false) {
  const path = `calibration_values.waveform_registry.settings.${id}`;
  const heading = `<span><strong>${esc(id)}</strong><small>${esc(record.gate_type)} · ${esc(record.transition || record.control_role || "波形")}</small></span>${waveformPreview(record.waveform_class)}`;
  if (record.gate_type === "DTN") return `<details class="parameter-folder" ${expanded ? "open" : ""}><summary>${heading}</summary><div class="folder-body">${recordFacts(record)}<div class="readonly-line">Z 控制通道；矩形包络；输入单位 Phi/Phi0。DTN 的实际幅度由调用指令提供。</div></div></details>`;
  return `<details class="parameter-folder" ${expanded ? "open" : ""}><summary>${heading}</summary><div class="folder-body">${recordFacts(record)}<div class="editor-grid compact">${selectField("波形类别", `${path}.waveform_class`, record.waveform_class, ["rectangle", "gaussian", "flattop"])}${numberField("长度（samples）", `${path}.length_samples`, record.length_samples, "1")}${numberField("幅度（GHz）", `${path}.amplitude_GHz`, record.amplitude_GHz)}${numberField("相位偏移（rad）", `${path}.phase_offset_rad`, record.phase_offset_rad)}${numberField("DRAG alpha（samples）", `${path}.dragAlpha_samples`, record.dragAlpha_samples)}${shapeFields(path, record)}</div></div></details>`;
}

function compositeSettingEditor(id, record, expanded = false) {
  const path = `calibration_values.waveform_registry.settings.${id}`;
  return `<details class="parameter-folder composite-folder" ${expanded ? "open" : ""}><summary><span><strong>${esc(id)}</strong><small>${esc(record.gate_type)} · 组合波形</small></span><span class="folder-summary-meta">${record.duration_samples} samples</span></summary><div class="folder-body">${recordFacts(record)}<div class="editor-grid compact">${numberField("公共时长（samples）", `${path}.duration_samples`, record.duration_samples, "1")}${checkboxField("使用 F012 Mapper", `${path}.use_f012zbias_mapper`, record.use_f012zbias_mapper)}${checkboxField("使用 G2 Mapper", `${path}.use_g2zbias_mapper`, record.use_g2zbias_mapper)}${numberField("Q0 动态相位（rad）", `${path}.q0_calibrated_dynamic_phase_rad`, record.q0_calibrated_dynamic_phase_rad)}${numberField("Q1 动态相位（rad）", `${path}.q1_calibrated_dynamic_phase_rad`, record.q1_calibrated_dynamic_phase_rad)}</div><div class="waveform-stack">${["q0", "q1", "coupler"].map((name) => compositeWaveformEditor(path, name, record.waveforms?.[name] || {}, record)).join("")}</div></div></details>`;
}

function compositeWaveformEditor(path, name, waveform, record) {
  const isCoupler = name === "coupler";
  const useMapper = isCoupler ? record.use_g2zbias_mapper : record.use_f012zbias_mapper;
  const operand = isCoupler ? "coupling_detune_GHz" : "frequency_detune_GHz";
  const label = useMapper ? `${operand.replace("_", " ")}（GHz）` : "直接 Z 偏置（flux_offset_phi0，Phi/Phi0）";
  const title = name === "q0" ? "Q0 比特波形" : name === "q1" ? "Q1 比特波形" : "Coupler 耦合器波形";
  return `<details class="waveform-folder"><summary><span><strong>${title}</strong><small>${esc(waveform.waveform_class || "未设置")}</small></span>${waveformPreview(waveform.waveform_class)}</summary><div class="folder-body">${selectField("波形类别", `${path}.waveforms.${name}.waveform_class`, waveform.waveform_class, ["rectangle", "gaussian", "flattop", "acz"])}${numberField(label, `${path}.waveforms.${name}.${useMapper ? operand : "flux_offset_phi0"}`, waveform[useMapper ? operand : "flux_offset_phi0"])}${shapeFields(`${path}.waveforms.${name}`, waveform)}</div></details>`;
}

function shapeFields(path, value) {
  if (value.waveform_class === "rectangle") return numberField("宽度（samples）", `${path}.width_samples`, value.width_samples, "1");
  if (value.waveform_class === "gaussian") return numberField("r sigma（samples）", `${path}.r_sigma_samples`, value.r_sigma_samples);
  if (value.waveform_class === "flattop") return numberField("边缘（samples）", `${path}.edge_samples`, value.edge_samples, "1");
  if (value.waveform_class === "acz") return ["thf", "thi", "lam2", "lam3"].map((key) => numberField(`ACZ ${key}`, `${path}.parameters.${key}`, value.parameters?.[key])).join("");
  return "";
}

function waveformPreview(kind) {
  const paths = { rectangle: "M2 21 L14 21 L14 7 L66 7 L66 21 L78 21", gaussian: "M2 21 C18 21 20 7 40 7 C60 7 62 21 78 21", flattop: "M2 21 C13 21 15 8 25 8 L55 8 C65 8 67 21 78 21", acz: "M2 21 C16 21 16 10 28 12 C38 15 42 4 52 9 C62 13 65 21 78 21" };
  return `<svg class="wave-preview" viewBox="0 0 80 28" aria-label="${esc(kind || "unknown")} 波形预览" role="img"><path d="M2 21 H78" class="axis"/><path d="${paths[kind] || paths.rectangle}" class="signal"/></svg>`;
}

function mapperGroups(mappers, type) {
  const rows = Object.entries(mappers).filter(([, row]) => row.mapper_type === type);
  if (!rows.length) return uncalibrated(`未校准：尚无 ${type} 记录`);
  return `<div class="setting-grid">${rows.map(([id, mapper]) => mapperEditor(id, mapper)).join("")}</div>`;
}

function mapperEditor(id, mapper, expanded = false) {
  const path = `calibration_values.waveform_registry.mappers.${id}`;
  if (mapper.mapper_type === "F012ZBIAS_MAPPER") return `<details class="parameter-folder" ${expanded ? "open" : ""}><summary><span><strong>${esc(id)}</strong><small>${esc(mapper.target)} · F012ZBIAS Mapper</small></span></summary><div class="folder-body">${recordFacts(mapper)}${numberField("f01 max（GHz）", `${path}.f01max_GHz`, mapper.f01max_GHz)}${numberField("k（rad/Phi0）", `${path}.k_rad_per_phi0`, mapper.k_rad_per_phi0)}${numberField("空闲磁通偏置（Phi/Phi0）", `${path}.idle_flux_offset_phi0`, mapper.idle_flux_offset_phi0)}</div></details>`;
  const xs = mapper.coupling_detune_GHz || [], ys = mapper.zbias_offset_phi0 || [];
  return `<details class="parameter-folder" ${expanded ? "open" : ""}><summary><span><strong>${esc(id)}</strong><small>C · G2ZBIAS Mapper · 两个等长 list</small></span>${mapperPreview(xs, ys)}</summary><div class="folder-body">${recordFacts(mapper)}<p class="folder-description">两个 list 按相同下标组成映射点，通过分段线性插值把耦合强度变化转换为相对当前偏置点的 Phi/Phi0；越界输入拒绝。</p>${numberListField("耦合失谐列表（GHz）", `${path}.coupling_detune_GHz`, xs)}${numberListField("Z 偏置列表（Phi/Phi0）", `${path}.zbias_offset_phi0`, ys)}</div></details>`;
}

function mapperPreview(xs, ys) {
  if (!xs.length || xs.length !== ys.length) return "";
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = (value, min, max, low, high) => max === min ? (low + high) / 2 : low + ((value - min) / (max - min)) * (high - low);
  const points = xs.map((x, index) => `${scale(x, minX, maxX, 4, 76).toFixed(1)},${scale(ys[index], minY, maxY, 23, 5).toFixed(1)}`).join(" ");
  return `<svg class="mapper-preview" viewBox="0 0 80 28" aria-label="G2ZBIAS Mapper 预览" role="img"><path d="M4 23 H76 M4 5 V23" class="axis"/><polyline points="${points}" class="signal"/></svg>`;
}

function characterizationView(rows) {
  const values = Object.values(rows || {});
  if (!values.length) return uncalibrated("暂无 FSIM characterization 实验产物");
  return `<div class="table-wrap matrix-scroll"><table><thead><tr><th>运行</th><th>theta</th><th>zeta</th><th>chi</th><th>gamma</th><th>phi</th><th>leakage</th><th>状态</th></tr></thead><tbody>${values.map((row) => `<tr><td class="mono">${esc(row.characterization_run_id)}</td><td>${fmt(row.theta_rad)}</td><td>${fmt(row.zeta_rad)}</td><td>${fmt(row.chi_rad)}</td><td>${fmt(row.gamma_rad)}</td><td>${fmt(row.phi_rad)}</td><td>${fmt(row.leakage)}</td><td>${status(row.status)}</td></tr>`).join("")}</tbody></table></div>`;
}

function readonlyEditor(item) {
  const readonly = item.readonly || item.editor_view?.readonly || {};
  return `<div class="config-group"><h3>设备与权威依据（只读）</h3><div class="facts">${fact("设备引用", readonly.device_ref?.path || "-", true)}${fact("设备哈希", short(readonly.device_ref?.sha256 || readonly.authority_refs?.device_sha256), true)}${fact("指令配置哈希", short(readonly.authority_refs?.instruction_profile_sha256), true)}${fact("编译器快照哈希", short(readonly.authority_refs?.compiler_snapshot_sha256), true)}</div><p class="muted">物理器件参数、authority references 与生成字段由后端管理，不可编辑。</p></div>`;
}

function recordFacts(record, keys = ["setting_id", "mapper_id", "target", "gate_type", "mapper_type", "transition", "status", "base_revision", "base_setting_hash", "revision", "setting_hash", "calibration_run_id"]) {
  const values = keys.filter((key) => record[key] !== undefined).map((key) => `<span>${esc(key)}: <strong>${esc(record[key] ?? "-")}</strong></span>`).join("");
  return values ? `<div class="record-facts">${values}</div>` : "";
}

function textField(label, id, value, description = "用于标识和说明当前配置。") {
  return fieldShell(label, id, "文本", description, `<input id="${esc(id)}" value="${esc(value ?? "")}">`);
}
function numberField(label, path, value, step = "any", mode = "") {
  const id = fieldId(path), accessibleLabel = label || normalizeFieldPath(path);
  const input = `<input id="${esc(id)}" aria-label="${esc(accessibleLabel)}" data-path="${esc(path)}" data-value-type="number" type="number" step="${esc(step)}" value="${esc(value ?? "")}">`;
  if (mode === "inline") return input;
  return fieldShell(label, id, step === "1" ? "整数" : "浮点数", parameterDescription(path, label), input);
}
function selectField(label, path, value, choices) {
  const id = fieldId(path), unique = [...new Set([value, ...(choices || [])].filter(Boolean))];
  const control = `<select id="${esc(id)}" aria-label="${esc(label || normalizeFieldPath(path))}" data-path="${esc(path)}" data-value-type="string">${unique.map((entry) => `<option value="${esc(entry)}" ${entry === value ? "selected" : ""}>${esc(entry)}</option>`).join("") || '<option value="">未校准</option>'}</select>`;
  return fieldShell(label, id, "枚举", parameterDescription(path, label), control);
}
function checkboxField(label, path, value) {
  const id = fieldId(path);
  const control = `<label class="switch-control" for="${esc(id)}"><input id="${esc(id)}" data-path="${esc(path)}" data-value-type="boolean" type="checkbox" ${value ? "checked" : ""}><span>${value ? "开启" : "关闭"}</span></label>`;
  return fieldShell(label, id, "布尔值", parameterDescription(path, label), control);
}
function arrayField(path, value) {
  return numberListField("FIR 系数", path, value);
}
function numberListField(label, path, value) {
  const id = fieldId(path);
  const control = `<input id="${esc(id)}" data-path="${esc(path)}" data-value-type="number-array" value="${esc((value || []).join(", "))}" aria-label="${esc(label)}" spellcheck="false">`;
  return fieldShell(label, id, "list[float]", parameterDescription(path, label), control);
}
function fieldShell(label, id, type, description, control) {
  return `<div class="field parameter-field"><div class="field-copy"><div class="field-title"><label for="${esc(id)}">${esc(label)}</label><span class="data-type">${esc(type)}</span></div><small>${esc(description)}</small></div><div class="field-control">${control}</div></div>`;
}
function parameterDescription(path, label) {
  const text = String(path || "");
  const rules = [
    ["sample_rate_Hz", "控制波形的数字采样率；必须与采样间隔互为倒数。"],
    ["dt_ns", "相邻数字样点之间的时间间隔，单位 ns。"],
    ["dac.bits", "DAC 的有效量化位数，决定输出幅度分辨率。"],
    ["dac.rounding", "浮点波形量化到 DAC 码值时采用的舍入规则。"],
    ["full_scale_min_V", "DAC 可输出电压范围的闭区间下限。"],
    ["full_scale_max_exclusive_V", "DAC 可输出电压范围的开区间上限。"],
    ["idle_flux_phi0", "器件空闲工作点的磁通偏置，统一使用 Phi/Phi0。"],
    ["reference_frequency_GHz", "当前配置用于相位计算和指令编译的比特参考频率。"],
    ["active_", "选择当前门操作实际引用的 Setting 或 Mapper。"],
    ["xy_pi_impl", "关闭时 X/Y 门由两个同向半角脉冲连续拼接实现。"],
    ["waveform_class", "选择波形包络类型，并决定下方出现的形状参数。"],
    ["length_samples", "单比特脉冲的总采样点数。"],
    ["duration_samples", "组合门中 Q0、Q1 与 Coupler 三路波形共享的时长。"],
    ["amplitude_GHz", "驱动包络幅度，以等效频率 GHz 表示。"],
    ["phase_offset_rad", "叠加到驱动载波的固定相位偏移。"],
    ["dragAlpha_samples", "DRAG 正交分量的导数权重，用于抑制泄漏。"],
    ["width_samples", "矩形包络的有效宽度。"],
    ["r_sigma_samples", "高斯包络截断半径，以标准差对应的采样点数表示。"],
    ["edge_samples", "Flattop 波形单侧平滑边缘的采样点数。"],
    ["use_f012zbias_mapper", "开启后以频率失谐输入，再由比特 F012 Mapper 转换为磁通偏置。"],
    ["use_g2zbias_mapper", "开启后以耦合强度失谐输入，再由 Coupler G2 Mapper 转换为磁通偏置。"],
    ["dynamic_phase_rad", "实验标定得到并在门后补偿的动力学相位。"],
    ["frequency_detune_GHz", "相对当前参考频率的目标频率变化量。"],
    ["coupling_detune_GHz", "相对当前耦合工作点的目标耦合强度变化量。"],
    ["zbias_offset_phi0", "与耦合失谐列表按下标对应的相对 Z 偏置，单位 Phi/Phi0。"],
    ["flux_offset_phi0", "相对当前空闲偏置叠加的磁通变化量。"],
    ["parameters.thf", "ACZ 绝热轨迹的高频端形状参数 thf。"],
    ["parameters.thi", "ACZ 绝热轨迹的初始端形状参数 thi。"],
    ["parameters.lam2", "ACZ 轨迹二阶修正系数。"],
    ["parameters.lam3", "ACZ 轨迹三阶修正系数。"],
    ["f01max_GHz", "比特在零磁通附近的最大 f01 频率。"],
    ["k_rad_per_phi0", "磁通到 SQUID 相位的比例系数。"],
    ["idle_flux_offset_phi0", "F012 模型相对于当前偏置点的磁通零点。"],
    ["latency_samples", "该电子学通道相对统一时钟的离散延迟。"],
    [".fir", "电子学链的 FIR 系数；所有系数之和必须为 1。"],
    [".matrix.", "输入通道到物理输出坐标的静态线性混合系数。"],
    ["acceptance", "Stage 4.1 对控制链数值结果的准入阈值。"],
    ["convergence_charge_cutoffs", "收敛对照使用的电荷基截断，不得低于基线。"],
    ["convergence_retained_energy_levels", "收敛对照保留的低能态数量，不得低于基线。"],
    ["charge_cutoffs", "电荷基截断 N；每个模态实际保留 2N+1 个电荷态。"],
    ["retained_energy_levels", "从高阶电荷基模型中投影保留的低能本征态数量。"],
  ];
  return rules.find(([token]) => text.includes(token))?.[1] || `用于配置“${label || normalizeFieldPath(path)}”的运行参数。`;
}
function fieldId(path) { return `field-${String(path).replace(/[^a-zA-Z0-9_-]/g, "-")}`; }
function uncalibrated(text) { return `<div class="uncalibrated">${status("uninitialized")}<span>${esc(text)}</span></div>`; }

function controlConfigurationView(control) {
  if (!control?.clock || !control?.dac) return `<section class="section"><div class="section-head"><div><h2>控制参数</h2></div></div>${empty("当前配置未包含控制参数")}</section>`;
  const dac = control.dac;
  return `<section class="section">
    <div class="section-head"><div><h2>控制参数</h2><p>时钟、DAC、偏置与电子学链</p></div></div>
    <div class="facts">
      ${fact("采样率", `${control.clock.sample_rate_Hz} Hz`, true)}
      ${fact("采样间隔", `${control.clock.dt_ns} ns`)}
      ${fact("DAC 位数", dac.bits)}
      ${fact("DAC 满量程", `[${dac.full_scale_min_V}, ${dac.full_scale_max_exclusive_V}) V`, true)}
      ${fact("Q1 空闲磁通", `${control.idle_flux_phi0.q1} Phi/Phi0`)}
      ${fact("Q2 空闲磁通", `${control.idle_flux_phi0.q2} Phi/Phi0`)}
      ${fact("C 空闲磁通", `${control.idle_flux_phi0.c} Phi/Phi0`)}
      ${fact("DAC 舍入", dac.rounding, true)}
    </div>
    <div class="config-block"><h3>仿真截断</h3>${simulationModelEditor(control)}</div>
    <div class="config-block"><h3>通道顺序</h3><p class="mono config-sequence">${esc(control.lane_order.join(" -> "))}</p></div>
    <div class="config-block"><h3>电子学通道</h3>${readonlyLaneTable(control)}</div>
    <div class="config-block"><h3>静态混合矩阵</h3>${Object.entries(control.static_mixing || {}).map(([name, value]) => mixingMatrixView(name, value)).join("")}</div>
    <div class="config-block"><h3>准入阈值</h3>${keyValueTable(control.acceptance || {})}</div>
  </section>`;
}

function readonlyLaneTable(control) {
  return `<div class="table-wrap"><table><thead><tr><th>通道</th><th>延迟（samples）</th><th>FIR</th></tr></thead><tbody>${control.lane_order.map((lane) => {
    const row = control.lanes[lane];
    return `<tr><td class="mono">${esc(lane)}</td><td class="numeric">${row.latency_samples}</td><td class="mono">[${esc(row.fir.join(", "))}]</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function mixingMatrixView(name, value) {
  const rows = (value.matrix || []).map((row, index) => `<tr><td class="mono">${esc(value.output_coordinates?.[index] || index)}</td>${row.map((cell) => `<td class="numeric">${fmt(cell, 6)}</td>`).join("")}</tr>`).join("");
  return `<div class="matrix-block"><h4>${esc(name.toUpperCase())}</h4><div class="table-wrap"><table class="matrix-table"><thead><tr><th>输出 / 输入</th>${(value.input_lanes || []).map((lane) => `<th class="mono">${esc(lane)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function keyValueTable(value) {
  const rows = Object.entries(value).map(([key, item]) => `<tr><td>${esc(parameterLabel(key))}</td><td class="mono">${esc(item)}</td></tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr><th>参数</th><th>数值</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function calibrationConfigurationView(calibration) {
  const item = { editable: { calibration_values: calibration || {} } };
  const frequencies = platformFrequencyRows(item);
  return `<section class="section">
    <div class="section-head"><div><h2>校准参数</h2><p>实验可更新的控制模型参数</p></div></div>
    <div class="config-block"><h3>参考频率</h3>${frequencyTable(frequencies)}</div>
    ${calibrationReadonlyGroups(calibration || {})}
    <details open><summary>完整校准参数 JSON</summary><pre>${esc(JSON.stringify(calibration || {}, null, 2))}</pre></details>
  </section>`;
}

function calibrationReadonlyGroups(calibration) {
  const settings = calibration.waveform_registry?.settings || {};
  const mappers = calibration.waveform_registry?.mappers || {};
  const gates = calibration.gate_configuration || {};
  const settingRows = Object.entries(settings).map(([id, row]) => `<tr><td class="mono">${esc(id)}</td><td>${esc(row.target || "-")}</td><td>${esc(row.gate_type || "-")}</td><td>${status(row.status)}</td><td>${row.revision ?? "-"}</td></tr>`).join("");
  const mapperRows = Object.entries(mappers).map(([id, row]) => `<tr><td class="mono">${esc(id)}</td><td>${esc(row.target || "-")}</td><td>${esc(row.mapper_type || "-")}</td><td>${status(row.status)}</td><td>${row.revision ?? "-"}</td></tr>`).join("");
  return `<div class="config-block"><h3>Gate Configuration</h3>${Object.keys(gates).length ? `<div class="facts">${Object.entries(gates).map(([target, value]) => fact(target, Object.entries(value).filter(([, item]) => typeof item !== "boolean").map(([key, item]) => `${key}: ${item}`).join("; "), true)).join("")}</div>` : uncalibrated("未校准：无 Gate Configuration")}</div>
  <div class="config-block"><h3>Setting</h3>${settingRows ? `<div class="table-wrap"><table><thead><tr><th>ID</th><th>目标</th><th>类型</th><th>状态</th><th>修订</th></tr></thead><tbody>${settingRows}</tbody></table></div>` : uncalibrated("未校准：无 Setting")}</div>
  <div class="config-block"><h3>Mapper</h3>${mapperRows ? `<div class="table-wrap"><table><thead><tr><th>ID</th><th>目标</th><th>类型</th><th>状态</th><th>修订</th></tr></thead><tbody>${mapperRows}</tbody></table></div>` : uncalibrated("未校准：无 Mapper")}</div>
  <div class="config-block"><h3>FSIM Characterization</h3>${characterizationView(calibration.fsim_characterizations)}</div>`;
}

function markDraftDirty(event) {
  state.draftDirty = true;
  state.draftEditVersion += 1;
  clearTimeout(state.draftSaveTimer);
  const saveState = document.querySelector(".save-state");
  if (saveState) saveState.textContent = "有未保存修改";
  document.querySelector("#validate-draft")?.setAttribute("disabled", "");
  document.querySelector("#publish-draft")?.setAttribute("disabled", "");
  document.querySelector("#snapshot-current")?.setAttribute("disabled", "");
  if (event?.target?.dataset?.path?.startsWith("control_values.simulation.calibration_model.")) {
    updateSimulationDimensions();
  }
  if (state.detail?.artifact_type !== "platform_configuration_current") {
    state.draftSaveTimer = setTimeout(() => persistDraft({ automatic: true }), 700);
  }
}

function updateSimulationDimensions() {
  const modes = ["q1", "c", "q2"];
  const values = (field) => modes.map((mode) => Number(document.querySelector(`[data-path="control_values.simulation.calibration_model.${field}.${mode}"]`)?.value));
  const update = (selector, text) => {
    document.querySelectorAll(selector).forEach((node) => { node.textContent = text; });
  };
  const baseline = values("retained_energy_levels");
  const convergence = values("convergence_retained_energy_levels");
  const dimension = (items) => items.every((value) => Number.isInteger(value) && value > 0) ? items.reduce((product, value) => product * value, 1) : "-";
  update('[data-simulation-summary="baseline-dimension"]', `${dimension(baseline)} 维`);
  update('[data-simulation-summary="convergence-dimension"]', `${dimension(convergence)} 维`);
  update('[data-simulation-summary="baseline-tensor"]', baseline.every(Number.isFinite) ? baseline.join(" × ") : "-");
  update('[data-simulation-field="retained_energy_levels"]', `${dimension(baseline)} 维`);
  update('[data-simulation-field="convergence_retained_energy_levels"]', `${dimension(convergence)} 维`);
}

function collectDraft(item) {
  const editable = structuredClone(editorEditable(item));
  const nameInput = document.querySelector("#draft-name");
  const noteInput = document.querySelector("#draft-note");
  const name = nameInput ? nameInput.value.trim() : item.name;
  const note = noteInput ? noteInput.value.trim() : item.note;
  document.querySelectorAll("[data-path]").forEach((input) => {
    const type = input.dataset.valueType;
    const value = type === "boolean" ? input.checked : type === "number" ? Number(input.value) : type === "number-array" ? parseNumberList(input.value, input.getAttribute("aria-label")) : input.value;
    setPath(editable, input.dataset.path, value);
  });
  return { editable, name, note };
}

function parseNumberList(text, label = "浮点数组") {
  const tokens = String(text).split(",").map((entry) => entry.trim());
  const values = tokens.map(Number);
  if (!tokens.length || tokens.some((entry) => !entry) || values.some((entry) => !Number.isFinite(entry))) {
    throw new Error(`${label} 必须是使用逗号分隔的有限数值列表`);
  }
  return values;
}

function editorEditable(item) {
  return item.editor_view?.editable || item.editable || { control_values: {}, calibration_values: {} };
}

function editorCapabilities(item) {
  const supplied = item.editor_view?.capabilities || item.capabilities || {};
  return {
    canSave: supplied.can_save !== false,
    canValidate: supplied.can_validate !== false,
    canPublish: supplied.can_publish !== false,
    canActivate: supplied.can_activate !== false,
  };
}

function setPath(value, path, next) {
  const segments = path.split(".");
  let cursor = value;
  for (let index = 0; index < segments.length - 1; index += 1) {
    const key = segments[index];
    if (cursor[key] === undefined || cursor[key] === null) cursor[key] = /^\d+$/.test(segments[index + 1]) ? [] : {};
    cursor = cursor[key];
  }
  cursor[segments.at(-1)] = next;
}

function bindStructuredEditor() {
  document.querySelectorAll("[data-path]").forEach((input) => input.addEventListener("change", () => {
    if (input.dataset.path.endsWith("waveform_class")) {
      const draft = editorEditable(state.detail);
      const record = pathValue(draft, input.dataset.path.replace(/\.waveform_class$/, ""));
      ["width_samples", "r_sigma_samples", "edge_samples", "parameters"].forEach((key) => delete record[key]);
      if (input.value === "rectangle") record.width_samples = 1;
      if (input.value === "gaussian") record.r_sigma_samples = 1;
      if (input.value === "flattop") record.edge_samples = 0;
      if (input.value === "acz") record.parameters = { thf: 1, thi: 1, lam2: 0, lam3: 0 };
      markDraftDirty();
      persistDraft().then((saved) => { if (saved) renderActiveEditor(); });
    }
  }));
}

function renderActiveEditor() {
  if (state.detail?.artifact_type === "platform_configuration_current") {
    renderCurrentConfiguration(state.detail.device_id);
  } else if (state.detail?.draft_id) {
    renderDraft(state.detail.draft_id);
  }
}

function pathValue(value, path) {
  return path.split(".").reduce((cursor, key) => cursor?.[key], value);
}

function showFieldErrors(rows) {
  const target = document.querySelector("#field-errors");
  if (!target) return;
  target.hidden = !rows?.length;
  target.innerHTML = (rows || []).map((row) => `<div><strong>${esc(normalizeFieldPath(row.path || row.field || "配置"))}</strong> ${esc(row.message || row.code || "校验失败")}</div>`).join("");
  document.querySelectorAll("[data-path]").forEach((input) => input.removeAttribute("aria-invalid"));
  (rows || []).forEach((row) => {
    const path = normalizeFieldPath(row.path || row.field || "");
    document.querySelector(`[data-path="${CSS.escape(path)}"]`)?.setAttribute("aria-invalid", "true");
  });
}

function normalizeFieldPath(path) {
  return String(path || "").replace(/^\$\.?/, "");
}

function showMutationError(error) {
  if (error.status === 409) {
    toast("配置已被更新，请刷新后处理冲突", true);
    const target = document.querySelector("#field-errors");
    if (target) {
      target.hidden = false;
      target.innerHTML = "<div><strong>保存冲突</strong> 配置已被服务器更新。请刷新页面后根据最新内容重新编辑。</div>";
    }
  }
  else toast(error.message, true);
  if (error.fieldErrors?.length) showFieldErrors(error.fieldErrors);
}

function persistDraft(options = {}) {
  state.draftSavePromise = state.draftSavePromise.then(
    () => persistDraftNow(options),
    () => persistDraftNow(options),
  );
  return state.draftSavePromise;
}

async function persistDraftNow({ automatic = false, notify = false } = {}) {
  clearTimeout(state.draftSaveTimer);
  if (!state.draftDirty) {
    if (notify) toast("当前内容已保存，无新增修改");
    return true;
  }
  const item = state.detail;
  const isCurrent = item.artifact_type === "platform_configuration_current";
  const editVersion = state.draftEditVersion;
  try {
    const { editable, name, note } = collectDraft(item);
    const endpoint = isCurrent
      ? `/api/v1/current-configurations/${encodeURIComponent(item.device_id)}`
      : `/api/v1/drafts/${item.draft_id}`;
    const result = await mutate(endpoint, { actor_id: actor(), expected_content_sha256: item.content_sha256, name, note, editable }, "PUT");
    state.detail = result;
    invalidateResources("management", "overview");
    state.draftDirty = state.draftEditVersion !== editVersion;
    const saveState = document.querySelector(".save-state");
    if (saveState) saveState.textContent = isCurrent ? `已保存并生效 / 修订 r${result.revision}` : `${automatic ? "已自动保存" : "已保存"} / 检查点 ${result.checkpoint}`;
    if (state.draftDirty && !isCurrent) {
      state.draftSaveTimer = setTimeout(() => persistDraft({ automatic: true }), 700);
    } else {
      document.querySelector("#validate-draft")?.removeAttribute("disabled");
    }
    document.querySelector("#publish-draft")?.setAttribute("disabled", "");
    if (isCurrent && result.validation?.status === "valid") document.querySelector("#snapshot-current")?.removeAttribute("disabled");
    if (isCurrent && result.validation?.field_errors?.length) showFieldErrors(result.validation.field_errors);
    if (notify) toast(isCurrent ? "当前配置已保存并生效" : "草稿已保存");
    return true;
  } catch (error) {
    const saveState = document.querySelector(".save-state");
    if (saveState) saveState.textContent = "保存失败";
    if (!automatic) showMutationError(error);
    return false;
  }
}

function renderExperiments(routeContext = null) {
  const selected = (value) => state.experimentFilters.status === value ? "selected" : "";
  app.innerHTML = `
    <section><div class="section-head"><div><h2>实验运行记录</h2><p>已发布的模型仿真证据</p></div></div>
      <div class="toolbar"><input id="experiment-search" type="search" maxlength="256" value="${esc(state.experimentFilters.q)}" placeholder="按目标、运行 ID 或工作流筛选"><select id="experiment-status"><option value="" ${selected("")}>全部状态</option><option value="data-only" ${selected("data-only")}>基础数据</option><option value="eligible" ${selected("eligible")}>可生成候选</option><option value="blocked" ${selected("blocked")}>已阻止</option><option value="invalid" ${selected("invalid")}>无效</option></select></div>
      <div id="experiment-table">${experimentTable(state.experiments)}</div>
      <div id="experiment-pagination">${experimentPagination()}</div>
    </section>`;
  document.querySelector("#experiment-search").addEventListener("input", () => {
    state.experimentFilters.q = document.querySelector("#experiment-search").value;
    scheduleExperimentFilter(routeContext);
  });
  document.querySelector("#experiment-status").addEventListener("change", () => {
    state.experimentFilters.status = document.querySelector("#experiment-status").value;
    scheduleExperimentFilter(routeContext);
  });
  bindExperimentRows();
  bindExperimentPagination(routeContext);
}

function scheduleExperimentFilter(routeContext) {
  cancelExperimentFilterWork();
  const pagination = document.querySelector("#load-more-experiments");
  if (pagination) pagination.disabled = true;
  state.experimentFilterTimer = setTimeout(() => {
    state.experimentFilterTimer = null;
    loadFilteredExperiments(routeContext);
  }, 250);
}

async function loadFilteredExperiments(routeContext) {
  const generation = ++state.experimentFilterGeneration;
  const filterKey = experimentListUrl();
  const controller = new AbortController();
  state.experimentFilterAbortController = controller;
  if (routeContext?.signal.aborted) return;
  routeContext?.signal.addEventListener("abort", () => controller.abort(), { once: true });
  const table = document.querySelector("#experiment-table");
  const pagination = document.querySelector("#experiment-pagination");
  if (table) table.innerHTML = `<div class="loading">加载中...</div>`;
  if (pagination) pagination.innerHTML = "";
  try {
    const page = await api(filterKey, { signal: controller.signal });
    if (
      controller.signal.aborted
      || generation !== state.experimentFilterGeneration
      || !routeIsCurrent(routeContext)
    ) return;
    applyExperimentPage(page);
    state.experimentAppliedFilterKey = filterKey;
    renderExperimentResults(routeContext);
  } catch (error) {
    if (controller.signal.aborted || generation !== state.experimentFilterGeneration || !routeIsCurrent(routeContext)) return;
    if (table) table.innerHTML = empty("实验筛选加载失败，请重试");
    toast(error.message, true);
  } finally {
    if (generation === state.experimentFilterGeneration) state.experimentFilterAbortController = null;
  }
}

function renderExperimentResults(routeContext) {
  const table = document.querySelector("#experiment-table");
  const pagination = document.querySelector("#experiment-pagination");
  if (table) table.innerHTML = experimentTable(state.experiments);
  if (pagination) pagination.innerHTML = experimentPagination();
  bindExperimentRows();
  bindExperimentPagination(routeContext);
}

function experimentPagination() {
  if (!state.experimentPage.hasMore) return "";
  return `<button id="load-more-experiments" type="button" class="button secondary" ${state.experimentPage.loadingMore ? "disabled" : ""}>${state.experimentPage.loadingMore ? "正在加载..." : "加载更多"}</button>`;
}

function bindExperimentPagination(routeContext) {
  const button = document.querySelector("#load-more-experiments");
  if (!button) return;
  button.addEventListener("click", async () => {
    if (state.experimentPage.loadingMore) return;
    const cursor = state.experimentPage.nextCursor;
    if (!cursor) {
      toast("实验分页游标缺失，请刷新后重试", true);
      return;
    }
    state.experimentPage.loadingMore = true;
    const loadGeneration = ++state.experimentLoadGeneration;
    button.disabled = true;
    button.textContent = "正在加载...";
    try {
      const page = await api(experimentListUrl(cursor), routeRequestOptions(routeContext));
      if (!routeIsCurrent(routeContext)) return;
      applyExperimentPage(page, { append: true });
      renderExperimentResults(routeContext);
    } catch (error) {
      if (!routeIsCurrent(routeContext) || error?.name === "AbortError") return;
      toast(error.message, true);
    } finally {
      if (loadGeneration === state.experimentLoadGeneration) state.experimentPage.loadingMore = false;
      if (loadGeneration === state.experimentLoadGeneration && routeIsCurrent(routeContext)) {
        const current = document.querySelector("#load-more-experiments");
        if (current) {
          current.disabled = false;
          current.textContent = "加载更多";
        }
      }
    }
  });
}

async function renderExperiment(id, routeContext = null) {
  setLoading();
  try {
    const response = await api(`/api/v1/experiments/${encodeURIComponent(id)}`, routeRequestOptions(routeContext));
    if (!routeIsCurrent(routeContext)) return;
    const detail = normalizeExperimentDetail(response, id);
    state.detail = detail;
    const renderer = renderers.get(detail.workflow_id) || renderers.get(detail.renderer) || renderGenericExperiment;
    renderer(detail, routeContext);
  } catch (error) { renderRouteError(error, routeContext); }
}

function normalizeExperimentDetail(detail, requestedId) {
  const source = detail && typeof detail === "object" ? detail : {};
  const datasets = source.datasets && typeof source.datasets === "object" ? { ...source.datasets } : {};
  for (const name of ["scan", "coarse", "refined"]) {
    if (!datasets[name] || typeof datasets[name] !== "object") continue;
    datasets[name] = { ...datasets[name], points: Array.isArray(datasets[name].points) ? datasets[name].points : [] };
  }
  const confirmations = datasets.confirmations && typeof datasets.confirmations === "object" ? datasets.confirmations : {};
  datasets.confirmations = Object.fromEntries(Object.entries(confirmations).map(([target, dataset]) => [
    target,
    { ...(dataset && typeof dataset === "object" ? dataset : {}), points: Array.isArray(dataset?.points) ? dataset.points : [] },
  ]));
  const rendererKey = source.workflow_id || source.renderer || "";
  if (["qubit_spectroscopy_scan_v1", "qubit_spectroscopy_scan"].includes(rendererKey)) {
    datasets.scan ||= { points: [] };
  } else if (["qubit_spectroscopy_calibration_v1", "qubit_spectroscopy"].includes(rendererKey)) {
    datasets.coarse ||= { points: [] };
    datasets.refined ||= { points: [] };
  } else if (["qubit_rabi_x2p_amplitude_scan_v1", "qubit_rabi_x2p_amplitude"].includes(rendererKey)) {
    datasets.scan ||= {};
  }
  const gates = Array.isArray(source.gates) ? source.gates : [];
  const suppliedGateSummary = source.gate_summary && typeof source.gate_summary === "object" ? source.gate_summary : {};
  const passed = Number.isFinite(Number(suppliedGateSummary.passed))
    ? Number(suppliedGateSummary.passed)
    : gates.filter((gate) => gate?.passed === true).length;
  return {
    ...source,
    run_id: source.run_id || requestedId,
    workflow_id: source.workflow_id || "",
    renderer: source.renderer || "",
    targets: Array.isArray(source.targets) ? source.targets : [],
    candidates: Array.isArray(source.candidates) ? source.candidates : [],
    gates,
    gate_summary: {
      ...suppliedGateSummary,
      passed,
      total: Number.isFinite(Number(suppliedGateSummary.total)) ? Number(suppliedGateSummary.total) : gates.length,
    },
    evidence_paths: Array.isArray(source.evidence_paths) ? source.evidence_paths : [],
    plot_specs: Array.isArray(source.plot_specs) ? source.plot_specs : [],
    datasets,
    request: source.request && typeof source.request === "object" ? source.request : {},
    analysis: source.analysis && typeof source.analysis === "object" ? source.analysis : {},
    claim: source.claim && typeof source.claim === "object" ? source.claim : {},
    raw: source.raw && typeof source.raw === "object" ? source.raw : source,
  };
}

function renderSpectroscopy(detail, routeContext = null) {
  const eligibleCandidates = detail.candidates.filter((row) => row.recommendation_eligible);
  const isSyntheticDemo = detail.claim?.evidence_class === "synthetic_demo";
  const originStatus = isSyntheticDemo ? "synthetic-demo" : "model-derived";
  const originNotice = isSyntheticDemo
    ? `<section class="section"><div class="warning-band">该记录仅用于验证实验数据链路与界面，不包含 QuTiP 或硬件测量证据，不能用于更新校准配置。</div></section>`
    : "";
  const actions = `<button id="export-experiment-json" class="button">导出 JSON</button><button id="export-experiment-csv" class="button">导出 CSV</button>${eligibleCandidates.length && !isSyntheticDemo ? `<button id="apply-candidates" class="button primary">更新当前配置</button>` : ""}`;
  app.innerHTML = `
    ${detailHeader("比特频谱", detail.run_id, [detail.verification_status, detail.recommendation_eligible ? "eligible" : "blocked", originStatus], actions)}
    <section class="section"><div class="facts">${fact("运行时间", dateText(detail.created_utc))}${fact("执行模式", statusText(detail.execution_mode))}${fact("目标", detail.targets.join(", "))}${fact("通过门限", `${detail.gate_summary.passed}/${detail.gate_summary.total}`)}${fact("证据路径", detail.relative_path, true)}</div></section>
    ${originNotice}
    <section class="section"><div class="section-head"><div><h2>频率候选值</h2></div></div><div class="candidate-band">${detail.candidates.map(candidateHtml).join("")}</div></section>
    <section class="section"><div class="section-head"><div><h2>实验数据图</h2><p>选择对象和数据指标，点击图中数据点查看坐标</p></div></div>${plotPanels(detail.plot_specs || [])}</section>
    <section class="section"><div class="section-head"><div><h2>硬门限检查</h2></div></div><div class="gate-list">${detail.gates.map(gateHtml).join("")}</div></section>
    <section class="section"><div class="section-head"><div><h2>数据点</h2><p>按目标量子比特分别展示</p></div></div>${pointLists(detail)}</section>
    ${detail.plot_url ? `<section class="section"><div class="section-head"><div><h2>已发布证据图</h2></div></div><img class="evidence-image" src="${esc(detail.plot_url)}" alt="已发布的频谱证据图"></section>` : ""}
    <section class="section"><details><summary>证据路径</summary><pre>${esc(detail.evidence_paths.join("\n"))}</pre></details><details><summary>请求 JSON</summary><pre>${esc(JSON.stringify(detail.request, null, 2))}</pre></details></section>`;
  requestAnimationFrame(() => installUnifiedPlots(detail.plot_specs || [], routeContext));
  document.querySelector("#export-experiment-json").addEventListener("click", () => downloadText(`spectroscopy-${detail.run_id}.json`, JSON.stringify({ request: detail.request, datasets: detail.datasets, analyses: detail.analyses, gates: detail.gates, candidates: detail.candidates }, null, 2), "application/json"));
  document.querySelector("#export-experiment-csv").addEventListener("click", () => downloadText(`spectroscopy-${detail.run_id}.csv`, spectroscopyCsv(detail), "text/csv"));
  if (eligibleCandidates.length && !isSyntheticDemo) document.querySelector("#apply-candidates").addEventListener("click", () => openCandidateUpdate(detail, eligibleCandidates));
}

function renderSpectroscopyScan(detail, routeContext = null) {
  const originStatus = detail.claim?.hardware_measurement ? "hardware" : "model-derived";
  const eligibleCandidates = detail.candidates.filter((row) => row.recommendation_eligible);
  const actions = `<button id="export-experiment-json" class="button">导出 JSON</button><button id="export-experiment-csv" class="button">导出 CSV</button>${eligibleCandidates.length ? `<button id="apply-candidates" class="button primary">更新当前配置</button>` : ""}`;
  const peaks = Object.values(detail.analysis?.peaks || {});
  app.innerHTML = `
    ${detailHeader("比特频谱", detail.run_id, [detail.verification_status, "single-scan", detail.recommendation_applicable ? (detail.recommendation_eligible ? "eligible" : "blocked") : "data-only", originStatus], actions)}
    <section class="section"><div class="facts">${fact("运行时间", dateText(detail.created_utc))}${fact("执行模式", statusText(detail.execution_mode))}${fact("目标", detail.targets.join(", "))}${fact("数据点", detail.datasets.scan.points.length)}${fact("证据路径", detail.relative_path, true)}</div></section>
    <section class="section"><div class="section-head"><div><h2>峰值分析</h2></div></div><div class="candidate-band">${peaks.map(peakHtml).join("")}</div></section>
    ${detail.recommendation_applicable ? `<section class="section"><div class="section-head"><div><h2>候选校准值</h2></div></div><div class="candidate-band">${detail.candidates.map(candidateHtml).join("")}</div></section><section class="section"><div class="section-head"><div><h2>候选门限</h2></div></div><div class="gate-list">${detail.gates.map(gateHtml).join("")}</div></section>` : ""}
    <section class="section"><div class="section-head"><div><h2>实验数据图</h2><p>选择对象和数据指标，点击图中数据点查看坐标</p></div></div>${plotPanels(detail.plot_specs || [])}</section>
    <section class="section"><div class="section-head"><div><h2>数据点</h2><p>按目标量子比特分别展示本次扫描数据</p></div></div>${pointLists(detail)}</section>
    <section class="section"><details><summary>证据路径</summary><pre>${esc(detail.evidence_paths.join("\n"))}</pre></details><details><summary>请求 JSON</summary><pre>${esc(JSON.stringify(detail.request, null, 2))}</pre></details></section>`;
  requestAnimationFrame(() => installUnifiedPlots(detail.plot_specs || [], routeContext));
  document.querySelector("#export-experiment-json").addEventListener("click", () => downloadText(`spectroscopy-${detail.run_id}.json`, JSON.stringify({ request: detail.request, dataset: detail.datasets.scan, analysis: detail.analysis, gates: detail.gates, candidates: detail.candidates }, null, 2), "application/json"));
  document.querySelector("#export-experiment-csv").addEventListener("click", () => downloadText(`spectroscopy-${detail.run_id}.csv`, spectroscopyCsv(detail), "text/csv"));
  if (eligibleCandidates.length) document.querySelector("#apply-candidates").addEventListener("click", () => openCandidateUpdate(detail, eligibleCandidates));
}

function renderRabiAmplitude(detail, routeContext = null) {
  const eligibleCandidates = detail.candidates.filter((row) => row.recommendation_eligible);
  const dataset = detail.datasets.scan || {};
  const axis = dataset.axis || {};
  const values = Array.isArray(axis.values) ? axis.values : [];
  const analysis = detail.analysis || {};
  const actions = `<button id="export-experiment-json" class="button">导出 JSON</button><button id="export-experiment-csv" class="button">导出 CSV</button>${eligibleCandidates.length ? `<button id="apply-candidates" class="button primary">更新当前配置</button>` : ""}`;
  app.innerHTML = `
    ${detailHeader("X2P Rabi 幅度校准", detail.run_id, [detail.verification_status, detail.recommendation_eligible ? "eligible" : "blocked"], actions)}
    <section class="section"><div class="facts">${fact("运行时间", dateText(detail.created_utc))}${fact("目标", detail.targets.join(", "))}${fact("扫描范围", values.length ? `${plotNumber(values[0])} - ${plotNumber(values.at(-1))} GHz` : "-")}${fact("数据点", values.length)}${fact("活动 XY2 setting", detail.request?.active_xy2_setting || detail.request?.setting_id || "-")}</div></section>
    <section class="section"><div class="section-head"><div><h2>候选 X2P 幅度</h2></div></div><div class="candidate-band">${detail.candidates.map(candidateHtml).join("")}</div></section>
    <section class="section"><div class="section-head"><div><h2>实验数据图</h2><p>选择对象和数据指标，点击图中数据点查看坐标</p></div></div>${plotPanels(detail.plot_specs || [])}</section>
    <section class="section"><div class="section-head"><div><h2>拟合与质量门</h2></div></div><pre>${esc(JSON.stringify(analysis, null, 2))}</pre><div class="gate-list">${detail.gates.map(gateHtml).join("")}</div></section>
    <section class="section"><details><summary>请求 JSON</summary><pre>${esc(JSON.stringify(detail.request, null, 2))}</pre></details></section>`;
  requestAnimationFrame(() => installUnifiedPlots(detail.plot_specs || [], routeContext));
  document.querySelector("#export-experiment-json").addEventListener("click", () => downloadText(`rabi-${detail.run_id}.json`, JSON.stringify({ request: detail.request, dataset, analysis, gates: detail.gates, candidates: detail.candidates }, null, 2), "application/json"));
  document.querySelector("#export-experiment-csv").addEventListener("click", () => downloadText(`rabi-${detail.run_id}.csv`, rabiCsv(dataset), "text/csv"));
  if (eligibleCandidates.length) document.querySelector("#apply-candidates").addEventListener("click", () => openCandidateUpdate(detail, eligibleCandidates));
}

function rabiCsv(dataset) {
  const axis = Array.isArray(dataset?.axis?.values) ? dataset.axis.values : [];
  const target = dataset?.target || Object.keys(dataset?.series || {})[0] || "target";
  const values = dataset?.series?.[target] || {};
  const columns = ["target", "amplitude_GHz", "P0", "P1", "leakage", "norm_error", "P1_fit"];
  const rows = [columns];
  axis.forEach((amplitude, index) => rows.push([target, amplitude, values.P0?.[index], values.P1?.[index], values.leakage?.[index], values.norm_error?.[index], values.P1_fit?.[index]]));
  return rows.map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

function peakHtml(row) {
  const value = row.estimated_frequency_GHz ?? row.discrete_frequency_GHz;
  return `<article class="candidate"><h3>${esc(row.qagent)}</h3><div class="candidate-value">${value == null ? "-" : fmt(value, 7)} <small>GHz</small></div><small>对比度 ${row.contrast == null ? "-" : fmt(row.contrast, 7)}</small>${status(row.valid ? "valid" : "invalid")}</article>`;
}

function renderGenericExperiment(detail, routeContext = null) {
  const specs = detail.plot_specs || [];
  app.innerHTML = `${detailHeader(experimentKind(detail.experiment_kind), detail.run_id, [detail.verification_status], "")}${specs.length ? `<section class="section"><div class="section-head"><div><h2>实验数据图</h2><p>由实验发布的统一 plot_spec</p></div></div>${plotPanels(specs)}</section>` : ""}<section class="section"><details open><summary>产物 JSON</summary><pre>${esc(JSON.stringify(detail.raw, null, 2))}</pre></details></section>`;
  if (specs.length) requestAnimationFrame(() => installUnifiedPlots(specs, routeContext));
}

function pointLists(detail) {
  return `<div class="point-list-grid">${detail.targets.map((target) => targetPointList(detail, target)).join("")}</div>`;
}

function targetPointList(detail, target) {
  const entries = [];
  if (detail.datasets.scan) {
    detail.datasets.scan.points.forEach((point) => entries.push({ phase: "scan", point }));
  } else {
    for (const [phase, dataset] of [["coarse", detail.datasets.coarse], ["refined", detail.datasets.refined]]) {
      dataset.points.forEach((point) => entries.push({ phase, point }));
    }
    const confirmation = detail.datasets.confirmations?.[target];
    if (confirmation) confirmation.points.forEach((point) => entries.push({ phase: "confirmation", point }));
  }
  const headingId = `point-list-${fieldId(target)}`;
  const frequencies = entries.map(({ point }) => fmt(point.point.coordinates_GHz[target], 6));
  const populations = entries.map(({ point }) => fmt(point.target_excited_population[target], 7));
  const phaseCounts = entries.reduce((counts, { phase }) => ({ ...counts, [phase]: (counts[phase] || 0) + 1 }), {});
  const countLabel = detail.datasets.scan ? `扫描 ${phaseCounts.scan || 0}` : `粗扫 ${phaseCounts.coarse || 0} · 细扫 ${phaseCounts.refined || 0} · 确认 ${phaseCounts.confirmation || 0}`;
  return `<section class="point-list-panel" aria-labelledby="${esc(headingId)}">
    <div class="point-list-head"><div><span class="object-dot ${esc(target.toLowerCase())}"></span><h3 id="${esc(headingId)}">${esc(target)}</h3></div><span class="point-list-count">${countLabel}</span></div>
    <div class="point-series-stack">
      ${pointSeriesList("频率（GHz）", "frequency_GHz", frequencies)}
      ${pointSeriesList("P1", "P1", populations)}
    </div>
  </section>`;
}

function pointSeriesList(label, name, values) {
  return `<div class="point-series-list"><div><strong>${esc(label)}</strong><span>list[float] · ${values.length} 项</span></div><code>${esc(`${name}: [${values.join(", ")}]`)}</code></div>`;
}

function spectroscopyCsv(detail) {
  const columns = ["phase", "target", "frequency_GHz", "excited_population", "population_000", "population_100", "population_001", "population_101", "leakage", "norm_error", "circuit_id", "circuit_receipt_sha256"];
  const rows = [columns];
  const append = (phase, target, point) => {
    const primitive = point.primitive_dressed_populations || {};
    rows.push([phase, target, point.point.coordinates_GHz[target], point.target_excited_population[target], primitive.population_000, primitive.population_100, primitive.population_001, primitive.population_101, point.leakage, point.norm_error, point.point.circuit_id, point.circuit_receipt_sha256]);
  };
  if (detail.datasets.scan) {
    detail.datasets.scan.points.forEach((point) => detail.targets.forEach((target) => append("scan", target, point)));
  } else {
    for (const [phase, dataset] of [["coarse", detail.datasets.coarse], ["refined", detail.datasets.refined]]) dataset.points.forEach((point) => detail.targets.forEach((target) => append(phase, target, point)));
    for (const [target, dataset] of Object.entries(detail.datasets.confirmations)) dataset.points.forEach((point) => append("confirmation", target, point));
  }
  return rows.map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

function csvCell(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function downloadText(filename, content, contentType) {
  const blob = new Blob([content], { type: `${contentType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function plotPanels(specs) {
  if (!specs.length) return empty("该实验暂未提供统一绘图数据");
  return `<div class="unified-plot-stack">${specs.map(plotPanel).join("")}</div>`;
}

function plotPanel(spec) {
  const domId = fieldId(spec.plot_id);
  const filters = (label, type, rows) => `<fieldset class="plot-filter-group"><legend>${esc(label)}</legend><div>${rows.map((row) => `<label><input type="checkbox" data-plot-filter="${esc(type)}" value="${esc(row.id)}" ${row.default_visible ? "checked" : ""}><span>${esc(row.label)}</span></label>`).join("")}</div></fieldset>`;
  return `<article class="unified-plot" id="plot-${esc(domId)}" data-plot-id="${esc(spec.plot_id)}">
    <div class="plot-heading"><div><h3>${esc(spec.title)}</h3><p>${esc(plotTypeLabel(spec.plot_type))}</p></div><span class="data-type">plot_spec v${esc(spec.schema_version)}</span></div>
    <div class="plot-toolbar">${filters("对象", "object", spec.objects || [])}${filters("数据", "metric", spec.metrics || [])}</div>
    <div class="plot-canvas-wrap"><canvas id="plot-canvas-${esc(domId)}" tabindex="0" role="img" aria-label="${esc(spec.title)}，可选择数据点"></canvas><div class="plot-hover-tooltip" role="tooltip" hidden></div><div class="plot-empty" hidden>当前筛选条件下没有数据</div></div>
    <div class="plot-legend" aria-label="图例"></div>
    <output class="plot-coordinate-readout" aria-live="polite"><span>数据点</span><strong>点击图中数据点查看坐标</strong></output>
  </article>`;
}

function plotTypeLabel(type) {
  return ({ line: "折线与数据点", scatter: "散点图", heatmap: "二维 Heatmap" })[type] || type;
}

function installUnifiedPlots(specs) {
  const routeContext = arguments.length > 1 ? arguments[1] : null;
  if (!routeIsCurrent(routeContext)) return;
  state.plotControllers = new Map();
  for (const spec of specs) {
    const domId = fieldId(spec.plot_id);
    const root = document.querySelector(`#plot-${domId}`);
    const canvas = document.querySelector(`#plot-canvas-${domId}`);
    if (!root || !canvas) continue;
    const controller = {
      spec, root, canvas,
      selectedObjects: new Set((spec.objects || []).filter((row) => row.default_visible).map((row) => row.id)),
      selectedMetrics: new Set((spec.metrics || []).filter((row) => row.default_visible).map((row) => row.id)),
      selectedPointId: null,
      hoverPointId: null,
      hoverTimer: null,
      renderedPoints: [],
      routeContext,
      dataGeneration: 0,
      pointLookupGeneration: 0,
      exactPointCache: new Map(),
    };
    state.plotControllers.set(spec.plot_id, controller);
    root.querySelectorAll("[data-plot-filter]").forEach((input) => input.addEventListener("change", () => {
      const selected = input.dataset.plotFilter === "object" ? controller.selectedObjects : controller.selectedMetrics;
      input.checked ? selected.add(input.value) : selected.delete(input.value);
      controller.selectedPointId = null;
      clearPlotHover(controller);
      updatePlotReadout(controller, null);
      drawUnifiedPlot(controller);
    }));
    canvas.addEventListener("click", (event) => {
      const rect = canvas.getBoundingClientRect();
      selectPlotPoint(controller, event.clientX - rect.left, event.clientY - rect.top);
    });
    canvas.addEventListener("mousemove", (event) => {
      const rect = canvas.getBoundingClientRect();
      const point = pickPlotPoint(controller, event.clientX - rect.left, event.clientY - rect.top);
      canvas.style.cursor = point ? "pointer" : "crosshair";
      schedulePlotHover(controller, point);
    });
    canvas.addEventListener("mouseleave", () => clearPlotHover(controller));
    canvas.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key) || !controller.renderedPoints.length) return;
      event.preventDefault();
      const current = controller.renderedPoints.findIndex((row) => row.id === controller.selectedPointId);
      const delta = event.key === "ArrowRight" ? 1 : -1;
      const next = current < 0 ? 0 : (current + delta + controller.renderedPoints.length) % controller.renderedPoints.length;
      setSelectedPlotPoint(controller, controller.renderedPoints[next]);
    });
    drawUnifiedPlot(controller);
    if (spec.data_url && spec.data_descriptor && spec.plot_type !== "heatmap") {
      loadExternalPlotData(controller);
    }
  }
  if (typeof ResizeObserver !== "function") return;
  state.chartObserver = new ResizeObserver((entries) => entries.forEach((entry) => {
    const controller = state.plotControllers.get(entry.target.dataset.plotId);
    if (controller) drawUnifiedPlot(controller);
  }));
  state.plotControllers.forEach((controller) => {
    const wrap = controller.canvas.parentElement;
    wrap.dataset.plotId = controller.spec.plot_id;
    state.chartObserver.observe(wrap);
  });
}

async function loadExternalPlotData(controller) {
  const generation = ++controller.dataGeneration;
  const requested = Number(controller.spec.data_descriptor?.default_max_points || 2000);
  const maxPoints = Number.isInteger(requested) ? Math.max(2, Math.min(10000, requested)) : 2000;
  const separator = controller.spec.data_url.includes("?") ? "&" : "?";
  try {
    const payload = await api(`${controller.spec.data_url}${separator}max_points=${maxPoints}`, routeRequestOptions(controller.routeContext));
    if (!plotControllerIsCurrent(controller) || generation !== controller.dataGeneration) return;
    controller.spec = {
      ...controller.spec,
      series: normalizeEnvelopeSeries(payload),
    };
    drawUnifiedPlot(controller);
  } catch (error) {
    if (!plotControllerIsCurrent(controller) || error?.name === "AbortError") return;
    toast(error.message, true);
  }
}

function normalizeEnvelopeSeries(payload) {
  if (payload?.method !== "min_max_envelope_v1" || !Array.isArray(payload.series)) {
    throw new Error("实验绘图 LOD 数据格式无效");
  }
  const pointIds = new Set();
  return payload.series.map((series) => {
    if (!series || typeof series !== "object" || !Array.isArray(series.points)) {
      throw new Error("实验绘图 LOD series 无效");
    }
    return {
      ...series,
      points: series.points.map((point) => {
        const pointId = point?.point_id || point?.id;
        if (typeof pointId !== "string" || !pointId || pointIds.has(pointId)) {
          throw new Error("实验绘图 LOD point_id 无效");
        }
        if (!Number.isFinite(Number(point.x)) || !Number.isFinite(Number(point.y))) {
          throw new Error("实验绘图 LOD 坐标无效");
        }
        pointIds.add(pointId);
        return { ...point, id: pointId, point_id: pointId };
      }),
    };
  });
}

function plotControllerIsCurrent(controller) {
  return routeIsCurrent(controller.routeContext)
    && state.plotControllers.get(controller.spec.plot_id) === controller;
}

function drawUnifiedPlot(controller) {
  const { canvas, spec } = controller;
  clearPlotHover(controller);
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const result = spec.plot_type === "heatmap"
    ? drawHeatmapPlot(ctx, rect.width, rect.height, controller)
    : drawXYPlot(ctx, rect.width, rect.height, controller);
  controller.renderedPoints = result.points;
  controller.root.querySelector(".plot-empty").hidden = result.points.length > 0;
  renderPlotLegend(controller, result.legend);
}

function visiblePlotRows(controller, key) {
  const rows = Array.isArray(controller.spec[key]) ? controller.spec[key] : [];
  return rows.filter((row) => controller.selectedObjects.has(row.object_id) && controller.selectedMetrics.has(row.metric_id));
}

function drawXYPlot(ctx, width, height, controller) {
  const series = visiblePlotRows(controller, "series");
  const sourcePoints = series.flatMap((row) => row.points || []);
  if (!sourcePoints.length) return { points: [], legend: [] };
  const margin = { left: 64, right: 20, top: 20, bottom: 52 };
  let xMin = Infinity, xMax = -Infinity, dataYMin = Infinity, yMax = -Infinity;
  sourcePoints.forEach((point) => {
    const x = Number(point.x), y = Number(point.y);
    xMin = Math.min(xMin, x); xMax = Math.max(xMax, x);
    dataYMin = Math.min(dataYMin, y); yMax = Math.max(yMax, y);
  });
  let yMin = controller.spec.axes?.y?.zero_baseline ? 0 : dataYMin;
  if (xMin === xMax) { xMin -= .5; xMax += .5; }
  if (yMin === yMax) { yMin -= Math.abs(yMin || 1) * .05; yMax += Math.abs(yMax || 1) * .05; }
  else yMax += (yMax - yMin) * .1;
  const px = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * (width - margin.left - margin.right);
  const py = (value) => height - margin.bottom - ((value - yMin) / (yMax - yMin)) * (height - margin.top - margin.bottom);
  drawPlotAxes(ctx, width, height, margin, xMin, xMax, yMin, yMax, controller.spec.axes);
  const points = [], legend = [];
  for (const marker of controller.spec.markers || []) {
    const markerX = Number(marker.x);
    if (!Number.isFinite(markerX) || markerX < xMin || markerX > xMax) continue;
    ctx.beginPath(); ctx.moveTo(px(markerX), margin.top); ctx.lineTo(px(markerX), height - margin.bottom);
    ctx.strokeStyle = "#c45b27"; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]);
    legend.push({ color: "#c45b27", dash: [5, 4], label: marker.label || "Marker" });
  }
  for (const row of series) {
    const color = plotSeriesColor(controller.spec, row.object_id, row.metric_id);
    const group = (controller.spec.groups || []).find((item) => item.id === row.group_id);
    const dash = plotGroupDash(row.group_id);
    const mapped = (row.points || []).map((point) => ({
      id: point.id || point.point_id, x: px(point.x), y: py(point.y), source: point,
      objectId: row.object_id, metricId: row.metric_id, groupId: row.group_id,
      color, hitRadius: 10,
    }));
    if (controller.spec.plot_type === "line" && mapped.length) {
      ctx.beginPath();
      mapped.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
      ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]);
    }
    mapped.forEach((point) => drawPlotMarker(ctx, point, point.id === controller.selectedPointId));
    points.push(...mapped);
    legend.push({ color, dash, label: `${plotOptionLabel(controller.spec.objects, row.object_id)} · ${plotOptionLabel(controller.spec.metrics, row.metric_id)} · ${group?.label || row.group_id || "数据"}` });
  }
  return { points, legend };
}

function drawPlotAxes(ctx, width, height, margin, xMin, xMax, yMin, yMax, axes) {
  ctx.font = "11px Segoe UI"; ctx.fillStyle = "#66736f"; ctx.strokeStyle = "#d8dfdc"; ctx.lineWidth = 1;
  for (let index = 0; index <= 4; index++) {
    const ratio = index / 4;
    const x = margin.left + ratio * (width - margin.left - margin.right);
    const y = height - margin.bottom - ratio * (height - margin.top - margin.bottom);
    ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(width - margin.right, y); ctx.stroke();
    ctx.fillText(plotTick(yMin + ratio * (yMax - yMin)), 6, y + 4);
    ctx.fillText(plotTick(xMin + ratio * (xMax - xMin)), x - 18, height - margin.bottom + 20);
  }
  ctx.fillStyle = "#394642"; ctx.textAlign = "left";
  ctx.fillText(axisLabel(axes?.y), margin.left, 11);
  ctx.textAlign = "center";
  ctx.fillText(axisLabel(axes?.x), margin.left + (width - margin.left - margin.right) / 2, height - 9);
  ctx.textAlign = "left";
}

function drawPlotMarker(ctx, point, selected) {
  ctx.beginPath(); ctx.arc(point.x, point.y, selected ? 5 : 3.2, 0, Math.PI * 2); ctx.fillStyle = point.color; ctx.fill();
  if (!selected) return;
  ctx.beginPath(); ctx.arc(point.x, point.y, 8, 0, Math.PI * 2); ctx.strokeStyle = "#16211f"; ctx.lineWidth = 2; ctx.stroke();
}

function drawHeatmapPlot(ctx, width, height, controller) {
  const layers = visiblePlotRows(controller, "layers");
  if (!layers.length) return { points: [], legend: [] };
  const columns = layers.length > 1 ? 2 : 1;
  const rows = Math.ceil(layers.length / columns);
  const gap = 24, outer = 12;
  const panelWidth = (width - outer * 2 - gap * (columns - 1)) / columns;
  const panelHeight = (height - outer * 2 - gap * (rows - 1)) / rows;
  const rendered = [], legend = [];
  layers.forEach((layer, layerIndex) => {
    const column = layerIndex % columns, rowIndex = Math.floor(layerIndex / columns);
    const panel = { x: outer + column * (panelWidth + gap), y: outer + rowIndex * (panelHeight + gap), width: panelWidth, height: panelHeight };
    const cells = layer.cells || [];
    const xs = [...new Set(cells.map((cell) => Number(cell.x)))].sort((a, b) => a - b);
    const ys = [...new Set(cells.map((cell) => Number(cell.y)))].sort((a, b) => a - b);
    const xEdges = heatmapEdges(xs), yEdges = heatmapEdges(ys);
    const values = cells.map((cell) => Number(cell.value));
    const valueMin = Math.min(...values), valueMax = Math.max(...values);
    const plot = { x: panel.x + 46, y: panel.y + 24, width: panel.width - 58, height: panel.height - 58 };
    const mapX = (value) => plot.x + ((value - xEdges[0]) / Math.max(1e-12, xEdges[xEdges.length - 1] - xEdges[0])) * plot.width;
    const mapY = (value) => plot.y + plot.height - ((value - yEdges[0]) / Math.max(1e-12, yEdges[yEdges.length - 1] - yEdges[0])) * plot.height;
    cells.forEach((cell) => {
      const xIndex = xs.indexOf(Number(cell.x)), yIndex = ys.indexOf(Number(cell.y));
      const x0 = mapX(xEdges[xIndex]), x1 = mapX(xEdges[xIndex + 1]);
      const y0 = mapY(yEdges[yIndex + 1]), y1 = mapY(yEdges[yIndex]);
      const ratio = (Number(cell.value) - valueMin) / Math.max(1e-12, valueMax - valueMin);
      ctx.fillStyle = heatmapColor(ratio); ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
      const point = { id: cell.id, x: (x0 + x1) / 2, y: (y0 + y1) / 2, x0, x1, y0, y1, source: cell, objectId: layer.object_id, metricId: layer.metric_id, color: heatmapColor(ratio), heatmap: true };
      if (point.id === controller.selectedPointId) { ctx.strokeStyle = "#16211f"; ctx.lineWidth = 2; ctx.strokeRect(x0 + 1, y0 + 1, Math.max(0, x1 - x0 - 2), Math.max(0, y1 - y0 - 2)); }
      rendered.push(point);
    });
    ctx.fillStyle = "#394642"; ctx.font = "11px Segoe UI";
    ctx.fillText(`${plotOptionLabel(controller.spec.objects, layer.object_id)} · ${plotOptionLabel(controller.spec.metrics, layer.metric_id)}`, panel.x, panel.y + 11);
    ctx.fillText(plotTick(xs[0]), plot.x, plot.y + plot.height + 16); ctx.fillText(plotTick(xs[xs.length - 1]), plot.x + plot.width - 30, plot.y + plot.height + 16);
    ctx.save(); ctx.translate(panel.x + 10, plot.y + plot.height / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(axisLabel(controller.spec.axes?.y), 0, 0); ctx.restore();
    legend.push({ color: heatmapColor(.65), dash: [], label: `${plotOptionLabel(controller.spec.objects, layer.object_id)} · ${plotOptionLabel(controller.spec.metrics, layer.metric_id)} · ${plotTick(valueMin)} 至 ${plotTick(valueMax)}` });
  });
  return { points: rendered, legend };
}

function heatmapEdges(values) {
  if (values.length === 1) return [values[0] - .5, values[0] + .5];
  const edges = [values[0] - (values[1] - values[0]) / 2];
  for (let index = 1; index < values.length; index++) edges.push((values[index - 1] + values[index]) / 2);
  edges.push(values[values.length - 1] + (values[values.length - 1] - values[values.length - 2]) / 2);
  return edges;
}

function heatmapColor(value) {
  const ratio = Math.max(0, Math.min(1, value));
  const start = [235, 244, 241], end = [15, 118, 110];
  return `rgb(${start.map((channel, index) => Math.round(channel + (end[index] - channel) * ratio)).join(",")})`;
}

function pickPlotPoint(controller, x, y) {
  const heatmap = controller.renderedPoints.find((point) => point.heatmap && x >= point.x0 && x <= point.x1 && y >= point.y0 && y <= point.y1);
  if (heatmap) return heatmap;
  let nearest = null, nearestDistance = Infinity;
  controller.renderedPoints.forEach((point) => {
    const distance = Math.hypot(point.x - x, point.y - y);
    if (distance <= (point.hitRadius || 10) && distance < nearestDistance) { nearest = point; nearestDistance = distance; }
  });
  return nearest;
}

function selectPlotPoint(controller, x, y) {
  const point = pickPlotPoint(controller, x, y);
  if (point) setSelectedPlotPoint(controller, point);
}

function setSelectedPlotPoint(controller, point) {
  controller.selectedPointId = point.id;
  updatePlotReadout(controller, point);
  drawUnifiedPlot(controller);
  resolveExactPlotPoint(controller, point);
}

async function resolveExactPlotPoint(controller, point) {
  const template = controller.spec.point_url_template;
  const pointId = point.source?.point_id || point.id;
  if (typeof template !== "string" || !template.includes("{point_id}") || !pointId) return;
  const cached = controller.exactPointCache.get(pointId);
  if (cached) {
    applyExactPlotPoint(controller, point, cached);
    return;
  }
  const generation = ++controller.pointLookupGeneration;
  const url = template.replace("{point_id}", encodeURIComponent(pointId));
  try {
    const exact = await api(url, routeRequestOptions(controller.routeContext));
    if (
      !plotControllerIsCurrent(controller)
      || generation !== controller.pointLookupGeneration
      || controller.selectedPointId !== point.id
    ) return;
    controller.exactPointCache.set(pointId, exact);
    applyExactPlotPoint(controller, point, exact);
  } catch (error) {
    if (!plotControllerIsCurrent(controller) || error?.name === "AbortError") return;
    toast(error.message, true);
  }
}

function applyExactPlotPoint(controller, renderedPoint, exact) {
  if (controller.selectedPointId !== renderedPoint.id) return;
  const source = exact?.point && typeof exact.point === "object" ? exact.point : exact;
  if (!source || typeof source !== "object") return;
  renderedPoint.source = {
    ...source,
    id: exact.point_id || source.id || renderedPoint.id,
    point_id: exact.point_id || source.point_id || renderedPoint.id,
    x: Number.isFinite(Number(exact.x)) ? Number(exact.x) : source.x,
    y: Number.isFinite(Number(exact.y)) ? Number(exact.y) : source.y,
  };
  updatePlotReadout(controller, renderedPoint);
}

function updatePlotReadout(controller, point) {
  const output = controller.root.querySelector(".plot-coordinate-readout");
  if (!point) { output.innerHTML = "<span>数据点</span><strong>点击图中数据点查看坐标</strong>"; return; }
  const details = plotPointDetails(controller, point);
  output.innerHTML = `<span>已选数据点</span><strong>${esc(details.title)}</strong><code>${esc(details.coordinates)}</code>`;
}

function plotPointDetails(controller, point) {
  const source = point.source, axes = controller.spec.axes || {};
  const group = (controller.spec.groups || []).find((row) => row.id === point.groupId)?.label;
  const title = `${plotOptionLabel(controller.spec.objects, point.objectId)} · ${plotOptionLabel(controller.spec.metrics, point.metricId)}${group ? ` · ${group}` : ""}`;
  const coordinates = point.heatmap
    ? `${axisLabel(axes.x)} = ${plotNumber(source.x)}；${axisLabel(axes.y)} = ${plotNumber(source.y)}；数值 = ${plotNumber(source.value)}`
    : `${axisLabel(axes.x)} = ${plotNumber(source.x)}；${plotOptionLabel(controller.spec.metrics, point.metricId)} = ${plotNumber(source.y)}`;
  return { title, coordinates };
}

function schedulePlotHover(controller, point) {
  if (!point) { clearPlotHover(controller); return; }
  if (controller.hoverPointId === point.id && (controller.hoverTimer || !controller.root.querySelector(".plot-hover-tooltip").hidden)) return;
  clearPlotHover(controller);
  controller.hoverPointId = point.id;
  controller.hoverTimer = setTimeout(() => {
    controller.hoverTimer = null;
    if (controller.hoverPointId === point.id) showPlotHover(controller, point);
  }, 300);
}

function showPlotHover(controller, point) {
  const tooltip = controller.root.querySelector(".plot-hover-tooltip");
  const wrap = controller.canvas.parentElement;
  const details = plotPointDetails(controller, point);
  tooltip.innerHTML = `<strong>${esc(details.title)}</strong><code>${esc(details.coordinates)}</code>`;
  tooltip.hidden = false;
  const wrapRect = wrap.getBoundingClientRect(), canvasRect = controller.canvas.getBoundingClientRect(), tooltipRect = tooltip.getBoundingClientRect();
  const pointX = canvasRect.left - wrapRect.left + point.x;
  const pointY = canvasRect.top - wrapRect.top + point.y;
  let left = pointX + 12, top = pointY + 12;
  if (left + tooltipRect.width > wrapRect.width - 8) left = pointX - tooltipRect.width - 12;
  if (top + tooltipRect.height > wrapRect.height - 8) top = pointY - tooltipRect.height - 12;
  tooltip.style.left = `${Math.max(8, left)}px`;
  tooltip.style.top = `${Math.max(8, top)}px`;
}

function clearPlotHover(controller) {
  if (controller.hoverTimer) clearTimeout(controller.hoverTimer);
  controller.hoverTimer = null;
  controller.hoverPointId = null;
  const tooltip = controller.root?.querySelector(".plot-hover-tooltip");
  if (tooltip) tooltip.hidden = true;
}

function renderPlotLegend(controller, items) {
  const unique = items.filter((item, index) => items.findIndex((other) => other.label === item.label) === index);
  controller.root.querySelector(".plot-legend").innerHTML = unique.map((item) => `<span><i style="--plot-color:${esc(item.color)}"></i>${esc(item.label)}</span>`).join("");
}

function plotSeriesColor(spec, objectId, metricId) {
  const palette = ["#0f766e", "#2563a8", "#b45309", "#287a50", "#b42318", "#7c3aed", "#0e7490", "#a16207", "#be185d"];
  const objectIndex = Math.max(0, (spec.objects || []).findIndex((row) => row.id === objectId));
  const metricIndex = Math.max(0, (spec.metrics || []).findIndex((row) => row.id === metricId));
  return palette[(objectIndex * Math.max(1, spec.metrics?.length || 1) + metricIndex) % palette.length];
}

function plotGroupDash(groupId) {
  return ({ refined: [7, 4], confirmation: [2, 4] })[groupId] || [];
}

function plotOptionLabel(rows, id) { return rows?.find((row) => row.id === id)?.label || id; }
function axisLabel(axis) { return `${axis?.label || "坐标"}${axis?.unit ? `（${axis.unit}）` : ""}`; }
function plotTick(value) { const number = Number(value); return Math.abs(number) >= 1000 || (Math.abs(number) > 0 && Math.abs(number) < .001) ? number.toExponential(2) : number.toFixed(3); }
function plotNumber(value) { const number = Number(value); return Math.abs(number) >= 1000 || (Math.abs(number) > 0 && Math.abs(number) < .0001) ? number.toExponential(6) : number.toFixed(7).replace(/0+$/, "").replace(/\.$/, ""); }

function experimentTable(rows) {
  if (!rows.length) return empty("暂无实验运行结果");
  return `<div class="table-wrap"><table><thead><tr><th>实验</th><th>运行时间</th><th>目标</th><th>执行模式</th><th>验证状态</th><th>候选状态</th><th>门限</th></tr></thead><tbody>${rows.map((row) => `
    <tr class="clickable" data-run-id="${esc(row.run_id)}"><td><strong>${esc(experimentKind(row.experiment_kind))}</strong><br><span class="mono muted">${short(row.run_id)}</span></td><td>${dateText(row.created_utc)}</td><td>${esc((row.targets || []).join(", ") || "-")}</td><td>${esc(statusText(row.execution_mode || "-"))}</td><td>${status(row.verification_status)}</td><td>${status(row.recommendation_applicable ? (row.recommendation_eligible ? "eligible" : "blocked") : "data-only")}</td><td>${row.recommendation_applicable ? `${row.gate_summary.passed}/${row.gate_summary.total}` : "-"}</td></tr>`).join("")}</tbody></table></div>`;
}

function candidateHtml(row) {
  const change = candidatePrimaryChange(row);
  const multiple = Array.isArray(row.changes) && row.changes.length > 1;
  const value = multiple ? String(row.changes.length) : change ? candidateValueText(change.proposed_value) : row.proposed_frequency_GHz == null ? "-" : fmt(row.proposed_frequency_GHz, 7);
  const unit = multiple ? "项参数" : change?.unit || (row.proposed_frequency_GHz == null ? "" : "GHz");
  const detail = multiple ? candidateChangesSummary(row) : change ? candidateParameterLabel(change.parameter_path) : `变化量 ${row.delta_GHz == null ? "-" : fmt(row.delta_GHz, 7)} GHz`;
  return `<article class="candidate"><h3>${esc(candidateSubjects(row).join(", "))}</h3><div class="candidate-value">${esc(value)} <small>${esc(unit || "")}</small></div><small>${esc(detail)}</small>${status(row.recommendation_eligible ? "eligible" : "blocked")}</article>`;
}

function candidateSubjects(row) {
  return Array.isArray(row.calibration_subjects) && row.calibration_subjects.length
    ? row.calibration_subjects
    : [row.target];
}

function candidateResourceLabel(row) {
  const resources = Array.isArray(row.configuration_resources) ? row.configuration_resources : [];
  return resources.map((resource) => `${resource.owner}/${resource.resource_id}`).join(" + ");
}

function candidatePrimaryChange(row) {
  if (Array.isArray(row.changes) && row.changes.length) return row.changes[0];
  if (Object.hasOwn(row, "proposed_frequency_GHz")) return {
    parameter_path: `calibration_values.qagents.${row.target}.reference_frequency_authority.reference_frequency_GHz`,
    proposed_value: row.proposed_frequency_GHz,
    unit: "GHz",
  };
  return null;
}

function candidateValueText(value) {
  if (typeof value === "number") return plotNumber(value);
  if (value == null) return "-";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function candidateParameterLabel(path) {
  const key = String(path || "参数").split(".").at(-1);
  const labels = {
    reference_frequency_GHz: "参考频率",
    amplitude_GHz: "脉冲幅度",
    phase_offset_rad: "相位偏置",
    dragAlpha_samples: "DRAG 系数",
    length_samples: "脉冲时长",
    duration_samples: "门时长",
    flux_offset_phi0: "磁通偏置",
  };
  return labels[key] || key.replaceAll("_", " ");
}

function candidateChangesSummary(candidate) {
  return (candidate.changes || []).map((change) => {
    const unit = change.unit ? ` ${change.unit}` : "";
    return `${candidateParameterLabel(change.parameter_path)}=${candidateValueText(change.proposed_value)}${unit}`;
  }).join("；");
}

function gateHtml(row) {
  const gateId = row.name || row.gate_id || "quality_gate";
  const metrics = row.metrics || row.details || {};
  const metricText = Object.entries(metrics)
    .filter(([, value]) => value != null)
    .map(([key, value]) => `${key}=${typeof value === "number" ? plotNumber(value) : value}`)
    .join("  ") || "-";
  return `<div class="gate-row"><strong>${esc(gateName(gateId))}</strong>${status(row.passed ? "passed" : "failed")}<span class="gate-metrics mono">${esc(metricText)}</span></div>`;
}

function frequencyTable(rows) {
  if (!rows?.length) return empty("暂无已校准的参考频率");
  return `<div class="table-wrap"><table><thead><tr><th>目标</th><th>频率（GHz）</th><th>来源</th><th>修订号</th><th>配置哈希</th></tr></thead><tbody>${rows.map((row) => `<tr><td><strong>${esc(row.target)}</strong></td><td class="numeric">${fmt(row.reference_frequency_GHz, 7)}</td><td>${esc(statusText(row.frequency_source || "-"))}</td><td>${row.revision ?? "-"}</td><td class="mono">${short(row.setting_hash || "-")}</td></tr>`).join("")}</tbody></table></div>`;
}

function platformFrequencyRows(item) {
  const qagents = item.editable?.calibration_values?.qagents || {};
  return Object.entries(qagents).flatMap(([target, value]) => value.reference_frequency_authority ? [{ target, ...value.reference_frequency_authority }] : []);
}

function validationList(validation) {
  if (!validation.checks?.length && validation.status === "valid") {
    return `<div class="validation-list"><div class="validation-row">${status("passed")}<div><strong>配置校验通过</strong><br><span class="muted">结构与业务规则均未发现阻断问题。</span></div></div></div>`;
  }
  if (!validation.checks?.length && validation.status === "invalid") {
    return empty("校验未通过，请根据字段错误修正后重新校验");
  }
  if (!validation.checks?.length) return empty("草稿尚未校验");
  return `<div class="validation-list">${validation.checks.map((row) => `<div class="validation-row">${status(row.passed ? "passed" : "failed")}<div><strong>${esc(validationName(row.name))}</strong><br><span class="muted">${esc(validationMessage(row.message))}</span></div></div>`).join("")}</div>`;
}

function detailHeader(name, id, statuses, actions) {
  return `<section class="detail-header"><div><p class="kicker">${esc(short(id))}</p><h2>${esc(name)}</h2><div class="detail-meta">${statuses.filter(Boolean).map(status).join("")}</div></div><div class="detail-actions">${actions}</div></section>`;
}

function bindExperimentRows() {
  document.querySelectorAll("[data-run-id]").forEach((row) => {
    if (row.dataset.runId.startsWith("invalid:")) return;
    bindClickableRow(row, () => { location.hash = `#/experiments/${row.dataset.runId}`; });
  });
}

function bindConfigurationRows() {
  document.querySelectorAll("[data-config-id]").forEach((row) => bindClickableRow(row, () => { location.hash = `#/configurations/${row.dataset.configId}`; }));
  document.querySelectorAll("[data-current-device]").forEach((row) => bindClickableRow(row, () => { location.hash = `#/configurations/current/${row.dataset.currentDevice}`; }));
  document.querySelectorAll("[data-draft-id]").forEach((row) => bindClickableRow(row, () => { location.hash = `#/configurations/drafts/${row.dataset.draftId}`; }));
  document.querySelectorAll("[data-platform-id]").forEach((row) => bindClickableRow(row, () => { location.hash = `#/configurations/snapshots/${row.dataset.platformId}`; }));
}

function bindClickableRow(row, action) {
  row.tabIndex = 0;
  row.setAttribute("role", "link");
  row.addEventListener("click", action);
  row.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    action();
  });
}

function openNewDraft(baseId = null) {
  const options = state.configurations.filter((row) => row.verification_status !== "invalid").map((row) => `<option value="${esc(row.configuration_id)}" ${baseId === row.configuration_id ? "selected" : ""}>${esc(row.name || row.state_id || short(row.configuration_id))}</option>`).join("");
  openDialog("创建草稿", `<div class="field"><label>基础配置</label><select id="dialog-base">${options}</select></div><div class="field"><label>名称</label><input id="dialog-name" value="频率配置"></div><div class="field"><label>备注</label><textarea id="dialog-note"></textarea></div>`, "创建", async () => {
    const result = await mutate("/api/v1/drafts", { base_configuration_id: document.querySelector("#dialog-base").value, actor_id: actor(), name: document.querySelector("#dialog-name").value.trim(), note: document.querySelector("#dialog-note").value.trim() });
    await refreshAfterMutation(`#/configurations/drafts/${result.draft_id}`, "management", "overview");
  });
}

function openCandidateDraft(detail, candidates) {
  const choices = candidates.map((candidate) => `<label class="check-row"><input type="checkbox" data-candidate-id value="${esc(candidate.candidate_id)}" checked> ${esc(candidateSubjects(candidate).join(", "))} · ${esc(candidateResourceLabel(candidate))}</label>`).join("");
  const targets = [...new Set(candidates.flatMap(candidateSubjects))];
  openDialog("基于候选值创建草稿", `<fieldset><legend>候选参数</legend>${choices}</fieldset><div class="field"><label for="dialog-name">名称</label><input id="dialog-name" value="校准 ${esc(targets.join(" + "))}"></div><div class="field"><label for="dialog-note">备注</label><textarea id="dialog-note">来自运行 ${esc(short(detail.run_id))}</textarea></div>`, "创建", async () => {
    const selected = [...document.querySelectorAll("[data-candidate-id]:checked")].map((input) => input.value);
    if (!selected.length) throw new Error("请至少选择一个可用候选");
    const result = await mutate(`/api/v1/experiments/${detail.run_id}/draft`, { actor_id: actor(), candidate_ids: selected, name: document.querySelector("#dialog-name").value.trim(), note: document.querySelector("#dialog-note").value.trim() });
    await refreshAfterMutation(`#/configurations/drafts/${result.draft_id}`, "management", "overview");
  });
  document.querySelectorAll("[data-candidate-id]").forEach((input) => input.addEventListener("change", () => {
    const selected = [...document.querySelectorAll("[data-candidate-id]:checked")];
    document.querySelector("#dialog-name").value = selected.length ? `校准候选 ${selected.length} 项` : "候选配置";
  }));
}

async function openCandidateUpdate(detail, candidates) {
  try {
    await loadResource("management");
  } catch (error) {
    markServiceUnavailable();
    toast(error.message, true);
    return;
  }
  const currentOptions = state.management.current.map((row) => `<option value="${esc(row.device_id)}">${esc(row.device_id)} · r${row.revision}</option>`).join("");
  const choices = candidates.map((candidate) => {
    const change = candidatePrimaryChange(candidate);
    const summary = candidateChangesSummary(candidate) || `${candidateValueText(change?.proposed_value)} ${change?.unit || ""}`;
    const resource = candidateResourceLabel(candidate);
    return `<label class="check-row"><input type="checkbox" data-candidate-id value="${esc(candidate.candidate_id)}" checked> ${esc(candidateSubjects(candidate).join(", "))}${resource ? ` · ${esc(resource)}` : ""} · ${esc(summary)}</label>`;
  }).join("");
  const phrase = `APPLY CALIBRATION CANDIDATES ${detail.run_id}`;
  openDialog("更新当前配置", `<div class="field"><label for="dialog-current">目标配置</label><select id="dialog-current">${currentOptions}</select></div><fieldset><legend>候选参数</legend>${choices}</fieldset><label class="check-row"><input id="dialog-confirm-candidates" type="checkbox"> 我确认将所选候选值写入当前配置</label>`, "确认更新", async () => {
    const selected = [...document.querySelectorAll("[data-candidate-id]:checked")].map((input) => input.value);
    if (!selected.length) throw new Error("请至少选择一个可用候选");
    if (!document.querySelector("#dialog-confirm-candidates").checked) throw new Error("请确认写入当前配置");
    const deviceId = document.querySelector("#dialog-current").value;
    const current = state.management.current.find((row) => row.device_id === deviceId);
    if (!current) throw new Error("当前配置不存在");
    const result = await mutate(`/api/v1/experiments/${detail.run_id}/apply-current`, {
      actor_id: actor(),
      device_id: deviceId,
      expected_content_sha256: current.content_sha256,
      candidate_ids: selected,
      confirmation_phrase: phrase,
    });
    await refreshAfterMutation(`#/configurations/current/${result.device_id}`, "management", "configurations", "overview");
  });
  const confirmation = document.querySelector("#dialog-confirm-candidates");
  const confirmButton = document.querySelector("#dialog-confirm");
  confirmButton.disabled = true;
  confirmation.addEventListener("change", () => { confirmButton.disabled = !confirmation.checked; });
}

function openPublish(item) {
  openDialog("发布快照", `<div class="field"><label>名称</label><input id="dialog-name" value="${esc(item.name)}"></div><div class="field"><label>变更原因</label><textarea id="dialog-reason" required></textarea></div><label><input id="dialog-keep" type="checkbox"> 长期保存</label>`, "发布", async () => {
    const result = await mutate(`/api/v1/drafts/${item.draft_id}/publish`, { actor_id: actor(), expected_content_sha256: item.content_sha256, name: document.querySelector("#dialog-name").value.trim(), reason: document.querySelector("#dialog-reason").value.trim(), keep: document.querySelector("#dialog-keep").checked });
    await refreshAfterMutation(`#/configurations/snapshots/${result.snapshot_id}`, "management", "configurations", "overview");
  });
}

function openCurrentSnapshot(item) {
  openDialog("保存当前配置快照", `<div class="field"><label>快照名称</label><input id="dialog-name" value="${esc(item.name)}"></div><div class="field"><label>版本说明</label><textarea id="dialog-reason" required placeholder="说明本次快照保留的原因"></textarea></div><label class="check-row"><input id="dialog-keep" type="checkbox"> 长期保存，不参与自动清理</label>`, "保存快照", async () => {
    const result = await mutate(`/api/v1/current-configurations/${encodeURIComponent(item.device_id)}/snapshots`, {
      actor_id: actor(),
      expected_content_sha256: state.detail.content_sha256,
      name: document.querySelector("#dialog-name").value.trim(),
      reason: document.querySelector("#dialog-reason").value.trim(),
      keep: document.querySelector("#dialog-keep").checked,
    });
    state.draftDirty = false;
    await refreshAfterMutation(`#/configurations/snapshots/${result.snapshot_id}`, "management", "configurations", "overview");
  });
}

function openApplySnapshot(item) {
  openDialog("恢复快照到当前配置", `<div class="restore-summary"><strong>${esc(item.name)}</strong><span class="mono">${esc(short(item.snapshot_id))}</span><p>该快照的全部可编辑参数将复制到 ${esc(item.device_id)} 的当前配置。快照本身保持只读，原当前配置不会自动生成备份。</p></div>`, "确认恢复", async () => {
    const current = await api(`/api/v1/current-configurations/${encodeURIComponent(item.device_id)}`);
    await mutate(`/api/v1/platform-snapshots/${item.snapshot_id}/apply`, {
      actor_id: actor(),
      expected_current_content_sha256: current.content_sha256,
    });
    state.draftDirty = false;
    await refreshAfterMutation(`#/configurations/current/${item.device_id}`, "management", "configurations", "overview");
  });
}

function openActivate(item) {
  const phrase = `SET ACTIVE ${item.snapshot_id}`;
  openDialog("设为当前配置", `<div class="field"><label for="dialog-confirmation">请输入以下确认短语</label><input id="dialog-confirmation" placeholder="${esc(phrase)}" autocomplete="off" required></div>`, "确认激活", async () => {
    const confirmation = document.querySelector("#dialog-confirmation").value;
    if (confirmation !== phrase) throw new Error("确认短语不匹配");
    await mutate(`/api/v1/platform-snapshots/${item.snapshot_id}/activate`, { actor_id: actor(), confirmation_phrase: confirmation });
    await refreshAfterMutation(location.hash || "#/overview", "management", "configurations", "overview");
  });
}

function renderStorage() {
  const rows = state.storage.items.filter((item) => state.storageFilter === "all" || item.storage_state === state.storageFilter);
  const filters = ["all", "hot", "archived", "archived_duplicate", "trash", "invalid"];
  app.innerHTML = `<section class="storage-view">
    <div class="storage-capacity" aria-label="存储容量摘要">
      ${metric("已分配", bytes(state.storage.allocated_bytes))}
      ${metric("可用空间", bytes(state.storage.volume_free_bytes))}
      ${metric("归档载体", bytes(state.storage.archive_bytes))}
      ${metric("可立即回收", bytes(state.storage.reclaimable_now_bytes))}
    </div>
    <div class="section-head storage-head"><div><h2>实验存储</h2><p>目录版本 ${esc(state.storage.catalog_revision)} · ${state.storage.refreshing ? "正在后台更新，新实验稍后显示" : state.storage.refresh_error ? "后台更新失败，请稍后刷新重试" : "容量数值均为实际字节口径"}</p></div><a class="button secondary" href="#/trash">查看回收站</a></div>
    <div class="storage-filters" role="toolbar" aria-label="存储筛选">${filters.map((filter) => `<button class="filter-button ${state.storageFilter === filter ? "active" : ""}" data-storage-filter="${filter}" type="button">${esc(filter === "all" ? "全部" : statusText(filter))}</button>`).join("")}</div>
    ${rows.length ? `<div class="storage-table-wrap"><table class="storage-table"><thead><tr><th>实验</th><th>状态</th><th>占盘</th><th>保留</th><th>引用</th><th>操作</th></tr></thead><tbody>${rows.map(storageRow).join("")}</tbody></table></div>` : empty("没有符合筛选条件的存储记录")}
  </section>`;
  app.querySelectorAll("[data-storage-filter]").forEach((button) => button.addEventListener("click", () => {
    state.storageFilter = button.dataset.storageFilter; renderStorage();
  }));
  installStorageActions();
  if (state.storage.refreshing) {
    state.storageRefreshTimer = setTimeout(async () => {
      if ((location.hash || "#/overview") !== "#/storage") return;
      invalidateResources("storage");
      await route({ force: true });
    }, 1500);
  }
}

function renderTrash() {
  const notice = state.trash.refreshing ? "存储目录正在更新，暂时不能恢复实验。" : state.trash.refresh_error ? "存储目录更新失败，请刷新后重试。" : "删除仅移动至回收站；此视图不提供永久清除。";
  app.innerHTML = `<section class="storage-view"><div class="section-head storage-head"><div><h2>回收站</h2><p>${notice}</p></div><a class="button secondary" href="#/storage">返回实验存储</a></div>
    ${state.trash.items.length ? `<div class="storage-table-wrap"><table class="storage-table"><thead><tr><th>实验</th><th>原状态</th><th>占盘</th><th>到期时间</th><th>操作</th></tr></thead><tbody>${state.trash.items.map((item) => `<tr><td><span class="mono">${esc(short(item.run_id))}</span><small>${esc(item.workflow_id)}</small></td><td>${status(item.carrier?.read_preference || "trash")}</td><td>${esc(bytes(item.allocated_bytes))}</td><td>${esc(dateText(item.delete_after_utc))}</td><td>${storageAction(item, "restore", "恢复")}</td></tr>`).join("")}</tbody></table></div>` : empty("回收站为空")}</section>`;
  installStorageActions();
}

function storageRow(item) {
  const blockers = item.blockers?.length ? `<span class="blocker" title="${esc(item.blockers.join("；"))}" aria-label="阻断原因：${esc(item.blockers.join("；"))}">已阻断</span>` : "";
  const actions = item.storage_state === "hot" ? `${storageAction(item, "keep", item.retention_state === "manual_keep" ? "取消长期保存" : "长期保存")} ${storageAction(item, "archive", "归档")} ${storageAction(item, "trash", "移入回收站")}`
    : item.storage_state === "archived" ? `${storageAction(item, "restore-hot", "恢复热目录")} ${storageAction(item, "trash", "移入回收站")}`
    : item.storage_state === "archived_duplicate" ? `<span class="muted">重复副本待处理</span>` : "";
  return `<tr><td><a class="mono" href="#/experiments/${encodeURIComponent(item.run_id)}">${esc(short(item.run_id))}</a><small>${esc(item.workflow_id)}</small></td><td>${status(item.storage_state)} ${blockers}</td><td>${esc(bytes(item.allocated_bytes))}<small>${item.allocated_estimated ? "估算" : "实际"}</small></td><td>${esc(statusText(item.retention_state))}</td><td>${esc(item.reference_count)}</td><td class="storage-actions">${actions || `<span class="muted">无可用操作</span>`}</td></tr>`;
}

function storageAction(item, action, label) {
  const catalogBlock = storageCatalogBlockReason();
  const actionAllowed = action === "keep"
    ? item.storage_state === "hot" && !(item.blockers || []).length
    : (item.allowed_actions || []).includes(action);
  const enabled = !catalogBlock && actionAllowed;
  const context = catalogBlock ? [catalogBlock] : [`保留状态：${statusText(item.retention_state)}`];
  if (item.reference_count) context.push(`引用：${item.reference_count}`);
  if (item.blockers?.length) context.push(...item.blockers);
  const reason = context.join("；") || "当前状态不允许此操作";
  const button = `<button type="button" class="button secondary storage-action" data-storage-action="${esc(action)}" data-storage-run="${esc(item.run_id)}" ${enabled ? "" : "disabled title=\"" + esc(reason) + "\""}>${esc(label)}</button>`;
  return enabled ? button : `<span class="storage-action-hint" tabindex="0" data-tooltip="${esc(reason)}" aria-label="${esc(label)}不可用：${esc(reason)}">${button}</span>`;
}

function activeStorageCatalog() {
  return (location.hash || "#/overview") === "#/trash" ? state.trash : state.storage;
}

function storageCatalogBlockReason() {
  const catalog = activeStorageCatalog();
  if (catalog?.refreshing) return "存储目录正在更新，暂时不能执行操作";
  if (catalog?.refresh_error) return "存储目录更新失败，请刷新后重试";
  return "";
}

function installStorageActions() {
  app.querySelectorAll("[data-storage-action]").forEach((button) => button.addEventListener("click", () => {
    const item = (activeStorageCatalog()?.items || []).find((row) => row.run_id === button.dataset.storageRun);
    if (item) openStorageAction(item, button.dataset.storageAction);
  }));
}

function openStorageAction(item, action) {
  const catalogBlock = storageCatalogBlockReason();
  if (catalogBlock) {
    toast(catalogBlock, true);
    return;
  }
  const label = { keep: "长期保存", archive: "归档", "restore-hot": "恢复热目录", trash: "移入回收站", restore: "恢复" }[action];
  const needsTrashConfirmation = action === "trash";
  const keep = action === "keep" ? item.retention_state !== "manual_keep" : null;
  const extra = needsTrashConfirmation ? `<label class="switch-control"><span>我确认将此实验移入回收站</span><input id="dialog-confirm-trash" type="checkbox"></label>` : "";
  const body = `<p>操作对象 <span class="mono">${esc(item.run_id)}</span></p><div class="field"><label for="dialog-storage-reason">原因</label><input id="dialog-storage-reason" required maxlength="240" value="Web storage management"></div>${extra}`;
  openDialog(label, body, "确认", async () => {
    if (needsTrashConfirmation && !document.querySelector("#dialog-confirm-trash").checked) throw new Error("请确认移入回收站");
    const reason = document.querySelector("#dialog-storage-reason").value.trim();
    if (!reason) throw new Error("必须填写原因");
    const payload = { actor_id: actor(), expected_catalog_revision: item.catalog_revision, expected_workflow_sha256: item.workflow_sha256, reason };
    if (action === "keep") payload.keep = keep;
    const endpoint = action === "restore" ? `/api/v1/experiment-trash/${encodeURIComponent(item.run_id)}/restore` : `/api/v1/experiments/${encodeURIComponent(item.run_id)}/${action}`;
    try {
      await mutate(endpoint, payload);
    } catch (error) {
      if (error.code === "storage_catalog_refreshing") throw new Error("存储目录正在更新，暂时不能执行操作");
      if (error.code === "storage_catalog_unavailable") throw new Error("存储目录暂时不可用，请刷新后重试");
      throw error;
    }
    await refreshAfterMutation(action === "trash" ? "#/trash" : "#/storage", "storage", "trash", "experiments", "overview");
  });
}

function bytes(value) { const amount = Number(value || 0); const units = ["B", "KiB", "MiB", "GiB", "TiB"]; let index = 0; let scaled = amount; while (scaled >= 1024 && index < units.length - 1) { scaled /= 1024; index += 1; } return `${scaled.toFixed(index ? 1 : 0)} ${units[index]}`; }

function openDialog(titleText, body, confirmText, action) {
  document.querySelector("#dialog-title").textContent = titleText;
  document.querySelector("#dialog-body").innerHTML = body;
  const confirmButton = document.querySelector("#dialog-confirm");
  confirmButton.textContent = confirmText;
  confirmButton.disabled = false;
  dialogForm.onsubmit = async (event) => {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { dialog.close(); return; }
    try { await action(); dialog.close(); } catch (error) { toast(error.message, true); }
  };
  dialog.showModal();
}

async function mutate(path, payload, method = "POST") {
  return api(path, { method, body: JSON.stringify(payload) });
}

async function refreshAfterMutation(targetHash, ...resources) {
  invalidateResources(...resources);
  const currentHash = location.hash || "#/overview";
  if (currentHash === targetHash) await refresh();
  else location.hash = targetHash;
}

function actor() {
  const value = actorInput.value.trim();
  localStorage.setItem("sqvm.actor", value);
  return value;
}

function metric(label, value) { return `<div class="metric"><span>${esc(label)}</span><strong>${esc(String(value))}</strong></div>`; }
function fact(label, value, mono = false) { return `<div class="fact"><span>${esc(label)}</span><strong class="${mono ? "mono" : ""}">${esc(String(value ?? "-"))}</strong></div>`; }
function empty(text) { return `<div class="empty">${esc(text)}</div>`; }
function status(value) {
  const key = String(value || "unknown");
  const good = ["ok", "verified", "eligible", "passed", "accepted_simulation", "active", "valid", "keep"];
  const bad = ["invalid", "failed", "rejected"];
  const warn = ["blocked", "requires_requalification", "uninitialized", "not_validated", "synthetic-demo"];
  const cls = good.includes(key) ? "status-good" : bad.includes(key) ? "status-bad" : warn.includes(key) ? "status-warn" : "status-neutral";
  return `<span class="status ${cls}">${esc(statusText(key))}</span>`;
}
function statusText(value) {
  const key = String(value || "unknown");
  const labels = {
    ok: "正常",
    verified: "已验证",
    invalid: "无效",
    eligible: "可生成候选",
    blocked: "已阻止",
    "data-only": "基础数据",
    passed: "通过",
    failed: "失败",
    rejected: "已拒绝",
    accepted_simulation: "已接受的仿真校准",
    bootstrap_seed: "初始种子值",
    active: "当前生效",
    current: "当前配置",
    valid: "校验通过",
    keep: "长期保存",
    published: "已发布",
    draft: "草稿",
    requires_requalification: "需要重新准入",
    uninitialized: "未初始化",
    not_validated: "尚未校验",
    "model-derived": "模型生成",
    "synthetic-demo": "合成演示数据",
    parallel_lockstep: "并行同步扫描",
    sequential: "顺序扫描",
    coarse: "粗扫",
    refined: "细扫",
    confirmation: "确认扫描",
    scan: "扫描",
    "single-scan": "单次扫描",
    unknown: "未知",
  };
  return labels[key] || key.replaceAll("_", " ");
}
function experimentKind(value) {
  return value === "Qubit spectroscopy" ? "比特频谱" : value;
}
function gateName(value) {
  const labels = {
    peak_quality: "峰值质量",
    max_leakage: "最大泄漏",
    max_norm_error: "最大归一化误差",
    coarse_peak_quality: "粗扫峰值质量",
    refined_peak_quality: "细扫峰值质量",
    leakage_within_limit: "泄漏低于限制",
    norm_error_within_limit: "归一化误差低于限制",
    coarse_refined_peak_consistent: "粗扫与细扫峰值一致",
    confirmation_peak_quality: "确认扫描峰值质量",
    parallel_peak_shift: "并行扫描峰值漂移",
    cross_excitation: "交叉激发",
    parallel_leakage_delta: "并行扫描泄漏变化",
    demo_data_not_calibration_eligible: "演示数据禁止校准更新",
  };
  const text = String(value);
  const separator = text.indexOf(".");
  if (separator < 0) return labels[text] || text;
  const target = text.slice(0, separator);
  const name = text.slice(separator + 1);
  return `${target === "batch" ? "全批次" : target} ${labels[name] || name}`;
}
function validationName(value) {
  const labels = {
    editable_schema: "可编辑配置结构",
    control_sections_complete: "控制配置完整性",
    clock_consistent: "时钟一致性",
    dac_valid: "DAC 参数",
    lane_order_valid: "通道顺序",
    lane_filters_valid: "通道滤波器",
    static_mixing_valid: "静态混合矩阵",
    idle_flux_units_valid: "空闲磁通单位",
    simulation_model_valid: "仿真截断配置",
    acceptance_valid: "准入阈值",
    calibration_values_valid: "校准参数",
    physical_fields_excluded: "物理参数隔离",
    physical_device_immutable: "物理设备不可变",
  };
  return labels[value] || value;
}
function validationMessage(value) {
  const labels = {
    "editable sections are valid": "可编辑配置结构有效",
    "control sections are exact": "控制配置分区完整且无多余字段",
    "sample rate and dt are reciprocal": "采样率与采样间隔互为倒数",
    "DAC resolution, range and rounding are valid": "DAC 分辨率、量程与舍入模式有效",
    "lane order exactly covers configured lanes": "通道顺序完整覆盖所有已配置通道",
    "lane latency and FIR values are valid": "通道延迟与 FIR 参数有效",
    "static mixing matrices are finite and dimensionally consistent": "静态混合矩阵数值有限且维度一致",
    "idle flux uses finite Phi/Phi0 values": "空闲磁通使用有限的 Phi/Phi0 数值",
    "acceptance thresholds are complete and positive": "准入阈值完整且均为正数",
    "calibration values and reference-frequency authorities are valid": "校准参数与参考频率权威记录有效",
    "physical device fields are excluded from editable sections": "可编辑分区未包含物理设备参数",
    "device and authority references are read-only": "设备与权威引用保持只读",
  };
  return labels[value] || value;
}
function parameterLabel(value) {
  const labels = {
    max_condition_number: "最大条件数",
    max_xy_area_relative_error: "XY 面积最大相对误差",
    max_readout_area_relative_error: "读出面积最大相对误差",
    max_z_flat_top_error_phi0: "Z 平顶最大误差（Phi/Phi0）",
    max_phase_proxy_rad: "最大相位代理误差（rad）",
    phase_proxy_window_ns: "相位代理窗口（ns）",
    max_formal_samples_per_scenario: "单场景最大正式采样数",
    analysis_runtime_budget_seconds: "分析运行预算（s）",
    total_runtime_budget_seconds: "总运行预算（s）",
  };
  return labels[value] || value;
}
function short(value) { const text = String(value || "-"); return text.length > 16 ? `${text.slice(0, 8)}...${text.slice(-6)}` : text; }
function fmt(value, digits = 6) { return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "-"; }
function dateText(value) { return value ? new Date(value).toLocaleString("zh-CN") : "-"; }
function esc(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char])); }
function setLoading() { app.innerHTML = `<div class="loading">加载中...</div>`; }
function renderError(error) { app.innerHTML = `<div class="empty"><strong>加载失败</strong><br>${esc(error.message)}</div>`; }
let toastTimer;
function toast(message, error = false) { clearTimeout(toastTimer); toastElement.textContent = message; toastElement.className = `show${error ? " error" : ""}`; toastTimer = setTimeout(() => toastElement.className = "", 2800); }

refresh();
