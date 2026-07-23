# Stage 7.1.2 校准配置与实验数据 Web 控制台详细设计

状态：bounded pilot 实现基线；本机单用户配置管理；实验结果只读

## 1. 目标

Web 控制台先以 `qubit_spectroscopy_calibration_v1` 打通配置管理和实验数据查看流程，同时冻结可扩展边界。后续增加 Rabi、Ramsey、DRAG、CZ 等校准实验时，只新增 artifact adapter 和 renderer，不修改导航、索引、配置快照或证据查看主流程。

控制台不是物理计算入口。它只消费已发布且可验证的：

```text
calibration snapshot
spectroscopy workflow + receipt
coarse/refined/confirmation datasets
gate/candidate/decision artifacts
plot and low-level evidence paths
```

Web 层不得调用 `run_circuits`、QuTiP、重新拟合峰值或修改 workflow。实验候选只能创建或更新 Draft，必须经过 Validate、Publish Snapshot 和显式 Set Active，不能直接修改 Active 配置。

## 2. 分层

```text
浏览器 renderer registry
  -> /api/v1 稳定视图模型
  -> artifact adapter registry
  -> configs/calibration + output immutable experiment artifacts

浏览器 configuration editor
  -> /api/v1 configuration-management contract
  -> PlatformConfigurationStore
  -> Draft / immutable Snapshot / Active pointer / Keep marker / Audit
```

### 2.1 artifact adapter

每种实验 adapter 负责：

- 识别 `artifact_type/workflow_id`；
- 调用实验自己的 verifier；
- 生成通用 run summary；
- 生成实验专用 detail payload；
- 声明 dataset 和 asset inventory。

频谱 adapter ID 为 `qubit_spectroscopy_calibration_v1`。未知实验不能伪装成频谱；它使用只读 generic fallback，保留 identity、状态、路径和原始 JSON。

### 2.2 renderer registry

前端以 `workflow_id` 注册 renderer：

```javascript
renderers.set("qubit_spectroscopy_calibration_v1", spectroscopyRenderer)
```

renderer 只解释已由 API 提供的视图模型。未知 `workflow_id` 使用 generic renderer，不阻塞运行列表。

## 3. 数据发现

默认 repository root 为当前项目根目录，output root 为 `<repository>/output`。

配置索引扫描：

```text
configs/calibration/*.json
output/**/calibration.json
output/platform-configurations/snapshots/*/snapshot.json
```

实验索引扫描：

```text
output/**/workflow.json
```

decision 索引扫描：

```text
output/**/decision.json
```

索引不得跟随符号链接或 junction。路径必须解析在 repository root 内。一个损坏 artifact 不得使整个列表不可用；列表行标为 `invalid` 并携带短错误原因。详情请求对损坏 artifact 返回 `422`。

不使用 SQLite 作为证据权威。v1 每次请求重新扫描 bounded output；后续可增加可删除重建的缓存索引。

## 4. API

所有 JSON 使用 UTF-8，响应禁止 NaN/Infinity。API 版本固定为 `/api/v1`。

### 4.1 Overview

```text
GET /api/v1/overview
```

返回：

```json
{
  "schema_version": "0.1",
  "configurations": {"total": 0, "accepted": 0},
  "experiments": {"total": 0, "eligible": 0, "invalid": 0},
  "decisions": {"accepted": 0, "rejected": 0},
  "latest_experiments": []
}
```

### 4.2 Configurations and lifecycle

```text
GET /api/v1/configurations
GET /api/v1/configurations/<configuration_id>
GET /api/v1/configuration-management
GET /api/v1/drafts/<draft_id>
GET /api/v1/platform-snapshots/<snapshot_id>
```

列表行包含 identity、状态、accepted、device hash、parent hash、accepted target 和 repository-relative path。详情额外返回完整 values、authority hashes、lineage 和原始 snapshot。

`configuration_id` 优先使用 `calibration_id`，其次 `state_id`。重复 ID 使相关行 invalid，详情拒绝歧义解析。

配置写接口只作用于配置生命周期，不触发实验：

```text
POST   /api/v1/drafts
PUT    /api/v1/drafts/<draft_id>
POST   /api/v1/drafts/<draft_id>/validate
POST   /api/v1/drafts/<draft_id>/publish
DELETE /api/v1/drafts/<draft_id>
POST   /api/v1/experiments/<run_id>/draft
POST   /api/v1/platform-snapshots/<snapshot_id>/activate
POST   /api/v1/platform-snapshots/<snapshot_id>/keep
DELETE /api/v1/platform-snapshots/<snapshot_id>
```

旧版 Draft 兼容流程为：

```text
Save Draft -> Validate -> Publish Snapshot -> Set Active
```

旧版 Draft 的 Publish 不自动激活。新版 Current 主流程使用“保存并生效”：已发布 Snapshot
仍不可修改，Current 校验保存、eligible 实验候选确认更新或快照恢复成功后，服务端自动生成
或复用不可变运行版本并切换 Active。浏览器中未保存的编辑不会影响正在运行的实验。

### 4.3 Experiments

```text
GET /api/v1/experiments
GET /api/v1/experiments/<run_id>
GET /api/v1/experiments/<run_id>/asset/<asset_name>
```

通用 summary：

```text
run_id, workflow_id, experiment_kind, status, verification_status,
targets, execution_mode, recommendation_eligible, parent_calibration,
gate_summary, candidate_summary, relative_path
```

频谱 detail 额外包含：

```text
request, datasets, analyses, gates, candidates, claim,
plot_url, decision_refs, evidence_paths
```

dataset 保留 point 顺序、每目标 frequency、主 observable、primitive dressed populations、leakage、norm error 和 circuit receipt。API 不做条件归一化。

允许的 asset 由 adapter 明确列出。频谱 v1 只允许 `spectroscopy.png`，禁止任意路径读取。

### 4.4 Health

```text
GET /api/v1/health
```

只返回服务状态、API schema、配置写能力和实验执行禁用状态，不暴露环境变量、绝对路径或进程信息。

## 5. 配置模型与保存策略

一个 `PlatformConfigurationSnapshot` 同时包含 `control_values` 与 `calibration_values`。`device_ref` 和 `authority_refs` 只保存外部权威引用，不复制或开放编辑物理器件参数。

可编辑范围：

- 控制信号配置：clock、lane、idle flux、mixing、waveform/electronics 等运行控制项；
- 校准结果：reference frequency、mapper、门与读出校准值等；
- Draft 名称和备注。

只读范围：

- 电容、电感、Josephson junction 等物理器件配置；
- device/authority reference；
- hash、revision、status、lineage、decision 等系统字段。

编辑器提供 Form 与 Advanced JSON 两种视图，但二者经过同一个服务端白名单与验证器。前端隐藏字段不是安全边界。

Snapshot 保存分层：

- Draft 输入停止 700 ms 后自动保存，Validate、Publish、切换编辑模式、Refresh 或离开路由前强制刷新待保存内容；
- 每个 Draft 最多保留 20 个 checkpoint：初始基线固定保留，另保留最近 19 次保存；
- 用户标记 Keep 的 Snapshot 可长期保留并可手动删除；
- 未 Keep、非 Active、未被证据引用的自动 Snapshot 只保留最近 10 个；
- Active Snapshot 和实验/决策证据引用的 Snapshot 禁止自动或手动删除。

每个 device 同时最多一个 Active Snapshot。控制信号配置变化会标记 `requires_requalification`，在重新验证完成前不能 Set Active；只有校准值变化且验证通过时可直接进入 `experiment_eligible`。

## 6. 页面信息架构

### 6.1 Overview

- 配置、实验、eligible、decision 紧凑统计；
- 最近实验表；
- 不显示营销 hero 或功能说明。

### 6.2 Configurations

- Snapshot、Draft、History 分页；
- 详情显示状态、device、lineage、accepted targets；
- QAgent reference frequency 采用结构化表格；
- 其他 values 使用可折叠 JSON；
- Draft 支持 Form/Advanced JSON、保存、验证、发布；Snapshot 支持 Keep、Set Active 和受保护删除；
- 所有写操作显示 actor、时间、父配置和校验状态，不提供绕过生命周期的 Apply 控件。

### 6.3 Experiments

- 可按状态、workflow、target 搜索；
- 详情首屏显示身份、状态、claim、候选；
- 实验曲线使用统一 `plot_spec v1.0` Canvas 组件，支持对象/指标筛选、点选择、坐标读数、line、scatter 和 heatmap；
- gate 使用扫描友好的通过/失败表；
- 频谱数据点按目标显示等长的 frequency 与 P1 list；完整 population、leakage 和 norm error 保留在导出数据；
- 原始 published PNG 作为可审计辅助资产；Canvas 数据图为交互视图，不替代证据 PNG。
- eligible 候选可创建 Draft；Web 不提供 Run、Rerun 或执行按钮。

## 7. 状态和语义

颜色不单独承担语义，所有状态同时显示文本：

```text
accepted_simulation / uninitialized
eligible / blocked
verified / invalid / unverified-generic
accept / reject
```

所有模型结果标注 `model-derived`。页面禁止使用 `measured`、shot、IQ、硬件频谱或硬件校准措辞。

## 8. 安全边界

- 实验资源只支持 GET、HEAD；不存在实验执行接口。
- POST/PUT/DELETE 只开放第 4.2 节列出的配置生命周期接口；PATCH 和其他写路径返回 `405`。
- 所有配置写入使用本机 `actor_id` 审计；v1 暂不支持多用户或远程访问。
- 静态资源和 artifact asset 使用固定 allowlist。
- URL ID 必须精确匹配索引，不拼接文件路径。
- JSON 解析拒绝非有限值；详情不返回绝对 filesystem path。
- 服务默认绑定 `127.0.0.1`，不提供认证；非本机绑定不属于 v1。
- 不提供 decision 接受/拒绝写接口，不允许原地修改已发布 Snapshot。

## 9. 测试

- configuration/run/decision 发现与稳定排序；
- accepted/uninitialized snapshot 解析；
- 频谱 workflow verifier 通过与篡改失败；
- 损坏对象不拖垮列表，详情返回 `422`；
- 重复 ID、路径越界、未知 asset、未知 ID；
- API JSON 有限性和 content type；
- 未列入白名单的写请求返回 `405`，`/api/v1/run` 不存在；
- Draft 乐观并发、20 checkpoint、Validate/Publish/Active 状态机；
- system/physical 字段不可编辑、控制变化要求 requalification；
- Keep、Active、evidence 引用保护与自动 Snapshot 最近 10 个保留规则；
- desktop 1440x900 与 mobile 390x844 无重叠、无水平溢出；
- Overview、Configurations、Experiments 导航和详情选择可用；
- Canvas 非空且频谱两目标使用各自横轴。

## 10. 后续实验增量

新增实验只需：

1. 后端注册 `workflow_id -> adapter`；
2. adapter 输出通用 summary 和专用 detail；
3. 前端注册 `workflow_id -> renderer`；
4. 增加该实验的 fixture、API contract 和视觉测试。

配置列表、运行列表、证据路径、未知实验 fallback、导航和服务入口保持不变。
