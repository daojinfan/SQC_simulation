const state = {
  overview: null,
  configurations: [],
  management: { drafts: [], snapshots: [], active: [] },
  experiments: [],
  detail: null,
  configTab: "snapshots",
  draftDirty: false,
  draftEditVersion: 0,
  draftSaveTimer: null,
  draftSavePromise: Promise.resolve(),
  chartObserver: null,
  currentHash: "#/overview",
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
]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
  });
  const payload = response.headers.get("Content-Type")?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const error = new Error(payload?.error || `请求失败（${response.status}）`);
    error.status = response.status;
    error.fieldErrors = payload?.field_errors || payload?.validation?.field_errors || [];
    throw error;
  }
  return payload;
}

async function refresh(showToast = false) {
  setLoading();
  try {
    const [health, overview, configurations, management, experiments] = await Promise.all([
      api("/api/v1/health"),
      api("/api/v1/overview"),
      api("/api/v1/configurations"),
      api("/api/v1/configuration-management"),
      api("/api/v1/experiments"),
    ]);
    state.overview = overview;
    state.configurations = configurations.items;
    state.management = management;
    state.experiments = experiments.items;
    document.querySelector("#service-status").className = "status status-good";
    document.querySelector("#service-status").textContent = health.status === "ok" ? "配置管理 / 实验结果只读" : health.status;
    await route();
    if (showToast) toast("数据已刷新");
  } catch (error) {
    document.querySelector("#service-status").className = "status status-bad";
    document.querySelector("#service-status").textContent = "服务不可用";
    renderError(error);
  }
}

async function route() {
  if (!state.overview) return;
  state.chartObserver?.disconnect();
  state.chartObserver = null;
  app.oninput = null;
  const parts = (location.hash || "#/overview").replace(/^#\//, "").split("/").filter(Boolean);
  const view = parts[0] || "overview";
  document.querySelectorAll("[data-nav]").forEach((link) => link.classList.toggle("active", link.dataset.nav === view));
  if (view === "overview") {
    title("平台", "总览");
    renderOverview();
  } else if (view === "configurations" && parts[1] === "drafts" && parts[2]) {
    title("配置", "编辑草稿");
    await renderDraft(parts[2]);
  } else if (view === "configurations" && parts[1] === "snapshots" && parts[2]) {
    title("配置", "已发布快照");
    await renderPlatformSnapshot(parts[2]);
  } else if (view === "configurations" && parts[1]) {
    title("配置", "快照详情");
    await renderConfiguration(parts[1]);
  } else if (view === "configurations") {
    title("平台", "配置管理");
    renderConfigurations();
  } else if (view === "experiments" && parts[1]) {
    title("实验", "运行详情");
    await renderExperiment(parts[1]);
  } else if (view === "experiments") {
    title("平台", "实验结果");
    renderExperiments();
  } else {
    location.hash = "#/overview";
  }
  app.focus({ preventScroll: true });
  state.currentHash = location.hash || "#/overview";
}

async function handleHashChange() {
  if (state.draftDirty && !(await persistDraft())) {
    history.replaceState(null, "", state.currentHash);
    return;
  }
  await route();
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
      <div class="section-head"><div><h2>配置状态</h2><p>草稿、已发布快照与当前生效配置</p></div></div>
      <div class="facts">
        ${fact("草稿", state.management.drafts.length)}
        ${fact("已发布快照", state.management.snapshots.length)}
        ${fact("已激活设备", state.management.active.length)}
        ${fact("无效产物", o.experiments.invalid)}
      </div>
    </section>`;
  bindExperimentRows();
}

function renderConfigurations() {
  const tabs = `
    <div class="tabs">
      <button data-config-tab="snapshots" class="${state.configTab === "snapshots" ? "active" : ""}">已发布快照</button>
      <button data-config-tab="drafts" class="${state.configTab === "drafts" ? "active" : ""}">草稿</button>
      <button data-config-tab="history" class="${state.configTab === "history" ? "active" : ""}">历史配置</button>
    </div>`;
  app.innerHTML = `
    <section>
      <div class="section-head">
        <div><h2>平台配置</h2><p>每个设备同时只允许一个生效快照</p></div>
        <div class="section-actions"><button id="new-draft" class="button primary">新建草稿</button></div>
      </div>
      ${tabs}
      <div id="config-content">${configurationTabContent()}</div>
    </section>`;
  document.querySelectorAll("[data-config-tab]").forEach((button) => button.addEventListener("click", () => {
    state.configTab = button.dataset.configTab;
    renderConfigurations();
  }));
  document.querySelector("#new-draft").addEventListener("click", openNewDraft);
  bindConfigurationRows();
}

function configurationTabContent() {
  if (state.configTab === "drafts") {
    if (!state.management.drafts.length) return empty("暂无草稿");
    return `<div class="table-wrap"><table><thead><tr><th>名称</th><th>设备</th><th>校验状态</th><th>检查点</th><th>更新时间</th></tr></thead><tbody>${state.management.drafts.map((row) => `
      <tr class="clickable" data-draft-id="${esc(row.draft_id)}"><td><strong>${esc(row.name)}</strong><br><span class="muted">${esc(row.note || "无备注")}</span></td><td>${esc(row.device_id)}</td><td>${status(row.validation_status)}</td><td>${row.checkpoint + 1}</td><td>${dateText(row.updated_utc)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  if (state.configTab === "history") {
    return configurationTable(state.configurations);
  }
  const platform = state.management.snapshots;
  if (!platform.length) return `<div class="empty">暂无已发布的平台快照</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>名称</th><th>设备</th><th>状态</th><th>准入状态</th><th>发布时间</th></tr></thead><tbody>${platform.map((row) => `
    <tr class="clickable" data-platform-id="${esc(row.snapshot_id)}"><td><strong>${esc(row.name)}</strong><br><span class="mono muted">${short(row.snapshot_id)}</span></td><td>${esc(row.device_id)}</td><td>${row.active ? status("active") : row.keep ? status("keep") : status("published")}</td><td>${row.experiment_eligible ? status("eligible") : status("requires_requalification")}</td><td>${dateText(row.published_utc)}</td></tr>`).join("")}</tbody></table></div>`;
}

function configurationTable(rows) {
  if (!rows.length) return empty("暂无配置快照");
  return `<div class="table-wrap"><table><thead><tr><th>配置</th><th>状态</th><th>设备</th><th>目标</th><th>路径</th></tr></thead><tbody>${rows.map((row) => `
    <tr class="clickable" data-config-id="${esc(row.configuration_id)}"><td><strong>${esc(row.name || row.state_id || short(row.configuration_id))}</strong></td><td>${status(row.verification_status === "invalid" ? "invalid" : row.status)}</td><td>${esc(row.device_id || "demo_2q1c2r")}</td><td>${esc((row.accepted_targets || []).join(", ") || "-")}</td><td class="mono">${esc(row.relative_path)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function renderConfiguration(id) {
  setLoading();
  try {
    const item = await api(`/api/v1/configurations/${encodeURIComponent(id)}`);
    app.innerHTML = `
      ${detailHeader(item.name || item.state_id || "配置", item.configuration_id, [item.status, item.verification_status], `<button id="draft-from-config" class="button primary">创建草稿</button>`)}
      <section class="section"><div class="facts">${fact("设备", item.device_id || "demo_2q1c2r")}${fact("已接受", item.accepted ? "是" : "否")}${fact("目标", (item.accepted_targets || []).join(", ") || "-")}${fact("路径", item.relative_path, true)}</div></section>
      ${controlConfigurationView(item.control_values || {})}
      ${calibrationConfigurationView(item.values || {})}
      <section class="section"><details><summary>原始配置证据 JSON</summary><pre>${esc(JSON.stringify(item.raw, null, 2))}</pre></details></section>`;
    document.querySelector("#draft-from-config").addEventListener("click", () => openNewDraft(item.configuration_id));
  } catch (error) { renderError(error); }
}

async function renderPlatformSnapshot(id) {
  setLoading();
  try {
    const item = await api(`/api/v1/platform-snapshots/${id}`);
    const capabilities = editorCapabilities(item);
    const actions = `
      <button id="snapshot-draft" class="button secondary">创建草稿</button>
      <button id="snapshot-keep" class="button secondary">${item.keep ? "取消长期保存" : "长期保存"}</button>
      <button id="snapshot-delete" class="button danger" ${item.active || item.keep ? "disabled" : ""} title="${item.active ? "当前生效快照不能删除" : item.keep ? "请先取消长期保存再删除" : "删除未被引用的快照"}">删除</button>
      <button id="snapshot-active" class="button primary" ${item.active || !item.experiment_eligible || !capabilities.canActivate ? "disabled" : ""}>设为当前配置</button>`;
    app.innerHTML = `
      ${detailHeader(item.name, item.snapshot_id, [item.active ? "active" : "published", item.experiment_eligible ? "eligible" : "requires_requalification"], actions)}
      ${item.requires_requalification ? `<section class="section warning-band">控制参数已变更，激活前必须重新完成 Stage 4.1 准入验证。</section>` : ""}
      <section class="section"><div class="facts">${fact("设备", item.device_id)}${fact("操作人", item.actor_id)}${fact("发布时间", dateText(item.published_utc))}${fact("内容哈希", short(item.content_sha256), true)}</div></section>
      ${controlConfigurationView(item.editable.control_values)}
      ${calibrationConfigurationView(item.editable.calibration_values)}
      <section class="section"><details><summary>完整快照 JSON</summary><pre>${esc(JSON.stringify(item, null, 2))}</pre></details></section>`;
    document.querySelector("#snapshot-draft").addEventListener("click", () => openNewDraft(item.snapshot_id));
    document.querySelector("#snapshot-keep").addEventListener("click", async () => {
      await mutate(`/api/v1/platform-snapshots/${id}/keep`, { actor_id: actor(), keep: !item.keep });
      await refresh();
    });
    document.querySelector("#snapshot-active").addEventListener("click", () => openActivate(item));
    document.querySelector("#snapshot-delete").addEventListener("click", async () => {
      if (!confirm(`确定删除快照“${item.name}”吗？仅未激活、未长期保存且未被引用的快照可以删除。`)) return;
      try {
        await api(`/api/v1/platform-snapshots/${id}`, { method: "DELETE", headers: { "X-SQVM-Actor": actor() } });
        await refresh(); location.hash = "#/configurations";
      } catch (error) { toast(error.message, true); }
    });
  } catch (error) { renderError(error); }
}

async function renderDraft(id) {
  setLoading();
  try {
    const item = await api(`/api/v1/drafts/${id}`);
    state.detail = item;
    state.draftDirty = false;
    const capabilities = editorCapabilities(item);
    app.innerHTML = `
      ${detailHeader(item.name, item.draft_id, ["draft", item.validation.status], `<button id="delete-draft" class="button danger">删除</button>`)}
      <section class="section">
        <div class="toolbar"><button id="save-draft" class="button secondary" ${capabilities.canSave ? "" : "disabled"}>保存草稿</button><button id="validate-draft" class="button secondary" ${capabilities.canValidate ? "" : "disabled"}>校验</button><button id="publish-draft" class="button primary" ${capabilities.canPublish && item.validation.status === "valid" ? "" : "disabled"}>发布快照</button><span class="spacer"></span><span class="mono muted">检查点 ${item.checkpoint}</span></div>
        <div id="field-errors" class="field-errors" hidden></div>
        ${draftEditor(item)}
      </section>
      <section class="section"><div class="section-head"><div><h2>校验结果</h2></div></div>${validationList(item.validation)}</section>`;
    app.oninput = markDraftDirty;
    bindStructuredEditor();
    document.querySelector("#save-draft").addEventListener("click", () => persistDraft({ notify: true }));
    document.querySelector("#validate-draft").addEventListener("click", async () => {
      if (state.draftDirty && !(await persistDraft())) return;
      try {
        const validation = await mutate(`/api/v1/drafts/${id}/validate`, { actor_id: actor() });
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
        state.draftDirty = false;
        await renderDraft(current.draft_id);
        toast("已初始化校准参数，请填写结构化记录");
      } catch (error) { showMutationError(error); }
    });
    document.querySelector("#delete-draft").addEventListener("click", async () => {
      if (!confirm(`确定删除草稿“${item.name}”吗？`)) return;
      await api(`/api/v1/drafts/${id}`, { method: "DELETE", headers: { "X-SQVM-Actor": actor() } });
      await refresh(); location.hash = "#/configurations";
    });
  } catch (error) { renderError(error); }
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
  <div class="config-group"><h3>准入阈值</h3><div class="editor-grid">${Object.keys(acceptance).map((key) => numberField(parameterLabel(key), `control_values.acceptance.${key}`, acceptance[key], key === "max_formal_samples_per_scenario" ? "1" : "any")).join("") || uncalibrated("未提供准入阈值")}</div></div>`;
}

function laneEditor(control) {
  const lanes = control.lanes || {}, order = control.lane_order || Object.keys(lanes);
  return `<div class="table-wrap matrix-scroll"><table class="editable-table"><thead><tr><th>通道</th><th>延迟（samples）</th><th>FIR 系数（逗号分隔）</th></tr></thead><tbody>${order.map((lane) => {
    const row = lanes[lane] || {};
    return `<tr><td class="mono">${esc(lane)}</td><td>${numberField("", `control_values.lanes.${lane}.latency_samples`, row.latency_samples, "1", "inline")}</td><td>${arrayField(`control_values.lanes.${lane}.fir`, row.fir)}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function matrixEditor(name, value) {
  const inputs = value.input_lanes || [], outputs = value.output_coordinates || [], matrix = value.matrix || [];
  return `<div class="matrix-block"><h4>${esc(name.toUpperCase())}</h4><div class="table-wrap matrix-scroll"><table class="matrix-table editable-table"><thead><tr><th>输出 / 输入</th>${inputs.map((lane) => `<th class="mono">${esc(lane)}</th>`).join("")}</tr></thead><tbody>${outputs.map((output, row) => `<tr><td class="mono">${esc(output)}</td>${inputs.map((_, column) => `<td>${numberField("", `control_values.static_mixing.${name}.matrix.${row}.${column}`, matrix[row]?.[column], "any", "inline")}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`;
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
  const rows = Object.entries(settings).filter(([, row]) => targets.includes(row.target) && (composite ? ["CZ", "FSIM"].includes(row.gate_type) : ["XY", "XY2", "X12", "DTN"].includes(row.gate_type)));
  if (!rows.length) return uncalibrated("未校准：尚无匹配的 Setting 记录");
  return `<div class="setting-grid">${rows.map(([id, record]) => composite ? compositeSettingEditor(id, record) : qubitSettingEditor(id, record)).join("")}</div>`;
}

function qubitSettingEditor(id, record) {
  const path = `calibration_values.waveform_registry.settings.${id}`;
  if (record.gate_type === "DTN") return `<div class="form-cluster"><h4>${esc(id)} <span class="muted">DTN</span></h4>${recordFacts(record)}<div class="readonly-line">control_role=z; envelope_class=rect; input_unit=Phi/Phi0</div></div>`;
  return `<div class="form-cluster"><h4>${esc(id)} <span class="muted">${esc(record.gate_type)}</span></h4>${recordFacts(record)}<div class="editor-grid compact">${selectField("波形类别", `${path}.waveform_class`, record.waveform_class, ["rectangle", "gaussian", "flattop"])}${numberField("长度（samples）", `${path}.length_samples`, record.length_samples, "1")}${numberField("幅度（GHz）", `${path}.amplitude_GHz`, record.amplitude_GHz)}${numberField("相位偏移（rad）", `${path}.phase_offset_rad`, record.phase_offset_rad)}${numberField("DRAG alpha（samples）", `${path}.dragAlpha_samples`, record.dragAlpha_samples)}${shapeFields(path, record)}</div></div>`;
}

function compositeSettingEditor(id, record) {
  const path = `calibration_values.waveform_registry.settings.${id}`;
  return `<div class="form-cluster full"><h4>${esc(id)} <span class="muted">${esc(record.gate_type)}</span></h4>${recordFacts(record)}<div class="editor-grid compact">${numberField("公共时长（samples）", `${path}.duration_samples`, record.duration_samples, "1")}${checkboxField("使用 F012 Mapper", `${path}.use_f012zbias_mapper`, record.use_f012zbias_mapper)}${checkboxField("使用 G2 Mapper", `${path}.use_g2zbias_mapper`, record.use_g2zbias_mapper)}${numberField("Q0 动态相位（rad）", `${path}.q0_calibrated_dynamic_phase_rad`, record.q0_calibrated_dynamic_phase_rad)}${numberField("Q1 动态相位（rad）", `${path}.q1_calibrated_dynamic_phase_rad`, record.q1_calibrated_dynamic_phase_rad)}</div><div class="waveform-grid">${["q0", "q1", "coupler"].map((name) => compositeWaveformEditor(path, name, record.waveforms?.[name] || {}, record)).join("")}</div></div>`;
}

function compositeWaveformEditor(path, name, waveform, record) {
  const isCoupler = name === "coupler";
  const useMapper = isCoupler ? record.use_g2zbias_mapper : record.use_f012zbias_mapper;
  const operand = isCoupler ? "coupling_detune_GHz" : "frequency_detune_GHz";
  const label = useMapper ? `${operand.replace("_", " ")}（GHz）` : "直接 Z 偏置（flux_offset_phi0，Phi/Phi0）";
  return `<div class="waveform-editor"><h5>${name === "q0" ? "Q0" : name === "q1" ? "Q1" : "Coupler"}</h5>${selectField("波形类别", `${path}.waveforms.${name}.waveform_class`, waveform.waveform_class, ["rectangle", "gaussian", "flattop", "acz"])}${numberField(label, `${path}.waveforms.${name}.${useMapper ? operand : "flux_offset_phi0"}`, waveform[useMapper ? operand : "flux_offset_phi0"])}${shapeFields(`${path}.waveforms.${name}`, waveform)}</div>`;
}

function shapeFields(path, value) {
  if (value.waveform_class === "rectangle") return numberField("宽度（samples）", `${path}.width_samples`, value.width_samples, "1");
  if (value.waveform_class === "gaussian") return numberField("r sigma（samples）", `${path}.r_sigma_samples`, value.r_sigma_samples);
  if (value.waveform_class === "flattop") return numberField("边缘（samples）", `${path}.edge_samples`, value.edge_samples, "1");
  if (value.waveform_class === "acz") return ["thf", "thi", "lam2", "lam3"].map((key) => numberField(`ACZ ${key}`, `${path}.parameters.${key}`, value.parameters?.[key])).join("");
  return "";
}

function mapperGroups(mappers, type) {
  const rows = Object.entries(mappers).filter(([, row]) => row.mapper_type === type);
  if (!rows.length) return uncalibrated(`未校准：尚无 ${type} 记录`);
  return `<div class="setting-grid">${rows.map(([id, mapper]) => mapperEditor(id, mapper)).join("")}</div>`;
}

function mapperEditor(id, mapper) {
  const path = `calibration_values.waveform_registry.mappers.${id}`;
  if (mapper.mapper_type === "F012ZBIAS_MAPPER") return `<div class="form-cluster"><h4>${esc(id)} <span class="muted">${esc(mapper.target)}</span></h4>${recordFacts(mapper)}<div class="editor-grid compact">${numberField("f01 max（GHz）", `${path}.f01max_GHz`, mapper.f01max_GHz)}${numberField("k（rad/Phi0）", `${path}.k_rad_per_phi0`, mapper.k_rad_per_phi0)}${numberField("空闲磁通偏置（Phi/Phi0）", `${path}.idle_flux_offset_phi0`, mapper.idle_flux_offset_phi0)}</div></div>`;
  const xs = mapper.coupling_detune_GHz || [], ys = mapper.zbias_offset_phi0 || [];
  return `<div class="form-cluster"><h4>${esc(id)} <span class="muted">C</span></h4>${recordFacts(mapper)}<div class="readonly-line">分段线性 / 越界拒绝</div><div class="table-wrap matrix-scroll"><table class="editable-table"><thead><tr><th>耦合失谐（GHz）</th><th>Z 偏置（Phi/Phi0）</th><th></th></tr></thead><tbody>${xs.map((x, index) => `<tr><td>${numberField("", `${path}.coupling_detune_GHz.${index}`, x, "any", "inline")}</td><td>${numberField("", `${path}.zbias_offset_phi0.${index}`, ys[index], "any", "inline")}</td><td><button type="button" class="icon-button" data-array-remove="${esc(path)}" data-array-index="${index}" title="删除点" aria-label="删除点">x</button></td></tr>`).join("")}</tbody></table></div><button type="button" class="button secondary array-add" data-array-add="${esc(path)}">新增配对点</button></div>`;
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

function textField(label, id, value) { return `<div class="field"><label for="${esc(id)}">${esc(label)}</label><input id="${esc(id)}" value="${esc(value ?? "")}"></div>`; }
function numberField(label, path, value, step = "any", mode = "") { const input = `<input data-path="${esc(path)}" data-value-type="number" type="number" step="${esc(step)}" value="${esc(value ?? "")}">`; return mode === "inline" ? input : `<div class="field"><label>${esc(label)}</label>${input}</div>`; }
function selectField(label, path, value, choices) { const unique = [...new Set([value, ...(choices || [])].filter(Boolean))]; return `<div class="field"><label>${esc(label)}</label><select data-path="${esc(path)}" data-value-type="string">${unique.map((entry) => `<option value="${esc(entry)}" ${entry === value ? "selected" : ""}>${esc(entry)}</option>`).join("") || '<option value="">未校准</option>'}</select></div>`; }
function checkboxField(label, path, value) { return `<label class="check-row"><input data-path="${esc(path)}" data-value-type="boolean" type="checkbox" ${value ? "checked" : ""}> ${esc(label)}</label>`; }
function arrayField(path, value) { return `<input data-path="${esc(path)}" data-value-type="number-array" value="${esc((value || []).join(", "))}" aria-label="FIR 系数">`; }
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

function markDraftDirty() {
  state.draftDirty = true;
  state.draftEditVersion += 1;
  clearTimeout(state.draftSaveTimer);
  const saveState = document.querySelector(".toolbar .mono.muted");
  if (saveState) saveState.textContent = "有未保存修改";
  document.querySelector("#validate-draft")?.setAttribute("disabled", "");
  document.querySelector("#publish-draft")?.setAttribute("disabled", "");
  state.draftSaveTimer = setTimeout(() => persistDraft({ automatic: true }), 700);
}

function collectDraft(item) {
  const editable = structuredClone(editorEditable(item));
  const name = document.querySelector("#draft-name").value.trim();
  const note = document.querySelector("#draft-note").value.trim();
  document.querySelectorAll("[data-path]").forEach((input) => {
    const type = input.dataset.valueType;
    const value = type === "boolean" ? input.checked : type === "number" ? Number(input.value) : type === "number-array" ? input.value.split(",").map((entry) => Number(entry.trim())).filter((entry) => Number.isFinite(entry)) : input.value;
    setPath(editable, input.dataset.path, value);
  });
  return { editable, name, note };
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
  document.querySelectorAll("[data-array-add]").forEach((button) => button.addEventListener("click", () => {
    const record = pathValue(editorEditable(state.detail), button.dataset.arrayAdd);
    if (!record) return;
    record.coupling_detune_GHz.push(0);
    record.zbias_offset_phi0.push(0);
    markDraftDirty();
    persistDraft().then((saved) => { if (saved) renderDraft(state.detail.draft_id); });
  }));
  document.querySelectorAll("[data-array-remove]").forEach((button) => button.addEventListener("click", () => {
    const record = pathValue(editorEditable(state.detail), button.dataset.arrayRemove);
    if (!record || record.coupling_detune_GHz.length <= 2) return toast("G2ZBIAS 至少需要两个配对点", true);
    const index = Number(button.dataset.arrayIndex);
    record.coupling_detune_GHz.splice(index, 1);
    record.zbias_offset_phi0.splice(index, 1);
    markDraftDirty();
    persistDraft().then((saved) => { if (saved) renderDraft(state.detail.draft_id); });
  }));
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
      persistDraft().then((saved) => { if (saved) renderDraft(state.detail.draft_id); });
    }
  }));
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
    toast("草稿已被更新，请刷新后处理冲突", true);
    const target = document.querySelector("#field-errors");
    if (target) {
      target.hidden = false;
      target.innerHTML = "<div><strong>保存冲突</strong> 草稿已被服务器更新。请刷新页面后根据最新内容重新编辑。</div>";
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
    if (notify) toast("草稿已保存，无新增修改");
    return true;
  }
  const item = state.detail;
  const editVersion = state.draftEditVersion;
  try {
    const { editable, name, note } = collectDraft(item);
    const result = await mutate(`/api/v1/drafts/${item.draft_id}`, { actor_id: actor(), expected_content_sha256: item.content_sha256, name, note, editable }, "PUT");
    state.detail = result;
    state.draftDirty = state.draftEditVersion !== editVersion;
    const saveState = document.querySelector(".toolbar .mono.muted");
    if (saveState) saveState.textContent = `${automatic ? "已自动保存" : "已保存"} / 检查点 ${result.checkpoint}`;
    if (state.draftDirty) {
      state.draftSaveTimer = setTimeout(() => persistDraft({ automatic: true }), 700);
    } else {
      document.querySelector("#validate-draft")?.removeAttribute("disabled");
    }
    document.querySelector("#publish-draft")?.setAttribute("disabled", "");
    if (notify) toast("草稿已保存");
    return true;
  } catch (error) {
    const saveState = document.querySelector(".toolbar .mono.muted");
    if (saveState) saveState.textContent = "保存失败";
    if (!automatic) showMutationError(error);
    return false;
  }
}

function renderExperiments() {
  app.innerHTML = `
    <section><div class="section-head"><div><h2>实验运行记录</h2><p>已发布的模型仿真证据</p></div></div>
      <div class="toolbar"><input id="experiment-search" type="search" placeholder="按目标、运行 ID 或工作流筛选"><select id="experiment-status"><option value="">全部状态</option><option value="eligible">可生成候选</option><option value="blocked">已阻止</option><option value="invalid">无效</option></select></div>
      <div id="experiment-table">${experimentTable(state.experiments)}</div>
    </section>`;
  const update = () => {
    const query = document.querySelector("#experiment-search").value.toLowerCase();
    const filter = document.querySelector("#experiment-status").value;
    const rows = state.experiments.filter((row) => {
      const haystack = [row.run_id, row.workflow_id, ...(row.targets || [])].join(" ").toLowerCase();
      const stateMatch = !filter || (filter === "eligible" && row.recommendation_eligible) || (filter === "blocked" && !row.recommendation_eligible && row.verification_status !== "invalid") || (filter === "invalid" && row.verification_status === "invalid");
      return haystack.includes(query) && stateMatch;
    });
    document.querySelector("#experiment-table").innerHTML = experimentTable(rows);
    bindExperimentRows();
  };
  document.querySelector("#experiment-search").addEventListener("input", update);
  document.querySelector("#experiment-status").addEventListener("change", update);
  bindExperimentRows();
}

async function renderExperiment(id) {
  setLoading();
  try {
    const detail = await api(`/api/v1/experiments/${encodeURIComponent(id)}`);
    state.detail = detail;
    const renderer = renderers.get(detail.workflow_id) || renderGenericExperiment;
    renderer(detail);
  } catch (error) { renderError(error); }
}

function renderSpectroscopy(detail) {
  const eligibleTargets = detail.candidates.filter((row) => row.recommendation_eligible).map((row) => row.target);
  const actions = eligibleTargets.length ? `<button id="candidate-draft" class="button primary">基于候选值创建草稿</button>` : "";
  app.innerHTML = `
    ${detailHeader("比特频谱", detail.run_id, [detail.verification_status, detail.recommendation_eligible ? "eligible" : "blocked", "model-derived"], actions)}
    <section class="section"><div class="facts">${fact("执行模式", statusText(detail.execution_mode))}${fact("目标", detail.targets.join(", "))}${fact("通过门限", `${detail.gate_summary.passed}/${detail.gate_summary.total}`)}${fact("证据路径", detail.relative_path, true)}</div></section>
    <section class="section"><div class="section-head"><div><h2>频率候选值</h2></div></div><div class="candidate-band">${detail.candidates.map(candidateHtml).join("")}</div></section>
    <section class="section"><div class="section-head"><div><h2>频谱曲线</h2><p>非条件化缀饰计算基态布居</p></div></div><div class="chart-grid">${detail.targets.map((target) => chartPanel(target)).join("")}</div></section>
    <section class="section"><div class="section-head"><div><h2>硬门限检查</h2></div></div><div class="gate-list">${detail.gates.map(gateHtml).join("")}</div></section>
    <section class="section"><div class="section-head"><div><h2>数据点</h2></div></div>${pointTable(detail)}</section>
    <section class="section"><div class="section-head"><div><h2>已发布证据图</h2></div></div><img class="evidence-image" src="${esc(detail.plot_url)}" alt="已发布的频谱证据图"></section>
    <section class="section"><details><summary>证据路径</summary><pre>${esc(detail.evidence_paths.join("\n"))}</pre></details><details><summary>请求 JSON</summary><pre>${esc(JSON.stringify(detail.request, null, 2))}</pre></details></section>`;
  requestAnimationFrame(() => installSpectroscopyCharts(detail));
  if (eligibleTargets.length) document.querySelector("#candidate-draft").addEventListener("click", () => openCandidateDraft(detail, eligibleTargets));
}

function renderGenericExperiment(detail) {
  app.innerHTML = `${detailHeader(experimentKind(detail.experiment_kind), detail.run_id, [detail.verification_status], "")}<section class="section"><details open><summary>产物 JSON</summary><pre>${esc(JSON.stringify(detail.raw, null, 2))}</pre></details></section>`;
}

function pointTable(detail) {
  const rows = [];
  for (const [phase, dataset] of [["coarse", detail.datasets.coarse], ["refined", detail.datasets.refined]]) {
    dataset.points.forEach((point) => detail.targets.forEach((target) => rows.push(pointRow(phase, target, point))));
  }
  for (const [target, dataset] of Object.entries(detail.datasets.confirmations)) dataset.points.forEach((point) => rows.push(pointRow("confirmation", target, point)));
  return `<div class="table-wrap"><table><thead><tr><th>扫描阶段</th><th>目标</th><th>频率（GHz）</th><th>激发布居</th><th>泄漏</th><th>归一化误差</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function pointRow(phase, target, point) {
  return `<tr><td>${esc(statusText(phase))}</td><td>${esc(target)}</td><td class="numeric">${fmt(point.point.coordinates_GHz[target], 6)}</td><td class="numeric">${fmt(point.target_excited_population[target], 7)}</td><td class="numeric">${fmt(point.leakage, 7)}</td><td class="numeric">${fmt(point.norm_error, 3)}</td></tr>`;
}

function drawSpectroscopyChart(canvas, target, datasets) {
  if (!canvas) return;
  const series = [
    { name: "coarse", color: "#2563a8", dataset: datasets.coarse },
    { name: "refined", color: "#b45309", dataset: datasets.refined },
  ];
  if (datasets.confirmations[target]) series.push({ name: "confirmation", color: "#287a50", dataset: datasets.confirmations[target] });
  const points = series.flatMap((item) => item.dataset.points.map((point) => ({ x: point.point.coordinates_GHz[target], y: point.target_excited_population[target] })));
  if (!points.length || points.some((point) => !Number.isFinite(point.x) || !Number.isFinite(point.y))) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  const margin = { left: 52, right: 14, top: 12, bottom: 38 };
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs), yMax = Math.max(.01, ...ys) * 1.12;
  const px = (x) => margin.left + ((x - xMin) / Math.max(1e-12, xMax - xMin)) * (width - margin.left - margin.right);
  const py = (y) => height - margin.bottom - (y / yMax) * (height - margin.top - margin.bottom);
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#d8dfdc"; ctx.fillStyle = "#66736f"; ctx.lineWidth = 1; ctx.font = "11px Segoe UI";
  for (let i = 0; i <= 4; i++) {
    const y = (yMax * i) / 4, yy = py(y);
    ctx.beginPath(); ctx.moveTo(margin.left, yy); ctx.lineTo(width - margin.right, yy); ctx.stroke();
    ctx.fillText(y.toFixed(3), 5, yy + 4);
  }
  for (let i = 0; i <= 4; i++) {
    const x = xMin + ((xMax - xMin) * i) / 4, xx = px(x);
    ctx.fillText(x.toFixed(4), xx - 18, height - 14);
  }
  for (const item of series) {
    const rows = item.dataset.points.map((point) => ({ x: point.point.coordinates_GHz[target], y: point.target_excited_population[target] }));
    ctx.strokeStyle = item.color; ctx.fillStyle = item.color; ctx.lineWidth = 2;
    ctx.beginPath(); rows.forEach((p, i) => i ? ctx.lineTo(px(p.x), py(p.y)) : ctx.moveTo(px(p.x), py(p.y))); ctx.stroke();
    rows.forEach((p) => { ctx.beginPath(); ctx.arc(px(p.x), py(p.y), 3, 0, Math.PI * 2); ctx.fill(); });
  }
}

function installSpectroscopyCharts(detail) {
  const draw = (target) => drawSpectroscopyChart(document.querySelector(`#chart-${target}`), target, detail.datasets);
  detail.targets.forEach(draw);
  if (typeof ResizeObserver !== "function") return;
  state.chartObserver = new ResizeObserver((entries) => {
    entries.forEach((entry) => draw(entry.target.dataset.chartTarget));
  });
  detail.targets.forEach((target) => {
    const wrap = document.querySelector(`#chart-${target}`)?.parentElement;
    if (wrap) {
      wrap.dataset.chartTarget = target;
      state.chartObserver.observe(wrap);
    }
  });
}

function chartPanel(target) {
  return `<article class="chart-panel"><h3>${esc(target)}</h3><div class="chart-wrap"><canvas id="chart-${esc(target)}" role="img" aria-label="${esc(target)} 频谱图：粗扫、细扫和确认扫描的激发布居随频率变化"></canvas></div><div class="legend"><span class="legend-coarse">粗扫</span><span class="legend-refined">细扫</span><span class="legend-confirmation">确认扫描</span></div></article>`;
}

function experimentTable(rows) {
  if (!rows.length) return empty("暂无实验运行结果");
  return `<div class="table-wrap"><table><thead><tr><th>实验</th><th>目标</th><th>执行模式</th><th>验证状态</th><th>候选状态</th><th>门限</th></tr></thead><tbody>${rows.map((row) => `
    <tr class="clickable" data-run-id="${esc(row.run_id)}"><td><strong>${esc(experimentKind(row.experiment_kind))}</strong><br><span class="mono muted">${short(row.run_id)}</span></td><td>${esc((row.targets || []).join(", ") || "-")}</td><td>${esc(statusText(row.execution_mode || "-"))}</td><td>${status(row.verification_status)}</td><td>${status(row.recommendation_eligible ? "eligible" : "blocked")}</td><td>${row.gate_summary.passed}/${row.gate_summary.total}</td></tr>`).join("")}</tbody></table></div>`;
}

function candidateHtml(row) {
  return `<article class="candidate"><h3>${esc(row.target)}</h3><div class="candidate-value">${row.proposed_frequency_GHz == null ? "-" : fmt(row.proposed_frequency_GHz, 7)} <small>GHz</small></div><small>变化量 ${row.delta_GHz == null ? "-" : fmt(row.delta_GHz, 7)} GHz</small>${status(row.recommendation_eligible ? "eligible" : "blocked")}</article>`;
}

function gateHtml(row) {
  return `<div class="gate-row"><strong>${esc(gateName(row.name))}</strong>${status(row.passed ? "passed" : "failed")}<span class="gate-metrics mono">${esc(Object.entries(row.metrics || {}).map(([k, v]) => `${k}=${typeof v === "number" ? fmt(v, 7) : v}`).join("  ") || "-")}</span></div>`;
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
    await refresh(); location.hash = `#/configurations/drafts/${result.draft_id}`;
  });
}

function openCandidateDraft(detail, targets) {
  const choices = targets.map((target) => `<label class="check-row"><input type="checkbox" data-candidate-target value="${esc(target)}" checked> ${esc(target)}</label>`).join("");
  openDialog("基于候选值创建草稿", `<fieldset><legend>目标</legend>${choices}</fieldset><div class="field"><label for="dialog-name">名称</label><input id="dialog-name" value="频谱校准 ${esc(targets.join(" + "))}"></div><div class="field"><label for="dialog-note">备注</label><textarea id="dialog-note">来自运行 ${esc(short(detail.run_id))}</textarea></div>`, "创建", async () => {
    const selected = [...document.querySelectorAll("[data-candidate-target]:checked")].map((input) => input.value);
    if (!selected.length) throw new Error("请至少选择一个可用目标");
    const result = await mutate(`/api/v1/experiments/${detail.run_id}/draft`, { actor_id: actor(), targets: selected, name: document.querySelector("#dialog-name").value.trim(), note: document.querySelector("#dialog-note").value.trim() });
    await refresh(); location.hash = `#/configurations/drafts/${result.draft_id}`;
  });
  document.querySelectorAll("[data-candidate-target]").forEach((input) => input.addEventListener("change", () => {
    const selected = [...document.querySelectorAll("[data-candidate-target]:checked")].map((row) => row.value);
    document.querySelector("#dialog-name").value = selected.length ? `频谱校准 ${selected.join(" + ")}` : "频谱候选配置";
  }));
}

function openPublish(item) {
  openDialog("发布快照", `<div class="field"><label>名称</label><input id="dialog-name" value="${esc(item.name)}"></div><div class="field"><label>变更原因</label><textarea id="dialog-reason" required></textarea></div><label><input id="dialog-keep" type="checkbox"> 长期保存</label>`, "发布", async () => {
    const result = await mutate(`/api/v1/drafts/${item.draft_id}/publish`, { actor_id: actor(), expected_content_sha256: item.content_sha256, name: document.querySelector("#dialog-name").value.trim(), reason: document.querySelector("#dialog-reason").value.trim(), keep: document.querySelector("#dialog-keep").checked });
    await refresh(); location.hash = `#/configurations/snapshots/${result.snapshot_id}`;
  });
}

function openActivate(item) {
  const phrase = `SET ACTIVE ${item.snapshot_id}`;
  openDialog("设为当前配置", `<div class="field"><label for="dialog-confirmation">请输入以下确认短语</label><input id="dialog-confirmation" placeholder="${esc(phrase)}" autocomplete="off" required></div>`, "确认激活", async () => {
    const confirmation = document.querySelector("#dialog-confirmation").value;
    if (confirmation !== phrase) throw new Error("确认短语不匹配");
    await mutate(`/api/v1/platform-snapshots/${item.snapshot_id}/activate`, { actor_id: actor(), confirmation_phrase: confirmation });
    await refresh(); await renderPlatformSnapshot(item.snapshot_id);
  });
}

function openDialog(titleText, body, confirmText, action) {
  document.querySelector("#dialog-title").textContent = titleText;
  document.querySelector("#dialog-body").innerHTML = body;
  document.querySelector("#dialog-confirm").textContent = confirmText;
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
  const warn = ["blocked", "requires_requalification", "uninitialized", "not_validated"];
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
    passed: "通过",
    failed: "失败",
    rejected: "已拒绝",
    accepted_simulation: "已接受的仿真校准",
    bootstrap_seed: "初始种子值",
    active: "当前生效",
    valid: "校验通过",
    keep: "长期保存",
    published: "已发布",
    draft: "草稿",
    requires_requalification: "需要重新准入",
    uninitialized: "未初始化",
    not_validated: "尚未校验",
    "model-derived": "模型生成",
    parallel_lockstep: "并行同步扫描",
    sequential: "顺序扫描",
    coarse: "粗扫",
    refined: "细扫",
    confirmation: "确认扫描",
    unknown: "未知",
  };
  return labels[key] || key.replaceAll("_", " ");
}
function experimentKind(value) {
  return value === "Qubit spectroscopy" ? "比特频谱" : value;
}
function gateName(value) {
  const labels = {
    coarse_peak_quality: "粗扫峰值质量",
    refined_peak_quality: "细扫峰值质量",
    leakage_within_limit: "泄漏低于限制",
    norm_error_within_limit: "归一化误差低于限制",
    coarse_refined_peak_consistent: "粗扫与细扫峰值一致",
    confirmation_peak_quality: "确认扫描峰值质量",
    parallel_peak_shift: "并行扫描峰值漂移",
    cross_excitation: "交叉激发",
    parallel_leakage_delta: "并行扫描泄漏变化",
  };
  const text = String(value);
  const separator = text.indexOf(".");
  if (separator < 0) return labels[text] || text;
  const target = text.slice(0, separator);
  const name = text.slice(separator + 1);
  return `${target} ${labels[name] || name}`;
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
