# Stage 7.1.9 Web 可视化读模型与实验索引优化详设

状态：目标详设已冻结，Tranche 1 与 Tranche 2 已于 2026-07-22 实现。本文中的二维 tile、通用后台
重验 worker 等演进目标不代表已经落地；实际实现和延期项以第 18 节为准。

### 0.1 当前交付摘要

当前实现使用独立 SQLite/WAL 读模型保存实验摘要和详情投影；发布器在原子发布后写入幂等 inbox，
后台协调器消费事件、周期浅对账并同步 hot/archive/trash 载体状态。实验列表、总览、详情、绘图数据
和精确点回查的普通 GET 只读数据库，不遍历 `execution/`，归档完整验证只发生在后台投影或生命周期
门禁中。列表已使用 keyset pagination、后端筛选和 ETag；大型一维图使用确定性 min/max envelope，
浏览器按路由、筛选和 plot descriptor 延迟加载。

SQLite 损坏会隔离为带时间戳的派生文件后重建；数据库及 sidecar 的 symlink、junction、reparse 和
hardlink 载体会直接拒绝。读模型丢失后，hot、archive 以及目录型 trash 可以从权威载体恢复。尚未
落地的是大型二维 heatmap tile cache、通用后台重验队列和外部进程消息队列。

## 0. 背景与问题基线

Web 的产品定位是本地校准数据的浏览、筛选和可视化，不是证据校验器，也不是实验执行入口。
当前实现把这三类职责混在同一条请求链中：

1. `CalibrationWebIndex.experiments()` 从 `output` 开始递归查找 `workflow.json`；
2. 每个已知实验在生成列表摘要时调用完整工作流 verifier，因而遍历大量 `execution/` 证据文件；
3. `experiment(run_id)` 先重新生成完整实验列表，再读取详情数据；
4. `overview()` 再次调用实验列表，造成同一页面生命周期中的重复扫描；
5. 前端 `refresh()` 无论当前路由是什么，均并发加载 health、overview、configurations、
   configuration-management 和 experiments 五个接口；
6. 实验存储 catalog 只在不存在或存储操作后重建。实验发布完成后没有增量登记，已有 catalog
   会继续返回旧快照，导致最新 v0.3 实验出现在实验列表、却不出现在存储管理页。

问题发生时的观测基线为 12 个实验、33,162 个文件、约 57 MiB 逻辑数据。单次请求观测值为：

| 请求 | 当前观测耗时 | 主要成本 |
| --- | ---: | --- |
| `GET /api/v1/experiments` | 约 5 s | 递归发现和逐实验完整验证 |
| `GET /api/v1/overview` | 约 8.5 s | 重复列表扫描、配置和决策扫描 |
| `GET /api/v1/experiments/{run_id}` | 约 7 s | 为查一个 run 先重建全量列表 |

这些数值是问题现场观测，不是稳定基准或验收结果。后续性能测试必须使用独立临时根，不得读取、
修改或删除正式 `output`。

## 1. 目标与非目标

### 1.1 目标

1. Web 列表、总览和详情的响应时间不随单个实验的证据文件数线性增长；
2. 实验原子发布后直接登记，新实验在 1 秒目标窗口内出现在实验列表和存储目录；
3. Web 列表只查询可重建读模型，详情只读取界面真正需要的小型工件；
4. 证据验证在发布、后台校验和破坏性操作门禁中执行，不阻塞普通页面请求；
5. 实验列表、详情、绘图和存储目录使用同一 `run_id` 与工作流身份，不再各自递归发现；
6. 保持现有 `/api/v1` 响应字段兼容，并以可选分页参数逐步迁移前端；
7. 为大量一维点和二维热图定义统一的分级加载、降采样和精确点回查协议；
8. catalog 或读模型损坏、登记中断、并发发布及外部目录变动均可被检测和恢复；
9. 不降低 Stage 7.1.8 的归档、回收、引用保护、路径封闭和 fail-closed 安全门禁。

### 1.2 非目标

1. 本设计不改变 QCIS、波形、Hamiltonian、QuTiP 演化或校准分析算法；
2. Web 仍不执行实验；实验由 Python API/Notebook 发起；
3. 不把 Web 读模型升级为实验、校准结果或证据的 authority；
4. 不允许浏览器提交任意文件路径、证据根、归档根或 verifier 名称；
5. 不在本轮引入网络数据库、对象存储、分布式消息队列或多用户权限；
6. 不在普通页面请求中自动修复、删除或重写已发布实验；
7. 不在本轮实现永久清除、自动配额清理或 Stage 7.1.8 中明确延期的生命周期能力；
8. 不要求把历史实验原地改写成新 artifact version。

## 2. 权威边界

### 2.1 三层数据边界

```text
实验执行/发布
  |
  +-- 结果层：workflow.json、dataset*.json、plot_spec、分析、候选值、证据图
  |      供 Web 读取；文件仍属于已发布实验且不可变
  |
  +-- 证据层：execution/、Stage 4.1/5.1 工件、manifest、receipt、verification report
  |      供 verifier、归档和审计使用；普通 Web 请求不遍历
  |
  +-- 派生读模型：SQLite 行、分页索引、绘图 LOD/cache
         仅供查询加速；可丢弃、可重建、不可作为校准或删除授权
```

结果层与证据层可以位于同一个不可变运行目录，但读取策略不同。Web 可以按受控相对路径读取
`workflow.json`、绑定的数据集和已声明的显示资产；Web 不应为了列出或画图枚举 `execution/`。

### 2.2 Authority 顺序

| 信息 | Authority | 读模型用途 |
| --- | --- | --- |
| `run_id`、`workflow_id`、请求、结果、候选值 | 已发布且通过相应门禁的工作流工件 | 缓存和查询投影 |
| 数据点 | 工作流绑定的数据集工件 | 绘图投影或 LOD 缓存 |
| 工作流/数据集/回执哈希 | 原始文件字节和证据闭包 | 缓存键、并发前置条件 |
| 完整验证结论 | 指定版本 verifier 对同一身份的验证结果 | 显示最近验证状态，不自行推导 |
| 热目录、归档、回收站状态 | 载体、生命周期事件和存储 catalog 重建结果 | 在 Web 中显示 |
| 配置引用与 manual keep | typed reference graph 和生命周期事件 | 显示摘要，不能覆盖权威判断 |
| Web 筛选、分页、降采样 | 派生读模型 | 无证据含义 |

SQLite 文件损坏或丢失只影响查询性能和暂时可用性，不能改变实验工件、配置、生命周期事件或
删除资格。任何由读模型得到的 `allowed_actions` 都只是 UI 提示。

## 3. 安全不变量

以下不变量高于性能目标：

1. **不可修改证据**：投影、重建、绘图和后台验证不得写入已发布实验目录；
2. **身份绑定**：每个投影必须绑定 `run_id + workflow_sha256`，有回执时同时绑定
   `receipt_sha256`；同一 `run_id` 出现不同工作流哈希时标记 identity conflict；
3. **读模型不授权**：应用候选参数、归档、恢复热目录、移入回收站等操作必须重新读取当前载体，
   检查预期 revision 和工作流哈希，并执行各自既有 verifier/引用/保留门禁；
4. **未知即受限**：未知 artifact version、未知 verifier、哈希不一致、重复身份、路径异常或后台
   验证失败均不得显示为 `verified`，也不得启用依赖验证的操作；
5. **路径封闭**：发现和详情读取只能访问启动配置的受信根，不跟随 symlink、junction、reparse
   point 或硬链接，不接受来自浏览器的文件系统路径；
6. **有界读取**：JSON、图片、plot spec、归档条目和数据响应均有独立的字节、点数、维度和递归
   深度上限，拒绝 NaN、无穷值和重复 JSON key；
7. **不可静默降级**：索引滞后、后台验证失败、重复身份、载体缺失和 catalog 重建失败必须在
   health/管理视图中可观察，不能通过返回旧的 `verified` 掩盖；
8. **失败隔离**：一个损坏 run 不得阻止其他合法 run 的列表和绘图；但损坏 run 本身 fail closed；
9. **正式数据隔离**：所有自动化测试、故障注入和性能数据生成只能使用独立临时目录；
10. **发布语义稳定**：索引登记失败不能回滚或删除已经成功原子发布的实验，也不能把同一 run
    重新执行成第二个身份；失败必须进入可恢复的待投影状态。

## 4. 目标架构

新增一个独立的、可重建的 Web 读模型组件，本文暂称 `ExperimentReadModel`。它与现有
`CalibrationWebIndex` 的配置读取职责分开，避免继续扩大单类职责。

```text
run_spectroscopy / 后续实验发布器
        |
        | 原子发布成功
        v
Publication Registrar -----> index inbox（仅故障恢复）
        |                          |
        +---- 增量投影 ------------+
                   |
                   v
        web-read-model.sqlite
          |       |        |
          |       |        +--> 后台验证队列/状态
          |       +-----------> plot LOD/cache
          +-------------------> 列表、总览、详情 API

载体/生命周期事件 ----> storage catalog ----> 存储 API
         ^                                      |
         +--------- mutation 前完整重验 <-------+
```

### 4.1 组件职责

| 组件 | 职责 | 禁止事项 |
| --- | --- | --- |
| Publication Registrar | 发布后生成身份投影、登记新 run、写待处理事件 | 不修改已发布目录 |
| Shallow Reconciler | 一级目录对账、恢复漏登记或外部载体变化 | 不递归 `execution/` |
| ExperimentReadModel | 列表、总览、详情定位、缓存 revision | 不作证据 authority |
| Plot Projector | 校验 `plot_spec`、生成小数据 inline 或大数据 LOD | 不回写原数据集 |
| Verification Worker | 异步执行版本化完整 verifier，记录绑定身份的结果 | 不在 HTTP 请求线程执行 |
| Storage Catalog Adapter | 增量登记 carrier 状态、生命周期操作后同步 | 不信任 Web DTO 授权操作 |

### 4.2 文件布局

建议使用现有受信存储根，不向实验目录增加新文件：

```text
output/experiment-storage/
  web-read-model.sqlite
  web-read-model-revision
  index-inbox/
    <event-id>.json
  index-quarantine/
    <event-id>.json
  catalog.sqlite                     # 现有生命周期/载体 catalog
```

`web-read-model.sqlite`、revision 和 inbox 都是派生状态。删除它们不得删除实验，冷启动可以重建。
`index-inbox` 只承载“存在一个待投影 run”的恢复提示，不承载实验结果或验证结论。

## 5. 读模型 Schema

首版建议采用 SQLite/WAL 和编号 migration。字段名称是设计合同候选，实施时若与现有 storage
模型复用，应保持等价语义并在迁移记录中说明。

### 5.1 `runs`

| 字段 | 含义 |
| --- | --- |
| `run_id` | 主键；工作流 run ID |
| `workflow_id` / `artifact_version` | renderer 和 verifier 版本分派 |
| `workflow_sha256` / `receipt_sha256` | 不可变身份；回执不存在时为 null |
| `created_utc` / `status` | 列表排序和状态显示 |
| `experiment_kind` / `data_origin` | 实验种类和 model/hardware/demo 来源 |
| `targets_json` / `execution_mode` | 筛选字段和执行摘要 |
| `recommendation_applicable` / `recommendation_eligible` | 候选值 UI 摘要，不是更新授权 |
| `gate_summary_json` / `candidate_summary_json` | 列表级小摘要 |
| `carrier_state` / `carrier_alias` | 当前定位提示；最终载体状态以 storage catalog 为准 |
| `source_relative_path` | 服务端生成的受控定位，不返回为可写路径 |
| `projection_status` | `ready`、`pending`、`invalid_metadata`、`identity_conflict`、`missing` |
| `verification_status` | 第 11 节定义的异步状态 |
| `verification_binding_json` | verifier ID/version、身份哈希和验证时间 |
| `discovered_utc` / `projected_utc` / `last_seen_utc` | 运维可观察性 |
| `row_revision` | 单 run 的单调版本 |

索引至少包括：

- `(created_utc DESC, run_id DESC)`；
- `(workflow_id, created_utc DESC)`；
- `(projection_status, verification_status)`；
- 目标对象应使用规范化关联表，不依赖 `LIKE` 搜索 JSON。

### 5.2 `run_targets`

以 `(run_id, target_id)` 为主键，支持 Q1、Q2、C 以及后续任意 agent 的精确筛选。

### 5.3 `dataset_bindings`

记录 `dataset_id`、受控相对路径或归档 entry、原始 SHA-256、字节数、维度、点数、坐标名、指标名
和 Schema 版本。绑定来自 workflow/manifest，不允许通过递归寻找“可能的数据文件”。

### 5.4 `plot_bindings`

记录 `plot_id`、plot protocol version、plot type、对象/指标摘要、投影器 ID/version、数据模式
`inline|paged|tiled` 和数据集哈希集合。完整的小型 `plot_spec` 可以缓存为规范 JSON；大型数据只缓存
描述符和 LOD 索引。

### 5.5 `projection_events`

记录事件 ID、类型、run ID、预期身份、来源、创建时间、处理时间、重试次数和最后错误。事件用于
幂等恢复，不作为实验审计或生命周期审计的替代物。

### 5.6 `aggregate_counters`

保存总览需要的按状态计数和最近实验指针。它在同一 SQLite transaction 中随 `runs` 变化更新，
使 `/overview` 不再调用 `/experiments` 或扫描文件系统。

## 6. 缓存键与失效规则

### 6.1 不可变内容键

```text
run_identity = run_id + workflow_sha256 + receipt_sha256-or-none
detail_key   = run_identity + detail_projection_version
plot_key     = run_identity
             + sorted(dataset_id, dataset_sha256)
             + plot_protocol_version
             + plot_projector_version
             + lod_parameters
```

缓存键不得只使用 `run_id`、目录 mtime 或文件名。热目录转归档时内容身份不变，因此详情和 LOD
缓存可以复用；只有 carrier locator 变化。verifier 升级只使验证状态失效，不必使数值绘图缓存失效，
除非新 verifier 判定该身份无效。

### 6.2 失效条件

| 变化 | 处理 |
| --- | --- |
| 同一 run、同一工作流/回执哈希，载体热目录转归档 | 只更新 carrier locator/state |
| 同一 run 出现不同工作流哈希 | 标记 `identity_conflict`，隐藏依赖验证的操作 |
| 数据集哈希集合变化 | 使详情和全部 plot cache 失效；已发布 run 应同时告警不可变性破坏 |
| plot projector/protocol 版本变化 | 惰性重建相应 plot cache |
| verifier ID/version 变化 | 状态变 `verification_stale`，进入后台队列 |
| 载体暂时不可见 | 标记 `missing`，保留诊断行，不立即删除读模型记录 |
| storage catalog revision 变化 | 刷新存储投影；不重新计算数据图 |
| 配置/决策引用变化 | 只刷新引用与候选更新门禁，不使实验数据缓存失效 |

## 7. 增量发现、发布登记与冷启动

### 7.1 发布登记

对于项目内受控发布器，索引更新不能依赖 Web 下一次递归发现。建议顺序为：

1. staging 内工作流按既有流程完成写入和验证；
2. staging 原子发布为终态运行目录；
3. Registrar 从已经发布的顶层小型工件构造 `PublishedRunProjection`；
4. 在一个读模型 transaction 中幂等 upsert `runs`、targets、dataset/plot bindings 和 aggregate；
5. 同步增量登记 storage catalog 的 hot carrier，使最新 v0.3 run 同时出现在存储页；
6. 提交后通知进程内订阅者刷新；Web 轮询或主动刷新即可读取；
7. 任一步索引写入失败时，保留已发布 run，写入原子 `index-inbox` 事件并报告 index degraded；
8. Registrar 重试以 `(event_id, run_identity)` 幂等，不能产生重复行或重复 catalog revision。

步骤 4 和 5 跨两个派生数据库时不宣称全局事务。inbox 事件必须一直保留到两个投影都确认同一身份，
因此崩溃后可以补齐其中一边。索引失败不改变实验发布成功的事实。

### 7.2 一级目录对账

Shallow Reconciler 只允许：

- 枚举 hot root 的直接子目录；
- 枚举 archive/trash root 的直接载体；
- 对新增或 locator 变化的载体读取有界的顶层 identity/manifest；
- 对比读模型、storage catalog 和一级目录 carrier 集合；
- 把未知、重复和异常对象投影为 blocker。

它不得进入 `execution/`、Stage 证据目录或递归计算目录大小。一级目录快照键至少包含安全文件名、
载体类型、文件身份/stat 信息和已解析的工作流哈希；mtime 只能触发复查，不能作为证据身份。

触发时机：服务启动、inbox 未清空、存储 mutation 完成、管理页显式“刷新目录”，以及带抖动的
低频后台周期。普通 GET 只查询数据库，不等待对账。

### 7.3 冷启动重建

当读模型不存在、Schema 不兼容或完整性检查失败时：

1. 在新临时数据库中运行 migration；
2. 浅枚举各一级载体；
3. 读取每个 run 的有界 workflow/manifest、绑定数据集头部和已有验证回执；
4. 合法元数据投影为 `ready` 或 `verification_pending`；损坏对象单独投影为 invalid；
5. 计算 aggregate，执行 SQLite integrity check；
6. 原子替换旧读模型，revision 单调推进；
7. 后台按优先级补做完整证据验证和大型 plot cache，不阻塞列表上线。

冷启动重建不得仅因某个 run 损坏而整体失败；只有根路径不可信、数据库替换失败或全局身份冲突
无法隔离时，服务进入 degraded。已有 storage catalog 的完整重建仍按 Stage 7.1.8 执行，但不应由
实验列表 GET 隐式触发。

## 8. 列表与总览读模型

### 8.1 实验列表

默认排序固定为 `(created_utc DESC, run_id DESC)`。筛选应在后端执行，首版支持：

- `q`：run ID、工作流和规范化对象 ID；
- `target`：可重复参数，例如 `target=Q1&target=Q2`；
- `workflow_id`；
- `verification_status`；
- `recommendation_state=data-only|eligible|blocked`；
- `storage_state`（从最近 storage 投影读取）。

列表行只包含当前前端表格和筛选需要的小字段，不包含 dataset、完整 plot spec、证据路径清单或 raw
workflow。列表请求不得读取实验目录。

### 8.2 总览

`/overview` 从 `aggregate_counters`、配置管理摘要和最近 5 条索引行组合，不再调用全量
`experiments()`。计数与 run upsert 在同一 transaction 中更新；重建时从 `runs` 重算。

总览可以返回 `read_model_revision`、`index_lag_seconds`、`pending_projection_count` 和
`verification_queue_depth` 供诊断，但前端主界面不应把内部索引细节作为用户主要内容。

## 9. API 兼容与分页演进

### 9.1 兼容原则

现有 `/api/v1` 路径和现有字段在本轮保持。新增字段为 additive；前端不得依赖未声明的字段顺序。
在切换期间，旧实现和新读模型对同一合法 run 的公共摘要必须通过 contract test 等价。

### 9.2 列表分页

扩展现有接口：

```http
GET /api/v1/experiments?limit=50&cursor=<opaque>&target=Q1
```

```json
{
  "schema_version": "0.2",
  "items": [],
  "page": {
    "limit": 50,
    "next_cursor": null,
    "has_more": false,
    "total": 12
  },
  "read_model_revision": 18
}
```

采用 keyset pagination，不使用随插入漂移的 offset。cursor 是服务端生成和校验的不透明值，至少
绑定排序版本、最后一条 `created_utc/run_id` 和过滤器摘要。伪造、过期或过滤器不匹配返回 400，
不能被解释成路径或 SQL。

迁移顺序：

1. `/api/v1/experiments` 无参数暂时保留现有 `{"items": [...]}` 语义；
2. 新 Web 前端显式使用 `limit=50` 并读取 `page`；
3. 观测旧客户端使用情况后，再通过新 API version 引入强制分页，不能在同一 v1 合同内静默截断。

### 9.3 详情

现有接口保持：

```http
GET /api/v1/experiments/{run_id}
```

对于当前小型 spectroscopy 数据，它继续返回现有 renderer 所需的 `datasets`、`plot_specs`、
analysis、gates 和 candidates。实现改为按 run 主键定位并只读取绑定工件，不再先生成全量列表或
完整验证证据。

大型数据演进使用：

```http
GET /api/v1/experiments/{run_id}/plots
GET /api/v1/experiments/{run_id}/plots/{plot_id}/data?max_points=2000&x_min=...&x_max=...
GET /api/v1/experiments/{run_id}/plots/{plot_id}/tiles/{level}/{tile_x}/{tile_y}
GET /api/v1/experiments/{run_id}/plots/{plot_id}/points/{point_id}
```

小数据仍可 inline，避免当前 41 点扫描被拆成多次请求。大型数据返回 descriptor/data URL，而不把
全部点塞进详情 JSON。是否 inline 由点数、网格大小和序列化字节数三重门限决定。

### 9.4 HTTP 缓存

列表 ETag 绑定 `read_model_revision + filters + cursor`；详情 ETag 绑定 `detail_key`；plot 数据 ETag
绑定 `plot_key`。支持 `If-None-Match` 返回 304。不得为 verification pending/failed 的响应错误地
复用旧 `verified` ETag。

## 10. `plot_spec` 与数据服务

Stage 7.1.7 的 `plot_spec` 继续是 Web 绘图协议。当前小型 line/scatter/heatmap 的 1.0 inline
结构保持兼容。后续协议只增加数据定位能力，不改变原始数据 authority。

### 10.1 小数据

满足以下全部条件时可以 inline：

- 点或 cell 数低于配置门限；
- 规范 JSON 字节数低于响应门限；
- 所有值有限且对象、指标和 group 引用合法；
- 数据集哈希与 read model binding 一致。

当前 41 点频谱属于小数据，详情直接返回完整坐标，点击和 300 ms hover 均显示精确原始值。

### 10.2 大型一维数据

一维扫描使用“视窗 + 目标点数”请求。首选确定性的 min/max envelope：

1. 保留视窗首尾点；
2. 按 x 范围分桶；
3. 每桶按原始顺序返回 y 最小和最大点；
4. 同一对象的一组相关指标使用共享桶边界，避免 P0/P1/leakage 横坐标错位；
5. 返回 `source_index`、原始 point ID、桶范围和 `is_aggregate`；
6. 点击降采样点可通过 point API 回查精确记录。

该算法保留谱峰和极值，比简单每 N 点抽样更适合校准曲线。若 x 非单调，投影器不得静默排序改变
实验时序；应使用原序列 LOD 或显式声明 x-sorted view。

### 10.3 二维与 heatmap

规则网格建立确定性多级 LOD，每一级由 2x2 cell 聚合得到，并同时保存 `mean/min/max/count`。
默认颜色显示 mean，tooltip 必须标明当前是 LOD 聚合值；点击后通过精确 cell API 读取原始 x、y、
value。坐标轴向量与 row-major value 块分开返回，避免为每个 cell 重复字段名。

大型 heatmap 使用固定 tile 尺寸和 level/x/y 寻址。tile cache 键必须绑定数据集哈希、指标、对象、
聚合函数、level 和 tile 坐标。对不规则网格，首版只能使用明确声明的 binning 规则；不能猜测规则
网格或用插值值冒充实验点。

### 10.4 派生缓存

LOD 和 tile 是可丢弃派生数据，建议以压缩 BLOB 存入独立 plot cache 表或独立可重建数据库，避免
在 `output/experiments` 生成大量新小文件。缓存写入使用 transaction，失败只降低显示性能，不改变
实验结论。缓存大小设置上限并按最近访问回收；回收不需要生命周期删除审计，因为它不是证据。

## 11. 后台验证

### 11.1 状态模型

| 状态 | 含义 |
| --- | --- |
| `verified` | 指定 verifier ID/version 已对当前 run identity 完整验证成功 |
| `verification_pending` | 已投影可显示元数据，完整验证排队中 |
| `verification_running` | 后台 worker 正在验证 |
| `verification_stale` | verifier 版本变化或验证绑定已过期 |
| `verification_failed` | 对当前身份验证失败，记录稳定错误码 |
| `unverified_generic` | 没有受支持 verifier 的通用工作流，仅按 plot schema 展示 |

`verified` 必须保存 verifier ID/version、workflow/receipt hash、开始/完成时间和结果摘要。身份或 verifier
版本变化后不得沿用旧结论。

### 11.2 调度规则

优先级建议为：用户显式重验 > 生命周期 mutation 前置验证 > 新发现 run > verifier 升级后的历史 run
> 定期抽检。后台并发默认 1，避免完整 evidence walk 与 QuTiP/实验运行争抢磁盘；可配置速率和暂停，
但不得由 Web 普通用户任意指定 verifier 或文件路径。

普通列表、总览和详情不等待后台验证。依赖验证的候选配置更新按钮仅在“同一身份 verified 且其他
候选门禁通过”时启用。存储 mutation 仍按 Stage 7.1.8 在服务端同步重验关键前置条件，不能只看后台
状态。

### 11.3 故障可见性

worker 崩溃后，超时 running 任务回到 pending 并增加 attempt；超过重试上限进入 failed，需要显式
重验或身份变化才能恢复。错误响应和日志只暴露稳定错误码和受控相对身份，不向浏览器返回任意系统
绝对路径、堆栈或证据内容。

## 12. 存储目录一致性

当前实验列表与 storage catalog 是两套发现路径，必须建立以下一致性合同：

1. 每个已成功登记的 hot v0.3 run 应同时存在 Web read row 和 storage catalog row；
2. 两边以同一 `run_id/workflow_sha256/receipt_sha256` 对账；
3. 发布登记缺一边时 inbox 不消费，health 报 `projection_lag`，reconciler 补齐；
4. archive、restore-hot、trash、trash restore 成功后，storage mutation 在同一锁域内更新 catalog，
   再投影 Web carrier state；Web 投影失败不回滚已提交生命周期事实，而是留下待处理事件；
5. storage 页读取 catalog，实验页读取 Web read model；两个 DTO 的 identity contract test 必须一致；
6. storage catalog 的 `allowed_actions` 仍是提示，mutation 重新检查当前 catalog revision、预期 workflow
   hash、引用、manual keep、载体身份、no-follow 和完整 verifier；
7. 外部手工添加、移动或删除目录会被 shallow reconcile 标为 new/missing/conflict，不自动推断为合法
   生命周期操作；
8. 已存在的 legacy run 可以继续展示，但没有相应 storage verifier 时必须保持 blocker，不伪装升级。

增量登记可以使用发布时已生成并绑定的 manifest/verification 数据填充逻辑大小、文件数和验证状态；
allocated bytes 暂缺时应明确标记 estimated/pending，不能为了 storage 页立即显示而在 Web GET 中递归
盘点整个证据树。

## 13. 前端加载策略

前端取消全局无条件 `refresh()`，改为路由级数据依赖：

| 路由 | 必需请求 |
| --- | --- |
| `#/overview` | health（可缓存）+ overview |
| `#/experiments` | 分页 experiments |
| `#/experiments/{run_id}` | 单个 detail；大型 plot 按需加载 |
| `#/configurations...` | configuration-management 及当前页面所需配置 |
| `#/storage` | experiment-storage |
| `#/trash` | experiment-trash |

路由切换使用 `AbortController` 取消已不需要的请求，避免慢接口返回后覆盖新页面状态。列表搜索输入
防抖并将筛选发送到后端；翻页追加结果，不一次读取全部历史实验。详情先显示标题和元数据，再加载大
plot；加载、空、pending、verification failed 和 index degraded 均有独立状态。

mutation 完成后只失效相关资源。例如 trash 成功后刷新 storage/trash 和该 run 的 carrier 摘要，不再
重新拉取全部配置与实验。前端内存缓存同样使用 ETag/identity，不能只按 URL 永久缓存。

## 14. 并发、故障恢复与运维

### 14.1 并发

- 多个实验可以并行发布，SQLite 写入以短 transaction 串行化；耗时 JSON 解析和 plot 投影在事务外；
- 同一 run 的相同身份登记幂等；不同身份登记立即形成 conflict，不允许 last-write-wins；
- 后台 verifier 通过 compare-and-set 领取任务，绑定 row revision 和 run identity；完成时身份不一致则
  丢弃结果并重新排队；
- rebuild 在新数据库完成后原子替换，服务读取旧的完整快照或新的完整快照，不读取半成品；
- storage mutation 与 catalog revision 保持现有乐观并发控制，412 后前端重新读取当前行。

### 14.2 故障点与恢复

| 故障点 | 恢复策略 |
| --- | --- |
| 原子发布后、读模型登记前崩溃 | 一级目录对账发现；inbox 存在时优先重放 |
| Web read model 已登记、storage catalog 未登记 | inbox 保留，补齐 catalog 后消费 |
| storage mutation 已完成、Web carrier 投影失败 | 生命周期事实保留，事件重放更新 Web |
| SQLite 损坏/丢失 | 隔离旧 DB，执行浅层冷启动重建 |
| plot cache 损坏 | 删除对应 cache key 并由原数据集重算 |
| 完整 verifier 崩溃 | running lease 超时后回 pending |
| 载体被外部移动 | 标记 missing/conflict，禁止依赖身份的操作 |
| inbox 事件损坏 | 移入 quarantine，health degraded；浅层对账仍尝试恢复 run |

### 14.3 健康指标

至少记录：请求耗时、SQLite query 耗时、投影队列深度、最老待投影年龄、最后成功对账时间、发现
run 数、identity conflict 数、后台验证队列/失败数、plot cache 命中率、响应点数/字节数，以及
`os.scandir` 进入 evidence 子树的违规计数（测试环境必须为 0）。日志不得逐个打印数万证据文件。

## 15. 性能 SLO 与容量约束

在本地 Windows、SQLite 热缓存、50 条默认页、普通 41 点频谱详情的参考环境中：

| 场景 | 目标 |
| --- | ---: |
| health p95 | `< 50 ms` |
| 实验列表 warm p95 | `< 200 ms` |
| overview warm p95 | `< 300 ms` |
| 小型实验详情 warm p95 | `< 500 ms` |
| storage 列表 warm p95 | `< 300 ms` |
| 新实验发布到列表可见 p95 | `< 1 s` |
| 前端实验列表路由可交互 p95 | `< 800 ms`，不含首次静态资源下载 |

关键扩展性门禁不是只测绝对时间，而是：给同一组 100 个 run 每个增加 0、1 万和 10 万个不可读取
evidence 占位文件时，列表/总览/详情请求的文件访问集合和查询复杂度保持不变。普通 GET 的实现测试
必须证明没有进入 evidence 子树。

冷启动浅重建与 run 数线性相关，但与每个 run 的 evidence 文件数无关。性能报告分别记录 12、100、
1,000 个 run 的 cold/warm p50/p95、读字节数和打开文件数；不以开发机单次最快值代替 SLO。

默认响应约束建议为：列表页 50、最大 200；inline plot 规范 JSON不超过 1 MiB；大型一维默认最多
2,000 个显示点；heatmap tile 使用固定 cell 上限；所有上限在服务端执行。具体常量在实现和基准测试
后冻结为配置，不开放成任意浏览器路径或内存无限制参数。

## 16. 测试矩阵

### 16.1 单元与合同测试

| 类别 | 必测内容 |
| --- | --- |
| Schema/migration | 新建、逐版本迁移、重复迁移、未来版本拒绝、integrity failure |
| 投影 | 已知 v0.3、legacy、generic plot spec、损坏 JSON、非有限数、重复 key、缺字段 |
| 身份 | 同 run 同哈希幂等、不同哈希冲突、数据集哈希变化、回执缺失/变化 |
| 缓存 | detail/plot key、verifier 升级、carrier 迁移不误失效、数据变化正确失效 |
| 分页 | 稳定排序、并发插入、空页、最大 limit、cursor 伪造/过滤器不匹配 |
| 一维 LOD | 首尾/极值保留、共享桶、非单调 x、point ID 回查、确定性 golden |
| 二维 LOD | 2x2 聚合、边界 tile、NaN 拒绝、min/max/mean/count、精确 cell 回查 |
| API 兼容 | 旧字段保持、错误码、ETag/304、generic renderer、archive detail |
| 前端 | 按路由请求、取消旧请求、分页、筛选、loading/error/empty/pending 状态 |

### 16.2 安全测试

- symlink、junction、reparse point、硬链接、大小写冲突和路径穿越；
- 超大 JSON、深层对象、压缩炸弹式归档条目、伪造长度和无限点数参数；
- 浏览器传入绝对路径、SQL/cursor 注入、未知 verifier 和未知 artifact version；
- stale read model 试图启用 trash/archive/apply 时，服务端重新门禁并拒绝；
- identity conflict、reference scan unknown 和 verifier failure 均 fail closed；
- 正式 `output` 写入守卫：测试前后文件数、哈希和 mtime 不变。

### 16.3 集成与故障注入

1. 在临时根创建 12 个 run、33,162 个 evidence 文件的现状规模，验证功能与 SLO；
2. 创建相同顶层元数据但 10 倍 evidence 文件，spy/ACL 令 evidence 不可读，普通 GET 仍成功；
3. 并行发布两个 run，列表和 storage 同时出现且没有重复身份；
4. 在发布后、Web upsert 后、storage upsert 后分别注入崩溃，重启后 inbox/reconcile 收敛；
5. hot -> archive -> restore-hot -> trash -> restore 全流程后，两套索引身份一致；
6. catalog/read DB 损坏、revision 文件丢失、inbox 半写和 SQLite busy 的恢复；
7. 后台 verifier 限流、崩溃 lease、版本升级和验证失败状态；
8. Chrome/移动视口验收列表、详情、line/scatter/heatmap、300 ms hover、精确点回查；
9. 当前全量项目回归，并单独报告与正式 Stage 1/2 工件缺失相关的环境失败，不能用生成正式工件掩盖。

### 16.4 性能验收方法

- 预热与冷启动分开测量，每项至少多次运行并报告 p50/p95/max；
- 记录 CPU、读字节、打开文件数、SQLite query plan 和返回字节数；
- 用测试钩子禁止 `os.scandir`/open 进入 `execution/`，比只看耗时更强；
- 浏览器用真实路由导航测量，不只调用后端函数；
- 性能失败不得通过减少验证安全门禁解决，应该移动验证时机或复用已绑定结果。

## 17. 迁移与回滚

### 17.1 分阶段迁移

1. **观测阶段**：为现有接口增加耗时和文件访问计数，冻结问题基线；
2. **旁路构建**：创建独立 read DB，浅层投影现有 run，不改变现有 API 数据源；
3. **影子比对**：对合法 run 比较旧 DTO 与新 DTO，差异进入报告，不访问正式证据做破坏性操作；
4. **发布接入**：受控发布器写增量登记/inbox，最新 run 同步进入 storage catalog；
5. **API 切换**：列表、总览、详情依次切到 read model，保留按启动参数回退旧实现的短期能力；
6. **前端切换**：启用路由级加载和显式分页；
7. **后台验证/LOD**：开启受限 worker 和大型绘图数据接口；
8. **收敛**：SLO、故障注入和正式数据只读检查全部通过后，删除旧递归 Web 请求路径。

### 17.2 回滚

回滚只切换代码路径或停用新 read DB；不回滚实验发布、归档、回收或配置事实。新 DB、plot cache 和
inbox 均为派生状态，可以保留用于诊断或在服务停止后删除重建。Schema migration 采用新文件原子替换，
不就地降级数据库。

短期旧路径回退只用于上线保护，并会恢复已知慢性能；它不得绕过 storage/verifier 安全门禁。若新
Registrar 已写入 catalog，旧代码应能忽略新增 additive 表/字段或通过兼容 adapter 查询。回滚演练
必须证明正式实验目录字节无变化。

## 18. 分阶段实现边界

### 18.1 Tranche 1：本次实际交付

1. 对热目录建立线程安全、可失效的进程内浅层读缓存；只发现 `output/experiments` 和兼容自定义根的
   直接子目录，不递归进入证据树；
2. 对 spectroscopy calibration、spectroscopy scan v0.3 和 generic `plot_spec` 保持现有 DTO 兼容；
3. 通过 workflow、dataset、receipt、manifest 和 verification report 的顶层哈希闭包建立 Web 投影，
   发布文件在读取中连续变化时 fail closed；
4. 列表、总览和热目录详情的普通请求不访问 `execution/`，同一稳定投影复用缓存；
5. 前端按路由加载资源并去重并发请求；迟到详情响应不能覆盖已经切换的路由；
6. storage catalog 使用顶层载体 token 发现新实验，在后台重建并向 UI 暴露 `refreshing`；刷新期间
   前后端均冻结 archive/trash/restore/keep 操作；
7. 修复“实验页有、存储页没有”的现有 v0.3 run 收录问题；
8. 保持小型 spectroscopy 图的完整精确点、对象/指标筛选和 300 ms 悬浮交互；
9. 完成无 evidence 递归断言、缓存失效、并发读取、catalog 故障/竞态、API/UI 和正式数据性能验收。

Tranche 1 的缓存是进程内派生状态，服务重启后通过浅层文件重新建立。归档详情仍在冷读时运行完整
verifier，但一个请求只验证一次并从同一已验证 reader 读取所需文件；大归档的普通 GET 不阻塞目标
要在 Tranche 2 的持久化归档投影完成后才能宣称达成。

### 18.2 Tranche 2：本次实际交付

1. 建立独立 `web-read-model.sqlite`、编号 migration 和原子 schema 升级；
2. 实现实验发布 Registrar、幂等 inbox、启动/周期 shallow reconcile 和故障恢复；
3. 列表、总览、热目录与归档详情全部改为主键/聚合读取，归档完整验证只在发布、后台校验和
   mutation 门禁执行；
4. `/api/v1/experiments` 增加可选 keyset pagination、稳定排序、默认最近 50 条和 ETag；
5. carrier 更新绑定完整三元身份；archive/trash/restore 后最终收敛，读模型丢失时可从已验证载体恢复；
6. 实现一维 LOD、确定性降采样、原始点回查、前端按需加载和 300 ms 悬浮交互；
7. 实现数据库/sidecar 链接攻击拒绝、损坏数据库隔离重建、坏事件/坏 archive 失败隔离和线程确定退出；
8. 前端搜索与状态筛选发送到后端，250 ms 防抖；cursor 绑定筛选，快速输入和路由切换会取消旧请求；
9. GET 使用有界 ETag 缓存，304 复用结构化克隆，避免渲染态污染缓存。

### 18.3 后续延期项

二维超大 heatmap 的完整 tile cache UI、绑定 verifier 版本并带 lease/retry 的通用后台重验 worker、
外部进程消息队列、多用户权限、远程存储、历史 artifact 原地迁移、永久 purge、自动清理和通用硬件
实时流不属于当前交付。二维协议和测试向量在本设计中冻结，待首个真实二维校准实验接入时实现；
不得宣称已经支持尚未落地的 tile 服务。

### 18.4 Tranche 1 验收记录

1. 合并 Web/API/UI 回归 78 项通过，storage/archive 安全回归 377 项通过；
2. 正式数据保持 12 个实验、33,162 个文件、57,033,588 字节，验收未修改实验载体；
3. 正式服务热请求观测：实验列表约 5 ms、overview 约 40 ms、小型详情约 135--155 ms、
   storage 约 6--8 ms；12 路并发列表请求全部返回 200；
4. 浏览器验收确认实验列表、Q1/Q2 详情、41 点数据、统一图表和存储操作状态正常，无横向溢出；
5. 独立验收结论为 GO；剩余能力仅为第 18.2、18.3 节明确的后续增量。

### 18.5 Tranche 2 验收记录

1. Web、storage、archive、catalog、inventory、lifecycle、publication、policy 和 adversarial
   合并验收共 511 项通过；新增读模型端到端与攻击面测试 19 项通过；
2. 正式读模型收录 12 个实验，`web-read-model.sqlite` 为 1,150,976 字节；服务健康状态为
   `ok`，inbox pending 为 0，quarantine 为 0；
3. 正式服务预热 20 次观测：列表 p50 15.23 ms、p95 15.65 ms、max 19.44 ms；详情 p50
   54.58 ms、p95 60.43 ms、max 64.72 ms；overview p50 53.02 ms、p95 62.80 ms、
   max 69.83 ms；相同 ETag 的条件请求返回 304；
4. 浏览器实机验收确认桌面和 390 px 移动视口均无横向溢出；Q1/Q2 曲线、对象/指标选择、
   300 ms 悬浮坐标和精确数据点选择正常，浏览器控制台无 warning/error；
5. 独立故障注入覆盖数据库损坏、链接攻击、坏 inbox、坏 archive、并发发布、生命周期收敛和
   线程退出；普通列表、总览和详情读取只访问持久化投影，不递归访问 `execution/`；
6. 目录型 trash 可从载体恢复读模型；以单文件 `.sqrun` 保存的 trash 载体恢复、通用后台重验
   worker 和二维超大 heatmap tile 服务继续按第 18.3 节延期，不能作为当前能力对外声明。

## 19. AI 团队工作包

### WP-A：读模型与投影

负责 SQLite Schema/migration、不可变缓存键、projection、浅层重建、aggregate 和主键详情读取。
交付单元测试与“禁止进入 execution”文件访问测试。不得改动工作流数值或证据文件。

### WP-B：发布、storage 与恢复

负责 Registrar、幂等 inbox、发布后双投影、storage catalog 增量登记、浅层对账以及 archive/trash/restore
后的状态同步。重点测试每个崩溃点和 identity conflict；所有 mutation 保留 Stage 7.1.8 完整门禁。

### WP-C：API、绘图与前端

负责兼容 `/api/v1` DTO、keyset pagination、ETag、路由级加载、AbortController、列表分页和一维 LOD
接口。当前 41 点图保持完整数据和既有选择/300 ms hover 行为。

### WP-D：独立验证与性能

不参与核心实现，负责临时根 fixture、安全攻击面、33,162 文件和扩展规模基准、并发/故障注入、
浏览器桌面/移动验收和全项目回归。必须报告实际命令、p50/p95、访问文件集合和剩余风险。

### 19.1 协作门禁

1. WP-A 先冻结 Schema、DTO 和 cache key；WP-B/WP-C 通过合同实现，不能各自定义 run identity；
2. WP-B 提供发布和 lifecycle 事件测试 fixture，WP-A 不扫描真实正式目录补数据；
3. WP-C 的前端不得把 `allowed_actions` 当安全授权；WP-D 必须验证服务端拒绝 stale 操作；
4. 每个工作包只能修改约定文件，合并前由项目经理检查工作树和跨包冲突；
5. WP-D 验收失败必须回到对应工作包修复后重测，不能以“单元测试通过”替代端到端完成。

## 20. 验收清单

- [x] 打开实验列表、总览或详情时没有递归访问任一 `execution/`；
- [x] 当前 12 个实验的列表字段、详情数值、候选值和图形与切换前一致；
- [x] 新 v0.3 实验通过 inbox 和浅对账最终出现在实验列表和 storage；
- [x] catalog 已存在时，新 run 仍可被增量收录；
- [x] 同 run 不同工作流哈希形成 conflict，不能覆盖旧行；
- [x] archive、trash、restore 后两套索引最终收敛，mutation 仍重新执行安全门禁；
- [x] 前端实验路由不再请求配置管理接口，配置路由不再请求实验列表；
- [x] 分页排序稳定，旧 `/api/v1` 公共字段兼容；
- [ ] 后台验证状态绑定确切身份和 verifier version，普通 GET 不等待完整验证；
- [x] 小型一维 plot 仍返回精确点，大型一维 LOD 保留首尾和极值并可回查原始点；
- [x] 自动化故障与生命周期测试使用临时根，不修改正式实验载体；
- [ ] 第 15 节 SLO 有可复现报告，且证据文件数增长不增加普通 GET 的文件访问集合；
- [x] 文档和 README 只声明实际完成的能力，二维 tile 等延期项明确标为未实现。
