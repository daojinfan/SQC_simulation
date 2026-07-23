from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "sqvm" / "web" / "static" / "app.js"


NODE_CONTRACT = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.env.SQVM_APP_JS, "utf8");

class FakeElement {
  constructor(resolve = null) {
    this.resolve = resolve;
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.parentElement = { dataset: {}, getBoundingClientRect() { return { left: 0, top: 0, width: 600, height: 360 }; } };
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelectorAll() { return []; }
  querySelector(selector) { return this.resolve ? this.resolve(selector) : null; }
  setAttribute() {}
  removeAttribute() {}
  getAttribute() { return null; }
  focus() {}
  showModal() {}
  close() {}
  appendChild() {}
  remove() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 600, height: 360 }; }
  getContext() {
    return {
      scale() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
      arc() {}, fill() {}, setLineDash() {}, fillText() {}, fillRect() {}, strokeRect() {},
      save() {}, restore() {}, translate() {}, rotate() {},
    };
  }
}

function responsePayload(path, options = {}) {
  if (path === "/api/v1/cache-alias") return { nested: { value: "original" } };
  if (path === "/api/v1/health") return { status: "ok" };
  if (path === "/api/v1/overview") return {
    configurations: { total: 0, accepted: 0 },
    experiments: { total: 0, eligible: 0, invalid: 0 },
    latest_experiments: [],
  };
  if (path === "/api/v1/configurations") return { items: [] };
  if (path === "/api/v1/configuration-management") return {
    current: [], drafts: [], snapshots: [], active: [],
  };
  if (path.startsWith("/api/v1/experiments?")) {
    if (options.filteredExperiments) {
      const row = (runId) => ({
        run_id: runId, workflow_id: "workflow-v1", experiment_kind: "generic",
        targets: [], verification_status: "verified", recommendation_applicable: false,
        recommendation_eligible: false, gate_summary: { passed: 0, total: 0 },
      });
      if (path.includes("cursor=filter-cursor")) return {
        items: [row("run-beta"), row("run-beta-next")],
        page: { has_more: false, next_cursor: null },
      };
      if (path.includes("q=alpha")) return {
        items: [row("run-alpha")], page: { has_more: false, next_cursor: null },
      };
      if (path.includes("q=beta")) return {
        items: [row("run-beta")], page: { has_more: true, next_cursor: "filter-cursor" },
      };
      return { items: [row("run-initial")], page: { has_more: false, next_cursor: null } };
    }
    if (!options.paginatedExperiments) return { items: [] };
    const row = (runId) => ({
      run_id: runId, workflow_id: "workflow-v1", experiment_kind: "generic",
      targets: [], verification_status: "verified", recommendation_applicable: false,
      recommendation_eligible: false, gate_summary: { passed: 0, total: 0 },
    });
    if (path.includes("cursor=cursor-2")) return {
      items: [row("run-2"), row("run-3")],
      page: { has_more: false, next_cursor: null },
    };
    return {
      items: [row("run-1"), row("run-2")],
      page: { has_more: true, next_cursor: "cursor-2" },
    };
  }
  if (options.externalPlot && path.includes("/plots/external-plot/data")) return {
    schema_version: "0.1", run_id: "external-run", plot_id: "external-plot",
    method: "min_max_envelope_v1", max_points: 4, x_min: 1, x_max: 2,
    bucket_edges: [1, 2],
    series: [{
      id: "scan:Q1:P1", object_id: "Q1", metric_id: "P1", group_id: "scan",
      points: [
        { point_id: "external:p0", source_index: 0, x: 1, y: 0.2, bucket: 0, is_aggregate: true },
        { point_id: "external:p1", source_index: 1, x: 2, y: 0.8, bucket: 0, is_aggregate: true },
      ],
    }],
  };
  if (options.externalPlot && path.includes("/plots/external-plot/points/")) return {
    schema_version: "0.1", run_id: "external-run", plot_id: "external-plot",
    series_id: "scan:Q1:P1", source_index: 0, point_id: "external:p0", x: 1, y: 0.25,
    point: { id: "external:p0", x: 1, y: 0.25, metadata: { exact: true } },
  };
  if (options.externalPlot && path === "/api/v1/experiments/external-run") return {
    workflow_id: "generic_workflow_v1", run_id: "external-run",
    verification_status: "verified", experiment_kind: "generic", raw: {},
    plot_specs: [{
      schema_version: "1.0", plot_id: "external-plot", plot_type: "line", title: "External",
      objects: [{ id: "Q1", label: "Q1", default_visible: true }],
      metrics: [{ id: "P1", label: "P1", default_visible: true }],
      groups: [{ id: "scan", label: "扫描" }],
      axes: { x: { label: "频率", unit: "GHz" }, y: { label: "P1", unit: "", zero_baseline: true } },
      series: [],
      data_url: "/api/v1/experiments/external-run/plots/external-plot/data",
      point_url_template: "/api/v1/experiments/external-run/plots/external-plot/points/{point_id}",
      data_descriptor: { format: "min_max_envelope_v1", default_max_points: 4, point_lookup: "point_id" },
    }],
  };
  if (path.startsWith("/api/v1/experiments/")) return {
    workflow_id: "generic_workflow_v1",
    run_id: path.split("/").at(-1),
    verification_status: "verified",
    experiment_kind: "generic",
    plot_specs: [],
    raw: options.detailMarker ? { marker: options.detailMarker } : {},
  };
  if (path === "/api/v1/experiment-storage") return {
    catalog_revision: 1,
    allocated_bytes: 0,
    volume_free_bytes: 0,
    archive_bytes: 0,
    reclaimable_now_bytes: 0,
    refreshing: options.storageRefreshing === true,
    refresh_error: options.storageRefreshError === true,
    items: options.storageItem ? [{
      run_id: "run-storage", workflow_id: "workflow-v1", storage_state: "hot",
      retention_state: "normal", allocated_bytes: 4096, reference_count: 0,
      blockers: [], allowed_actions: ["archive", "trash"], catalog_revision: 1,
      workflow_sha256: "A".repeat(64),
    }] : [],
  };
  if (path === "/api/v1/experiment-trash") return {
    refreshing: options.trashRefreshing === true,
    refresh_error: options.trashRefreshError === true,
    items: options.trashItem ? [{
      run_id: "run-trash", workflow_id: "workflow-v1", storage_state: "trash",
      retention_state: "normal", allocated_bytes: 4096, reference_count: 0,
      blockers: [], allowed_actions: ["restore"], catalog_revision: 1,
      workflow_sha256: "B".repeat(64), carrier: { read_preference: "trash" },
    }] : [],
  };
  throw new Error(`unexpected request: ${path}`);
}

async function settle() {
  for (let index = 0; index < 8; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function boot(hash, options = {}) {
  const requests = [];
  const requestHeaders = [];
  const requestCounts = new Map();
  const delayedResponses = new Map();
  const windowListeners = {};
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new FakeElement(node));
    return nodes.get(selector);
  };
  const location = { hash };
  const document = {
    querySelector: node,
    querySelectorAll() { return []; },
    createElement() { return new FakeElement(node); },
  };
  const window = {
    addEventListener(name, handler) { windowListeners[name] = handler; },
  };
  const context = {
    console,
    document,
    window,
    location,
    history: { replaceState(_state, _unused, next) { location.hash = next; } },
    localStorage: { getItem() { return null; }, setItem() {} },
    fetch: async (path, fetchOptions = {}) => {
      requests.push(path);
      requestHeaders.push(fetchOptions.headers || {});
      const count = (requestCounts.get(path) || 0) + 1;
      requestCounts.set(path, count);
      const payload = responsePayload(path, options);
      if (
        path === options.etagPath
        && count > 1
        && fetchOptions.headers?.["If-None-Match"] === '"etag-v1"'
      ) return {
        ok: false, status: 304,
        headers: { get(name) { return name.toLowerCase() === "etag" ? '"etag-v1"' : null; } },
        async json() { throw new Error("304 must not read a body"); },
      };
      const response = {
        ok: true,
        status: 200,
        headers: { get(name) {
          if (name.toLowerCase() === "content-type") return "application/json";
          if (name.toLowerCase() === "etag" && path === options.etagPath) return '"etag-v1"';
          return null;
        } },
        async json() { return payload; },
      };
      if (path === options.delayedPath && (!options.delayedOnce || count === 1)) {
        return new Promise((resolve, reject) => delayedResponses.set(path, () => {
          if (options.delayedReject) reject(new Error(options.delayedReject));
          else resolve(response);
        }));
      }
      return response;
    },
    setTimeout,
    clearTimeout,
    setImmediate,
    structuredClone,
    encodeURIComponent,
    requestAnimationFrame(callback) { callback(); },
    ResizeObserver: class { observe() {} disconnect() {} },
    AbortController,
    devicePixelRatio: 1,
    Blob: class {},
    URL: { createObjectURL() { return "blob:test"; }, revokeObjectURL() {} },
    CSS: { escape(value) { return value; } },
    confirm() { return true; },
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nglobalThis.__contractApi = api;`, context, { filename: "app.js" });
  await settle();
  return {
    requests,
    requestHeaders,
    location,
    html() { return node("#app").innerHTML; },
    nodeHtml(selector) { return node(selector).innerHTML; },
    setValue(selector, value) { node(selector).value = value; },
    release(path) { delayedResponses.get(path)?.(); },
    async click(selector) {
      await node(selector).listeners.click();
      await settle();
    },
    async dispatch(selector, eventName, event) {
      await node(selector).listeners[eventName](event);
      await settle();
    },
    async navigate(next) {
      location.hash = next;
      await windowListeners.hashchange();
      await settle();
    },
    async refresh() {
      await node("#refresh").listeners.click();
      await settle();
    },
    async wait(milliseconds) {
      await new Promise((resolve) => setTimeout(resolve, milliseconds));
      await settle();
    },
    async request(path) {
      const payload = await context.__contractApi(path);
      await settle();
      return payload;
    },
  };
}

(async () => {
  const hashes = [
    "#/overview",
    "#/configurations",
    "#/experiments",
    "#/experiments/run-123",
    "#/storage",
    "#/trash",
  ];
  const initial = {};
  for (const hash of hashes) {
    const session = await boot(hash);
    initial[hash] = session.requests;
  }

  const navigation = await boot("#/overview");
  await navigation.navigate("#/experiments");
  const afterExperiments = [...navigation.requests];
  await navigation.navigate("#/overview");
  const afterCachedOverview = [...navigation.requests];
  await navigation.refresh();

  const stale = await boot("#/experiments/slow-run", {
    delayedPath: "/api/v1/experiments/slow-run",
    detailMarker: "STALE_DETAIL_MUST_NOT_RENDER",
  });
  await stale.navigate("#/experiments");
  const beforeLateDetail = stale.html();
  stale.release("/api/v1/experiments/slow-run");
  await settle();
  const afterLateDetail = stale.html();

  const staleError = await boot("#/experiments/slow-error", {
    delayedPath: "/api/v1/experiments/slow-error",
    delayedReject: "STALE_ERROR_MUST_NOT_RENDER",
  });
  await staleError.navigate("#/experiments");
  const beforeLateError = staleError.html();
  staleError.release("/api/v1/experiments/slow-error");
  await settle();
  const afterLateError = staleError.html();

  const refreshingStorage = await boot("#/storage", { storageRefreshing: true, storageItem: true });
  const refreshingStorageHtml = refreshingStorage.html();
  await refreshingStorage.navigate("#/overview");
  const failedTrash = await boot("#/trash", { trashRefreshError: true, trashItem: true });
  const failedTrashHtml = failedTrash.html();

  const pagination = await boot("#/experiments", { paginatedExperiments: true });
  const initialPaginationHtml = pagination.html();
  await pagination.click("#load-more-experiments");
  const appendedExperimentTableHtml = pagination.nodeHtml("#experiment-table");
  const finalPaginationHtml = pagination.nodeHtml("#experiment-pagination");

  const external = await boot("#/experiments/external-run", { externalPlot: true });
  const externalRequestsBeforeClick = [...external.requests];
  await external.dispatch("#plot-canvas-field-external-plot", "click", { clientX: 64, clientY: 243 });
  const externalRequestsAfterClick = [...external.requests];
  const exactPointReadout = external.nodeHtml(".plot-coordinate-readout");

  const staleLodPath = "/api/v1/experiments/external-run/plots/external-plot/data?max_points=4";
  const staleLod = await boot("#/experiments/external-run", { externalPlot: true, delayedPath: staleLodPath });
  await staleLod.navigate("#/experiments");
  const beforeLateLod = staleLod.html();
  staleLod.release(staleLodPath);
  await settle();
  const afterLateLod = staleLod.html();

  const etagPath = "/api/v1/experiments?limit=50";
  const etag = await boot("#/experiments", { etagPath });
  await etag.refresh();

  const aliasPath = "/api/v1/cache-alias";
  const alias = await boot("#/overview", { etagPath: aliasPath });
  const firstAliasPayload = await alias.request(aliasPath);
  firstAliasPayload.nested.value = "mutated-first-response";
  const secondAliasPayload = await alias.request(aliasPath);
  const secondAliasValue = secondAliasPayload.nested.value;
  secondAliasPayload.nested.value = "mutated-304-response";
  const thirdAliasPayload = await alias.request(aliasPath);
  const thirdAliasValue = thirdAliasPayload.nested.value;

  const alphaFilterPath = "/api/v1/experiments?limit=50&q=alpha";
  const betaFilterPath = "/api/v1/experiments?limit=50&q=beta";
  const eligibleFilterPath = `${betaFilterPath}&recommendation_state=eligible`;
  const debounced = await boot("#/experiments", { filteredExperiments: true });
  debounced.setValue("#experiment-search", "alpha");
  await debounced.dispatch("#experiment-search", "input", {});
  await debounced.wait(100);
  debounced.setValue("#experiment-search", "beta");
  await debounced.dispatch("#experiment-search", "input", {});
  await debounced.wait(280);
  debounced.setValue("#experiment-status", "invalid");
  await debounced.dispatch("#experiment-status", "change", {});
  await debounced.wait(280);

  const filtered = await boot("#/experiments", {
    filteredExperiments: true,
    delayedPath: alphaFilterPath,
  });
  filtered.setValue("#experiment-search", "alpha");
  await filtered.dispatch("#experiment-search", "input", {});
  await filtered.wait(280);
  filtered.setValue("#experiment-search", "beta");
  await filtered.dispatch("#experiment-search", "input", {});
  await filtered.wait(280);
  const betaTableBeforeLateAlpha = filtered.nodeHtml("#experiment-table");
  filtered.release(alphaFilterPath);
  await settle();
  const betaTableAfterLateAlpha = filtered.nodeHtml("#experiment-table");
  filtered.setValue("#experiment-status", "eligible");
  await filtered.dispatch("#experiment-status", "change", {});
  await filtered.wait(280);
  await filtered.click("#load-more-experiments");
  const filteredTableAfterMore = filtered.nodeHtml("#experiment-table");

  const routeCancelledFilter = await boot("#/experiments", {
    filteredExperiments: true,
    delayedPath: alphaFilterPath,
    delayedOnce: true,
  });
  routeCancelledFilter.setValue("#experiment-search", "alpha");
  await routeCancelledFilter.dispatch("#experiment-search", "input", {});
  await routeCancelledFilter.wait(280);
  await routeCancelledFilter.navigate("#/overview");
  const overviewBeforeLateFilter = routeCancelledFilter.html();
  routeCancelledFilter.release(alphaFilterPath);
  await settle();
  const overviewAfterLateFilter = routeCancelledFilter.html();
  await routeCancelledFilter.navigate("#/experiments");
  const returnedFilteredTable = routeCancelledFilter.html();

  process.stdout.write(JSON.stringify({
    initial,
    afterExperiments,
    afterCachedOverview,
    afterOverviewRefresh: navigation.requests,
    beforeLateDetail,
    afterLateDetail,
    beforeLateError,
    afterLateError,
    refreshingStorageHtml,
    failedTrashHtml,
    paginationRequests: pagination.requests,
    initialPaginationHtml,
    appendedExperimentTableHtml,
    finalPaginationHtml,
    externalRequestsBeforeClick,
    externalRequestsAfterClick,
    exactPointReadout,
    beforeLateLod,
    afterLateLod,
    etagRequests: etag.requests,
    etagRequestHeaders: etag.requestHeaders,
    etagHtml: etag.html(),
    aliasRequests: alias.requests,
    aliasRequestHeaders: alias.requestHeaders,
    secondAliasValue,
    thirdAliasValue,
    debouncedFilterRequests: debounced.requests,
    filteredRequests: filtered.requests,
    betaTableBeforeLateAlpha,
    betaTableAfterLateAlpha,
    filteredTableAfterMore,
    eligibleFilterPath,
    overviewBeforeLateFilter,
    overviewAfterLateFilter,
    routeCancelledFilterRequests: routeCancelledFilter.requests,
    returnedFilteredTable,
  }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""


@lru_cache(maxsize=1)
def _run_contract() -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the executable frontend contract")
    env = os.environ.copy()
    env["SQVM_APP_JS"] = str(APP_JS)
    result = subprocess.run(
        [node, "-e", NODE_CONTRACT],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_initial_route_only_fetches_its_required_resources():
    result = _run_contract()
    initial = result["initial"]
    assert sorted(initial["#/overview"]) == ["/api/v1/health", "/api/v1/overview"]
    assert sorted(initial["#/configurations"]) == [
        "/api/v1/configuration-management",
        "/api/v1/configurations",
    ]
    assert initial["#/experiments"] == ["/api/v1/experiments?limit=50"]
    assert initial["#/experiments/run-123"] == ["/api/v1/experiments/run-123"]
    assert initial["#/storage"] == ["/api/v1/experiment-storage"]
    assert initial["#/trash"] == ["/api/v1/experiment-trash"]


def test_navigation_reuses_cache_and_global_refresh_only_reloads_current_route():
    result = _run_contract()
    assert sorted(result["afterExperiments"]) == [
        "/api/v1/experiments?limit=50",
        "/api/v1/health",
        "/api/v1/overview",
    ]
    assert result["afterCachedOverview"] == result["afterExperiments"]
    refresh_requests = result["afterOverviewRefresh"]
    assert refresh_requests.count("/api/v1/health") == 2
    assert refresh_requests.count("/api/v1/overview") == 2
    assert refresh_requests.count("/api/v1/experiments?limit=50") == 1


def test_storage_views_render_cached_payloads_and_mutations_invalidate_related_state():
    source = APP_JS.read_text("utf-8")
    storage_body = source.split("function renderStorage()", 1)[1].split("function renderTrash()", 1)[0]
    trash_body = source.split("function renderTrash()", 1)[1].split("function storageRow", 1)[0]
    assert 'api("/api/v1/experiment-storage")' not in storage_body
    assert 'api("/api/v1/experiment-trash")' not in trash_body
    assert 'refreshAfterMutation(action === "trash" ? "#/trash" : "#/storage", "storage", "trash", "experiments", "overview")' in source
    assert 'await loadResource("management")' in source


def test_late_experiment_detail_cannot_overwrite_the_new_route():
    result = _run_contract()
    assert "实验运行记录" in result["beforeLateDetail"]
    assert result["afterLateDetail"] == result["beforeLateDetail"]
    assert "STALE_DETAIL_MUST_NOT_RENDER" not in result["afterLateDetail"]
    assert result["afterLateError"] == result["beforeLateError"]
    assert "STALE_ERROR_MUST_NOT_RENDER" not in result["afterLateError"]


def test_storage_actions_are_disabled_while_catalog_is_unreliable():
    result = _run_contract()
    refreshing = result["refreshingStorageHtml"]
    failed = result["failedTrashHtml"]
    assert "存储目录正在更新，暂时不能执行操作" in refreshing
    assert "data-storage-action" in refreshing and "disabled" in refreshing
    assert "存储目录更新失败，请刷新后重试" in failed
    assert "data-storage-action" in failed and "disabled" in failed


def test_archived_detail_renderer_and_optional_fields_are_defensive():
    source = APP_JS.read_text("utf-8")
    assert 'renderers.get(detail.workflow_id) || renderers.get(detail.renderer)' in source
    assert '["qubit_spectroscopy_scan", renderSpectroscopyScan]' in source
    assert "function normalizeExperimentDetail(detail, requestedId)" in source
    assert 'evidence_paths: Array.isArray(source.evidence_paths) ? source.evidence_paths : []' in source


def test_experiment_cursor_pagination_appends_and_deduplicates_rows():
    result = _run_contract()
    assert result["paginationRequests"] == [
        "/api/v1/experiments?limit=50",
        "/api/v1/experiments?limit=50&cursor=cursor-2",
    ]
    assert "加载更多" in result["initialPaginationHtml"]
    table = result["appendedExperimentTableHtml"]
    assert table.count('data-run-id="run-1"') == 1
    assert table.count('data-run-id="run-2"') == 1
    assert table.count('data-run-id="run-3"') == 1
    assert result["finalPaginationHtml"] == ""


def test_external_plot_loads_lod_and_resolves_selected_point_exactly():
    result = _run_contract()
    assert result["externalRequestsBeforeClick"] == [
        "/api/v1/experiments/external-run",
        "/api/v1/experiments/external-run/plots/external-plot/data?max_points=4",
    ]
    assert result["externalRequestsAfterClick"][-1] == (
        "/api/v1/experiments/external-run/plots/external-plot/points/external%3Ap0"
    )
    assert "P1 = 0.25" in result["exactPointReadout"]


def test_late_external_lod_cannot_overwrite_new_route():
    result = _run_contract()
    assert "实验运行记录" in result["beforeLateLod"]
    assert result["afterLateLod"] == result["beforeLateLod"]


def test_etag_304_reuses_cached_json_payload():
    result = _run_contract()
    assert result["etagRequests"] == [
        "/api/v1/experiments?limit=50",
        "/api/v1/experiments?limit=50",
    ]
    assert result["etagRequestHeaders"][1]["If-None-Match"] == '"etag-v1"'
    assert "实验运行记录" in result["etagHtml"]


def test_etag_cache_isolated_from_caller_object_mutation():
    result = _run_contract()
    assert result["aliasRequests"][-3:] == ["/api/v1/cache-alias"] * 3
    assert result["aliasRequestHeaders"][-2]["If-None-Match"] == '"etag-v1"'
    assert result["aliasRequestHeaders"][-1]["If-None-Match"] == '"etag-v1"'
    assert result["secondAliasValue"] == "original"
    assert result["thirdAliasValue"] == "original"


def test_server_filters_debounce_ignore_stale_responses_and_bind_cursor():
    result = _run_contract()
    eligible_filter_path = result["eligibleFilterPath"]
    assert result["debouncedFilterRequests"] == [
        "/api/v1/experiments?limit=50",
        "/api/v1/experiments?limit=50&q=beta",
        "/api/v1/experiments?limit=50&q=beta&verification_status=invalid",
    ]
    assert result["filteredRequests"] == [
        "/api/v1/experiments?limit=50",
        "/api/v1/experiments?limit=50&q=alpha",
        "/api/v1/experiments?limit=50&q=beta",
        eligible_filter_path,
        f"{eligible_filter_path}&cursor=filter-cursor",
    ]
    before_late_alpha = result["betaTableBeforeLateAlpha"]
    assert 'data-run-id="run-beta"' in before_late_alpha
    assert "run-alpha" not in before_late_alpha
    assert result["betaTableAfterLateAlpha"] == before_late_alpha
    filtered_table = result["filteredTableAfterMore"]
    assert filtered_table.count('data-run-id="run-beta"') == 1
    assert filtered_table.count('data-run-id="run-beta-next"') == 1
    assert result["overviewAfterLateFilter"] == result["overviewBeforeLateFilter"]
    assert result["routeCancelledFilterRequests"].count(
        "/api/v1/experiments?limit=50&q=alpha"
    ) == 2
    assert 'data-run-id="run-alpha"' in result["returnedFilteredTable"]
