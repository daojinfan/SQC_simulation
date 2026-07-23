# Stage 7.1.8 实验数据存储、归档与删除详设

状态：AI 团队评审后的设计冻结候选；本轮只交付第 0 节列出的 v1 基础能力。其余正文描述的是冻结目标合同，不应被解读为全部已经实现。

## 0. 本轮实现状态（v1 基础能力）

本节是本轮代码交付的状态边界。后续章节保留完整生命周期的设计合同、字段和门禁，供后续阶段实现；除非本节明确列为“已实现”，不得据此宣称该能力已经上线。

### 0.1 已实现

1. **确定性归档格式。** 已实现纯格式 `.sqrun` writer、reader 和 verifier：ZIP_STORED、ZIP64、确定 entry order/时间/权限、严格路径和 ZIP 结构限制，且 `run/**` 原始字节保持不变。归档 raw SHA 由外部载体取得，不写回归档自身。
2. **v0.3 证据验证。** 热树与归档均通过受控 EvidenceReader/verifier registry 验证 evidence closure；未知 verifier、验证失败、越界读取和证据不完整均 fail closed。归档读取为有界流式读取，不任意解压到磁盘。
3. **可重建 catalog。** SQLite/WAL catalog 是派生缓存，具备编号 migration、revision/ledger、只读查询和原子 rebuild；它盘点 hot、`.sqrun`、trash 与 tombstone，并将未知、损坏、重复或冲突对象作为 blocker，而非可删除对象。
4. **保留和引用的基础交叉检查。** 已实现 manual keep，以及 references/lifecycle typed snapshot 的 fail-closed 投影；引用、manual keep 或未知状态会阻断相应的破坏性操作。
5. **可恢复的载体迁移。** 已实现 hot 到 archive、archive 到 hot、hot/archive 到 recoverable trash，以及 trash restore。归档和回收站载体均经过身份、哈希和 no-follow 校验；本轮没有永久删除操作。
6. **Web v1 入口。** 已实现实验存储与回收站的只读查询和 keep/archive/restore-hot/trash/restore 操作入口。Web 仅管理数据，不执行实验；每个 mutation 由服务端重新检查 revision、workflow identity、保留/引用和载体状态，成功后只 rebuild 一次 catalog。Web 不开放 purge。

### 0.2 当前 Web/API 交付边界

当前只读入口为 `GET /api/v1/experiment-storage`、`GET /api/v1/experiments/{run_id}/storage`、`GET /api/v1/experiment-trash` 和 `GET /api/v1/experiment-trash/{run_id}`。状态变更仅限 keep、archive、restore-hot、trash 与 trash restore；请求使用 actor、理由、预期 catalog revision、预期 workflow hash，keep 额外提供布尔值。响应错误稳定包含 `code`、`status`、`error`、`details`。

浏览器不会提交路径、archive root、`allowed_actions` 或永久删除授权。`allowed_actions` 仅是 UI 可用性提示，服务端不信任它。GET 在初始化后的 catalog 上只读查询，不会因一次查询推进 catalog revision；启动配置的热、storage 和 archive 根均须经过逐组件 no-follow/reparse 检查。

### 0.3 后续阶段（尚未实现）

以下章节中描述的能力仍是后续阶段，不属于本轮 v1 交付：

1. 自动保留策略的定时/批量执行；现有策略 schema 或盘点信息不等同于自动执行器。
2. cleanup plan 的生成、preview、审批和 apply。
3. 永久 purge、物理空间清除及其操作入口。
4. 配额、空间准入、reservation 和基于它们的写入拒绝策略。
5. 旧版实验载体、历史目录或旧 archive 格式的迁移。

因此，本设计中涉及 cleanup、purge、quota/admission 或 migration 的接口、状态机和验收条款，均不得作为当前 Web 或 Python API 的可用功能进行宣传。

## 1. 目的与边界

本设计解决校准实验长期运行后大量小文件导致的磁盘容量、目录扫描性能和安全删除问题。当前
Q1/Q2 并行、41 点频谱实验包含约 3652 个文件，文件逻辑大小约 5.76 MiB，在 4096 字节簇的
NTFS 上实际分配约 17.03 MiB。现有 8 个完成实验和 1 个运行中暂存实验的逻辑大小约 32 MiB，
估算实际分配约 94 MiB。主要放大来自每个扫描点重复保存 Stage 4.1、Stage 5.1 和线路执行证据。

本阶段必须实现：

1. 已发布实验的完整证据归档，不降低字节级可验证性；
2. 用户长期保存、配置引用和校准决策引用保护；
3. 可恢复删除、永久清除和完整审计；
4. 基于实际分配空间的配额、清理计划和实验运行前容量准入；
5. Web 中的存储查看、归档、保留、回收、恢复和清理操作；
6. 现有实验目录的无损迁移；
7. 独立测试、故障注入、浏览器验收和全项目回归门禁。

本阶段不实现：

- 网络文件系统、对象存储或云端归档；
- 多用户权限和审批流；
- 在归档过程中改变 QCIS、波形、Hamiltonian、QuTiP 或实验数据数值；
- 从损坏证据中猜测或修复实验结果；
- 自动删除仍被引用、用户长期保存或引用状态未知的实验；
- 把 SQLite 目录当作证据权威；
- 断点续跑或从暂存目录继续执行缺失扫描点。

## 2. 与现有契约的关系

Stage 6 规定终态运行目录不可原地修改，运行目录是证据权威，SQLite 仅为可重建的查询缓存。
本设计保留该原则：

1. `hot` 运行目录发布后绝不在原目录内增加、替换或删除文件；
2. 归档会创建一个新的、经过完整验证的派生证据包；
3. 只有派生证据包验证通过并写入生命周期审计后，原运行目录才可整体移入清理区；
4. 任意时刻至少存在一份已验证的完整证据；
5. Web 和 Python 读取层同时支持热目录和归档包，但向上返回相同实验 DTO；
6. SQLite 可从热目录、归档包、生命周期事件和删除墓碑重建，损坏或丢失不能影响证据本身。

当前 `run_spectroscopy()` 默认把实验写入 `output/experiments`，运行时先创建
`.spectroscopy_<run-id-prefix>`，完成后发布为 `qubit_spectroscopy_<uuid>`。该入口和默认路径保持兼容。

### 2.1 前置 P0 关闭记录

AI 团队在实现归档迁移和可恢复删除前识别的五项 P0 已关闭，处理方式如下：

1. `qubit_spectroscopy_scan_v1` v0.3 已把 `execution/` 的 exact inventory、逐文件哈希、顶层
   verification report 和 receipt 纳入证据闭包；热目录与归档共用受控 verifier registry；
2. Stage 7 失败、取消、中断和发布失败使用持久恢复证据，不再通过静默递归删除掩盖失败；
3. 实验存储 catalog 只接纳版本分派 verifier 通过的载体；未知、损坏、重复或身份冲突载体进入
   `invalid`/blocker 视图，不能成为删除授权；
4. runtime v0.2 authority/source 漂移已通过冻结 authority v2 和相应回归恢复一致性；
5. 引用保护已改为结构化 typed reference graph。读取、Schema、哈希或身份异常统一投影为
   `reference_unknown`，并阻断破坏性操作。

关闭方式没有放宽 verifier。对应实现继续受本设计的归档格式、生命周期、引用和独立测试门禁约束。

## 3. 核心不变量

以下不变量是实现和测试的最高优先级门禁：

1. **字节不变**：归档中每个原始文件解压后的字节必须与热目录完全一致；
2. **身份不变**：`run_id`、工作流哈希、数据集哈希和回执哈希在归档前后不变；
3. **至少一份**：删除热目录前必须存在已关闭、重读并验证通过的归档包；
4. **引用失败关闭**：引用扫描失败、重复身份、未知 Schema 或验证失败时禁止破坏性操作；
5. **运行隔离**：运行中、finalizing、发布失败待恢复的目录禁止归档或删除；
6. **不可静默删除**：所有移入回收站和永久清除都必须产生不可变审计事件；
7. **可恢复优先**：普通删除只移入回收站；永久清除只能作用于回收站对象；
8. **计划绑定**：批量自动清理只能执行未过期且前置哈希仍匹配的清理计划；
9. **路径封闭**：不跟随 symlink、junction、reparse point、硬链接或 ZIP 中的越界路径；
10. **正式数据隔离**：测试和故障注入只使用独立临时根，不能触碰正式 `output`；
11. **保护优先于配额**：空间不足时拒绝新实验，不能通过删除受保护实验强行腾出空间；
12. **完整披露**：部分成功、重试状态、重复副本和清理失败必须在 Web/API 中可见。

## 4. 术语与状态机

### 4.1 存储状态

| 状态 | 权威载体 | Web 可见性 | 允许的下一状态 |
| --- | --- | --- | --- |
| `running` | `.spectroscopy_*` 或 Stage 6 staging | 运行状态页，不作为完成实验 | `hot`、`recovery_required` |
| `recovery_required` | 暂存/锁/隔离证据 | 管理页告警 | 显式恢复记录或隔离清理 |
| `hot` | 完整不可变运行目录 | 完整可读 | `archiving`、`trash` |
| `archiving` | 热目录 + 归档暂存 | 仍从热目录读取 | `hot`、`archived_duplicate` |
| `archived_duplicate` | 热目录 + 已验证 `.sqrun` | 优先热目录 | `archived` |
| `archived` | 已验证 `.sqrun` | 透明读取 | `hot`、`trash` |
| `trash` | 回收站中的目录或 `.sqrun` | 仅回收站页 | 原状态、`purging` |
| `purging` | 回收站清除暂存名 | 只显示清除中/待重试 | `trash`、`purged` |
| `purged` | 墓碑和生命周期事件 | 只显示审计摘要 | 无 |
| `invalid` | 无法验证的载体 | 隔离显示 | 人工隔离处理，不自动删除 |

`archiving`、`archived_duplicate` 和 `purging` 是可恢复的持久状态，不得只存在于进程内存。

### 4.2 保留状态

存储状态与保留状态正交。保留状态为：

- `normal`：遵守自动策略；
- `latest_hold`：属于每个设备/工作流最近 N 次保护窗口；
- `manual_keep`：用户长期保存；
- `referenced`：配置、决策或应用审计引用；
- `reference_unknown`：引用解析失败，按受保护处理；
- `legal_hold`：预留的管理员证据冻结状态，v1 只解析、不开放 Web 设置。

只有 `normal` 且满足时间策略的终态实验可以自动进入回收站。所有保留状态都允许归档压缩。

## 5. 存储布局

默认布局为：

```text
output/
  experiments/
    qubit_spectroscopy_<uuid>/        # hot，不可变
    .spectroscopy_<run-prefix>/       # running
  experiment-storage/
    policy.json
    catalog.sqlite
    catalog.lock
    lifecycle/
      <run_id>/
        00000000_<event_id>.json
        00000001_<event_id>.json
    pins/
      <run_id>.json
    references-cache/
      <catalog_revision>.json
    archive-staging/
      <operation_id>/
    archives/
      <run_id>.sqrun
    trash/
      <run_id>/
        trash-record.json
        payload/                       # 原热目录或归档文件
    purge-staging/
      <run_id>_<operation_id>/
    tombstones/
      <run_id>.json
    cleanup-plans/
      <plan_id>.json
    cleanup-receipts/
      <plan_id>.json
    reservations/
      <reservation_id>.json
    locks/
      <run_id>.storage.lock
      cleanup.lock
```

`archive_root` 可以在服务启动参数中配置为另一块本地固定磁盘。v1 禁止 UNC、网络映射盘、相对路径、
symlink、junction 和 reparse point。归档根不通过 Web 修改。跨卷归档使用“复制 -> 关闭 -> 重读验证 ->
生命周期提交 -> 清理热目录”，不能把跨卷移动当作原子操作。

## 6. `.sqrun` 归档格式

### 6.1 容器约束

`.sqrun` 是 ZIP64 容器，使用 Python 标准库 `zipfile`，不允许 pickle、可执行脚本或自解压格式。
约束如下：

- 条目按 UTF-8 POSIX 相对路径字节序排序；
- 条目名称必须使用 `/`，不得包含 `\\`、空段、`.`、`..`、NUL、冒号或绝对路径；
- 不允许重复名称、仅大小写不同的冲突名称、加密条目、symlink 或特殊设备类型；
- ZIP 元数据时间固定为 `1980-01-01T00:00:00`，权限字段固定；
- v1 压缩方法固定为 `ZIP_STORED`，不使用 Deflate、data descriptor 或 ZIP comment；
- 解压后的文件字节才是证据内容；ZIP 原始 SHA-256 是载体身份，不替代文件级哈希；
- Web 读取时直接按条目流式读取，不自动解压完整目录；
- 恢复时只能解压到新的同根暂存目录，逐条验证后原子发布到不存在的目标。

按当前 41 点样本估算，`ZIP_STORED` 会把约 3652 个文件合并成一个实际分配约 6-7 MiB 的载体，
相对当前约 17 MiB 的 NTFS 实际分配减少约 60%-65%。该估算只作为容量计划，不作为验收承诺；
正式验收使用打包前后的真实 allocated bytes。

### 6.2 容器条目

```text
format.json
bundle-manifest.json
bundle-verification-report.json
bundle-receipt.json
run/<原运行目录内的全部相对文件>
```

`format.json` 精确包含 `schema_version`、`artifact_type`、`artifact_version`、`format_version`、
`run_id`、`payload_prefix`、`compression`、`zip64` 和 `entry_order`。v1 的 `compression` 固定为
`stored`，`payload_prefix` 固定为 `run/`，`entry_order` 固定为 `utf8_posix_path_bytes`。

`bundle-manifest.json` 使用 canonical JSON，精确字段为：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `schema_version` | string | 固定 `0.1` |
| `artifact_type` | string | 固定 `sqvm_experiment_bundle_manifest` |
| `artifact_version` | string | 固定 `0.1` |
| `run_id` | UUID string | 与原工作流一致 |
| `workflow_id` | string | 与原工作流一致 |
| `source_artifact_version` | string | 原工作流 artifact version |
| `original_directory_name` | string | 单段安全文件名 |
| `workflow_sha256` | uppercase SHA-256 | 原 `workflow.json` 原始字节 |
| `receipt_sha256` | uppercase SHA-256 | 原顶层 `receipt.json` 原始字节 |
| `entry_count` | integer | 大于 0 |
| `logical_bytes` | integer | 所有原文件长度和 |
| `compression` | object | exact method=`stored`、level=null 和 writer version |
| `entries` | array | 按 path 排序的 exact 条目清单 |

每个 `entries` 元素精确包含：

```text
path
byte_length
raw_sha256
```

`bundle-verification-report.json` 精确字段为：

```text
schema_version
artifact_type
artifact_version
run_id
ok
bundle_manifest_sha256
source_verifier_id
source_verifier_version
checks
blocking_reasons
```

`bundle-receipt.json` 精确字段为：

```text
schema_version
artifact_type
artifact_version
run_id
bundle_manifest_sha256
bundle_verification_report_sha256
workflow_sha256
receipt_sha256
source_verifier_id
source_verifier_version
entry_count
logical_bytes
```

哈希拓扑固定为：原运行文件树绑定 bundle manifest；verification report 绑定 bundle manifest；bundle
receipt 绑定 manifest、verification report 和原运行 receipt。归档时间、操作者和归档包原始 SHA-256
写入外部生命周期事件，避免把本次存储操作混入原实验身份。ZIP raw SHA 不得写入 ZIP 自身。

### 6.3 归档验证

归档验证必须：

1. 检查 ZIP central directory 和 exact 顶层条目；
2. 检查路径、重复名、大小写冲突、Unix mode、加密标志和 ZIP64 元数据；
3. 先解析受限大小的 format/manifest/report/receipt，再按 manifest 流式读取每个条目；
4. 对每个条目限制实际读取字节数为 `byte_length + 1`，防止伪造长度；
5. 重算文件 SHA-256、总大小、条目数、工作流和回执哈希；
6. 通过 `EvidenceReader` 版本分派调用对应工作流原始 verifier，验证完整文件集和语义；
7. 禁止把归档解压到正式路径后再验证；
8. 验证通过后关闭所有句柄，重新打开归档并完成第二次只读验证；
9. 记录归档原始 SHA-256、大小、验证器版本和 lifecycle 前序哈希。

## 7. 权威对象与 Schema

### 7.1 存储策略 `policy.json`

精确根字段如下，不允许额外字段：

```text
schema_version
artifact_type
artifact_version
policy_id
enabled
compact_after_hours
simulation_retention_days
hardware_retention_days
failed_retention_days
trash_retention_days
simulation_keep_latest
hardware_keep_latest
minimum_free_disk_GiB
maximum_store_GiB
high_watermark_ratio
critical_watermark_ratio
estimate_safety_factor
stale_staging_hours
cleanup_plan_ttl_minutes
archive_compression
updated_utc
actor_id
content_sha256
```

默认值为：

| 字段 | 默认值 |
| --- | ---: |
| `compact_after_hours` | 1 |
| `simulation_retention_days` | 30 |
| `hardware_retention_days` | 90 |
| `failed_retention_days` | 7 |
| `trash_retention_days` | 7 |
| `simulation_keep_latest` | 20 |
| `hardware_keep_latest` | 50 |
| `minimum_free_disk_GiB` | 10 |
| `maximum_store_GiB` | 20 |
| `high_watermark_ratio` | 0.80 |
| `critical_watermark_ratio` | 0.90 |
| `estimate_safety_factor` | 1.50 |
| `stale_staging_hours` | 24 |
| `cleanup_plan_ttl_minutes` | 10 |
| `archive_compression` | `stored` |

比例满足 `0 < high < critical < 1`。时间和数量为有界非负整数；保留天数不能为 0；GiB 值必须为
有限正数。`content_sha256` 是移除自身后 canonical JSON 的 SHA-256。

Web 可以修改数值策略和 `enabled`，但不能修改存储根、压缩算法、Schema 版本或引用保护规则。Deflate
或 Zstandard 只能在新的 `.sqrun` format version 中引入，并重新通过跨平台确定性和安全门禁。

### 7.2 生命周期事件

每个事件是一个只创建不覆盖的 canonical JSON 文件，精确字段为：

```text
schema_version
artifact_type
artifact_version
event_id
run_id
sequence
event_type
utc_time
actor_id
operation_id
payload
previous_event_sha256
event_sha256
```

事件类型固定为：

```text
hot_discovered
keep_changed
archive_started
archive_verified
hot_cleanup_started
hot_cleanup_completed
archive_restore_started
archive_restore_completed
trash_started
trashed
trash_restored
purge_started
purge_failed
purged
staging_recovery_required
reference_scan_failed
```

序号从 0 连续递增，`event_sha256` 使用移除自身后的 canonical JSON 计算。操作失败不能改写旧事件，
必须写新事件。事件 payload 按事件类型使用 exact Schema；所有变更事件至少绑定前一载体状态、目标状态、
工作流哈希、载体路径别名、载体 SHA-256 和 catalog revision。

### 7.3 删除墓碑

永久清除后保留小型墓碑，精确字段为：

```text
schema_version
artifact_type
artifact_version
run_id
workflow_id
workflow_sha256
receipt_sha256
last_bundle_sha256
original_created_utc
purged_utc
actor_id
reason
last_lifecycle_event_sha256
tombstone_sha256
```

墓碑不包含实验数据、候选值或物理结果，只证明该身份曾存在并被显式清除。墓碑本身永久保留。

### 7.4 SQLite 目录

`catalog.sqlite` 是派生缓存，使用 WAL 和编号迁移。至少包含：

- `runs`：run identity、workflow、device、created UTC、状态、载体别名和各类大小；
- `retention`：manual keep、latest hold、reference status 和删除期限；
- `references`：引用类型、引用对象 ID、引用证据路径和哈希；
- `lifecycle_heads`：每个 run 的 sequence 和事件尾哈希；
- `tombstones`：永久清除后的最小索引；
- `reservations`：运行前空间预留；
- `schema_migrations`：目录版本。

目录不能存储原始数值数组。目录重建必须只读扫描热目录、归档包、生命周期事件、pins、配置引用和墓碑。
单个载体损坏时记录 blocker，不能把该 run 当作可删除对象。重建使用新临时数据库，完成验证和 checkpoint
后原子替换旧目录。

## 8. 引用图与保护规则

`ExperimentReferenceResolver` 扫描以下来源：

1. 当前配置中的 `source_candidate.experiment_run_id`；
2. Draft 和 Snapshot 的 `source_candidate.json`；
3. Active Snapshot 和当前运行绑定；
4. calibration decision 的 `run_id` 或 `recommendation_id` 反向绑定；
5. 配置审计中的候选应用事件；
6. 手工 `pins/<run_id>.json`；
7. 后续实验声明的 parent/derivation run 引用。

引用类型固定为：

```text
current_configuration
draft_configuration
snapshot_configuration
active_snapshot
accepted_decision
applied_audit
derived_experiment
manual_keep
```

候选实际写入当前配置后，该实验产生 `applied_audit` 永久保护。即使后续当前配置改用另一实验，原应用记录
仍然是校准历史的一部分。保护实验可以归档到另一块本地磁盘，但 v1 不允许永久清除。

引用解析必须满足：

- run ID 和 recommendation ID 唯一；
- 所有来源文件通过自身 Schema 和哈希验证；
- 不跟随链接或跳出配置根；
- 相同引用去重但保留所有来源；
- 任一来源不可读、重复或非法时，结果为 `reference_unknown`；
- `reference_unknown` 时归档允许，trash/purge 禁止。

## 9. 原子操作与崩溃恢复

### 9.1 归档

归档流程为：

1. 获取 `<run_id>.storage.lock`，重读并验证热目录；
2. 写 `archive_started` 事件；
3. 在 `archive-staging/<operation_id>` 构建临时 `.sqrun`；
4. 关闭、flush、重开并完成两次归档验证；
5. 把归档发布到不存在的 `archives/<run_id>.sqrun`；
6. 再次重读正式归档，写 `archive_verified` 事件；
7. 目录事务将状态设为 `archived_duplicate`，读取仍优先热目录；
8. 写 `hot_cleanup_started`，整体删除热目录；
9. 写 `hot_cleanup_completed`，目录状态设为 `archived`；
10. 释放锁。

第 1-6 步失败保留热目录并删除未验证暂存；第 7-9 步失败保留两份完整证据并标记
`archived_duplicate`，后续幂等重试只清理已经验证且哈希相同的热目录。

### 9.2 移入回收站

普通删除流程为：

1. 获取 run 锁并刷新引用图；
2. 校验 `expected_workflow_sha256`、保留状态和当前存储状态；
3. 创建 `trash-record.json.tmp` 并写 `trash_started`；
4. 同卷时把载体原子移动到 `trash/<run_id>/payload`；
5. 跨卷时复制、验证、提交回收站载体后再清理来源；
6. 原子发布 `trash-record.json`，写 `trashed`；
7. 更新目录后释放锁。

回收站记录包含删除人、原因、原状态、原载体哈希、工作流哈希、删除时间和计划清除时间。恢复目标必须
不存在；恢复后重新执行完整 verifier 和引用扫描。

### 9.3 永久清除

永久清除只允许 `trash`：

1. 获取 run 锁并校验确认短语、trash record 和载体哈希；
2. 原子重命名为 `purge-staging/<run_id>_<operation_id>`；
3. 写 `purge_started`；
4. 递归删除 purge staging，不跟随任何链接；
5. 删除失败时写 `purge_failed` 并保留可重试状态；
6. 只有确认载体完全不存在后才写墓碑和 `purged`；
7. 更新目录并释放锁。

确认短语固定为 `PURGE EXPERIMENT <run_id>`。普通 trash 和自动清理不使用永久清除短语。

### 9.4 运行中暂存

`.spectroscopy_*` 不能仅因目录年龄自动删除。系统读取进程/锁/心跳后只报告：

- `active`：进程和心跳有效；
- `possibly_interrupted`：心跳超时但不能证明终止；
- `recovery_required`：现有 Stage 6 恢复协议确认需要隔离；
- `orphan_unbound`：没有可验证身份的非运行临时目录。

所有清理都需要显式恢复或隔离操作。KeyboardInterrupt、进程崩溃、publication failure、Windows sharing
violation 和重启后的行为必须分别测试。

## 10. 配额、容量估算与自动清理

### 10.1 空间口径

系统同时报告：

- `logical_bytes`：文件长度总和；
- `allocated_bytes`：实际分配空间；
- `archive_bytes`：归档载体长度；
- `reclaimable_now_bytes`：立即归档或清除可释放空间；
- `reclaimable_after_trash_bytes`：回收站到期后可释放空间；
- `volume_free_bytes` 和 `volume_total_bytes`。

Windows 优先使用 `GetCompressedFileSizeW` 获取实际分配；无法获得时按卷簇大小向上取整并标记
`allocated_estimated=true`。POSIX 使用 `st_blocks * 512`。不能把逻辑大小冒充实际占盘。

### 10.2 运行前估算

按 `workflow_id + device_id + execution_profile` 保存最近有效运行的每点实际分配统计，使用最近 20 次的
p95；不足 3 次时使用保守默认 `1 MiB/point + 64 MiB/run`。二维扫描的点数是所有扫描轴的笛卡尔积，
并行 lockstep 轴按同一 point 计数。

```text
required_bytes = max(history_p95_bytes_per_point * point_count,
                     fallback_estimate) * estimate_safety_factor
```

准入必须同时满足：

```text
volume_free_bytes - active_reservations - required_bytes >= minimum_free_disk_bytes
store_allocated_bytes + required_bytes <= maximum_store_bytes
```

通过后创建有 TTL 的 reservation；实验正式进入 running 后绑定 run ID，终态发布或失败后释放。并发请求
必须在同一 cleanup/catalog lock 下计算，防止超卖。

### 10.3 自动清理顺序

自动任务和运行前清理都先生成计划，动作顺序固定为：

1. 把已到期回收站列入 purge preview；v1 不自动执行永久清除；
2. 清理 `archived_duplicate` 的重复热目录；
3. 归档超过 `compact_after_hours` 的 hot 实验；
4. 把过期的 failed/cancelled 且未保护实验移入回收站；
5. 把过期 simulation/hardware 且不在 latest hold 的实验移入回收站；
6. 若仍不能满足最低空间，拒绝新实验并报告保护项占用。

自动任务永不直接从 hot/archived 进入 purged。

### 10.4 清理计划

清理计划精确字段为：

```text
schema_version
artifact_type
artifact_version
plan_id
trigger
created_utc
expires_utc
actor_id
catalog_revision
policy_sha256
usage_before
actions
estimated_usage_after
estimated_reclaimed_bytes
plan_sha256
```

每个 action 精确包含：

```text
action_id
run_id
action_type
from_state
to_state
expected_workflow_sha256
expected_carrier_sha256
reason_code
estimated_reclaimed_bytes
```

执行时逐项重验前置条件。前置条件变化的动作标为 `skipped_precondition_changed`，不能改用新状态继续。
物理文件操作无法跨多个 run 事务化，因此 cleanup receipt 必须记录每项 `completed/skipped/failed`，不得用
一个成功布尔值掩盖部分结果。相同 plan/action 重试必须幂等。

## 11. Python 模块边界

建议新增 `src/sqvm/storage/`，不把存储治理逻辑堆入 Web handler：

```text
storage/
  models.py          # 枚举和只读 DTO
  policy.py          # policy loader/validator/hash
  inventory.py       # logical/allocated/volume usage
  references.py      # 引用图，fail closed
  readers.py         # DirectoryEvidenceReader/ZipEvidenceReader
  bundle.py          # .sqrun writer/reader/verifier
  lifecycle.py       # 事件链和状态归约
  catalog.py         # SQLite 派生目录与重建
  retention.py       # 资格判定和 latest hold
  planning.py        # cleanup plan/reservation
  operations.py      # archive/trash/restore/purge orchestration
  migration.py       # legacy hot directory migration
  errors.py          # typed public errors
```

依赖方向固定为：

```text
models <- policy/inventory/lifecycle/bundle/references/catalog
       <- retention/planning
       <- operations/migration
       <- calibration API / Web API / scripts
```

实验 verifier 必须依赖只读 `EvidenceReader` 协议，而不是强制依赖真实目录。目录和 ZIP reader 返回同一
文件枚举/流接口；未知工作流没有 verifier 时不得进入可信 catalog。任何实验模块不得依赖 Web。Web 只能调用 application service，
不能直接 `shutil.rmtree`、`zipfile.ZipFile` 或更新 catalog。

## 12. API 详设

### 12.1 查询接口

| 方法 | 路径 | 响应 |
| --- | --- | --- |
| GET | `/api/v1/experiment-storage` | 总用量、卷空间、分类、策略和告警 |
| GET | `/api/v1/experiments/{run_id}/storage` | 单实验载体、大小、引用、保留和允许动作 |
| GET | `/api/v1/experiment-trash` | 回收站列表 |
| GET | `/api/v1/experiment-trash/{run_id}` | 回收站详情和恢复前置条件 |
| GET | `/api/v1/experiment-storage/cleanup-plans/{plan_id}` | 计划或执行回执 |

所有大小使用整数 bytes；Web 自行格式化，不在 API 中混用 MiB 字符串。

单实验 storage DTO 精确包含：

```text
schema_version
run_id
workflow_id
workflow_sha256
storage_state
retention_state
created_utc
carrier
logical_bytes
allocated_bytes
allocated_estimated
reference_count
references
delete_after_utc
allowed_actions
blockers
catalog_revision
```

### 12.2 变更接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/experiments/{run_id}/keep` | 设置/取消长期保存 |
| POST | `/api/v1/experiments/{run_id}/archive` | 立即归档 |
| POST | `/api/v1/experiments/{run_id}/restore-hot` | 从归档恢复完整目录 |
| POST | `/api/v1/experiments/{run_id}/trash` | 移入回收站 |
| POST | `/api/v1/experiment-trash/{run_id}/restore` | 恢复原状态 |
| POST | `/api/v1/experiment-trash/{run_id}/purge` | 永久清除 |
| PUT | `/api/v1/experiment-storage/policy` | 更新数值策略 |
| POST | `/api/v1/experiment-storage/cleanup-preview` | 生成清理计划 |
| POST | `/api/v1/experiment-storage/cleanup-apply` | 执行计划 |

所有变更请求至少包含：

```text
actor_id
expected_catalog_revision
expected_workflow_sha256
reason
```

keep 增加 `keep: bool`；purge 增加 `confirmation_phrase`；cleanup apply 增加 `plan_id` 和
`expected_plan_sha256`。不允许客户端提供目标文件路径、归档路径、引用状态或预计释放空间。

### 12.3 错误契约

错误响应精确包含：

```text
schema_version
error_code
message
status
run_id
blockers
retryable
catalog_revision
```

公共错误码至少包括：

| HTTP | `error_code` |
| ---: | --- |
| 404 | `experiment_not_found` |
| 409 | `invalid_storage_state`、`experiment_referenced`、`experiment_kept`、`identity_conflict` |
| 412 | `workflow_hash_conflict`、`catalog_revision_conflict`、`plan_precondition_changed` |
| 422 | `invalid_storage_request`、`invalid_confirmation_phrase` |
| 423 | `storage_operation_locked`、`experiment_running` |
| 507 | `insufficient_storage` |
| 500 | `archive_verification_failed`、`storage_operation_failed` |

500 错误不能泄漏绝对路径、traceback 或环境变量。服务端日志可以记录 operation ID 和内部异常链。

## 13. Web 详设

### 13.1 实验列表

实验列表新增：

- 多选框；
- 存储状态；
- 实际占盘；
- 保留状态；
- 引用数量；
- 预计删除时间；
- 行级“长期保存”“归档”“移入回收站”操作。

默认不显示 trash/purged。筛选项包括工作流、对象、验证状态、存储状态、保留状态和时间。列表只读取目录，
不能递归计算每个实验大小；大小必须来自 storage catalog。

### 13.2 存储管理页

页面使用全宽信息带和表格，不使用嵌套卡片。显示：

1. 卷总空间、可用空间、存储上限和最低保留空间；
2. hot、archived、trash、staging、protected 各类实际占盘；
3. 当前告警和阻止新实验原因；
4. 可编辑的数值保留策略；
5. “生成清理预览”按钮和清理动作表；
6. 回收站 tab；
7. 孤立 staging/recovery-required tab。

清理预览逐行列出 run、动作、原因和预计释放空间。执行按钮只提交 plan ID，计划过期或 catalog revision
变化时要求重新预览。

### 13.3 确认交互

- 归档：普通命令确认；
- 移入回收站：复选框确认，显示引用检查和回收站到期日；
- 永久清除：必须勾选不可恢复确认，并输入/由 UI 绑定精确确认短语；
- 批量清理：显示总数量、总预计释放空间和每个 blocker；
- 引用或 keep 阻止时按钮禁用，tooltip 和详情列显示具体来源；
- mutation 进行中按钮显示稳定 loading 状态并防止重复提交；
- 成功后刷新 catalog revision，失败后保留弹窗和用户选择。

桌面 1440x900 和移动端 390x844 都不得页面级横向溢出。移动端批量表格可以局部横向滚动，操作按钮
使用图标和 tooltip，永久清除不能只依靠颜色区分。

## 14. 旧数据迁移

迁移工具只处理已验证完成实验，不处理 `.spectroscopy_*`。流程为：

1. 只读建立 inventory，报告 duplicate run ID、无效 artifact、链接和未知工作流；
2. 运行对应 verifier；
3. 计算逻辑/实际占盘并生成迁移计划；
4. 对每个 run 创建和二次验证 `.sqrun`；
5. 保留原目录，完成全批次抽样和 catalog rebuild；
6. 仅对归档哈希匹配的 run 执行 hot cleanup；
7. 输出 migration receipt，逐 run 记录归档、跳过和失败；
8. 任何失败不能影响其他 run，也不能降低失败 run 的可见性。

首轮迁移默认 `archive_only=true`，即先产生重复归档但不删除热目录。独立验收通过后再运行
`cleanup_verified_duplicates`。当前正式 output 只能在用户显式确认后迁移。

## 15. 充分测试与发布门禁

### 15.0 当前基线与 NO-GO 证据

独立测试负责人和主任务分别执行了同一只读基线：

```powershell
python -m pytest -q tests/test_stage6_runtime_core.py tests/test_stage6_runtime_evidence.py tests/test_stage6_runtime_platform.py tests/test_stage6_runtime_v02.py tests/test_runtime_publication.py
```

两次结果一致：`41 passed, 13 failed`。第一个失败是当前 source snapshot aggregate
`674526E9607CF1E6518F66A926EFF83F394CF463C53C2BCF00C5E01FD0971648` 与冻结期望不一致；其余失败
共同抛出 `compiler fixture source binding drifted: src/sqvm/qcis/compiler.py`。13 项均位于
`tests/test_stage6_runtime_v02.py`，覆盖 loader、authority、dataset tampering、run/replay/catalog、failed run、
claim tampering、interrupted publication、recovery 和 link rejection。

该结果表示当前 runtime v0.2 基线真实 NO-GO，不能用新存储测试掩盖或修改断言促成通过。另外，历史全项目
测试仍依赖当前工作区缺失且被 `.gitignore` 排除的 Stage 1-4 `output` fixtures；修复这 13 项只代表 v0.2
选择集恢复，不等于全项目 green。正式发布前必须补齐/重建合法历史 fixtures，并让全套测试真实结束为 0。

### 15.1 测试职责分离

- 实现负责人编写模块单元、集成和回归测试；
- 独立测试负责人不参与实现，依据冻结设计另建验收、篡改和故障注入测试；
- Web 负责人提供浏览器测试，但不能代替独立浏览器验收；
- 项目经理只在实现测试和独立测试都通过后允许提交/推送；
- 用户正式 output 不作为可修改测试 fixture，只可只读统计或复制到临时根。

### 15.2 测试层级

#### A. Schema 和模型测试

- policy、bundle manifest/receipt、lifecycle、pin、trash record、tombstone、cleanup plan/receipt；
- 每个 Schema 至少包含 valid golden、missing、unknown、null、type、range、enum、hash 和 duplicate-key；
- canonical JSON、有限数、UTC、UUID、大小写 SHA 和 additionalProperties fail closed；
- 状态转换表每条允许边和拒绝边都有测试。

#### B. 归档往返与属性测试

- 空文件、边界大小、数千小文件、二进制、Unicode 内容和合法 ASCII 路径；
- 原目录 -> `.sqrun` -> 临时恢复后逐文件字节、路径、数量和 SHA 全相等；
- 相同输入和固定工具链产生相同 manifest/receipt；
- 归档 reader 与 hot reader 返回相同 experiment DTO、plot spec、候选和 CSV/JSON 导出；
- 随机生成安全文件树，至少 500 个 property cases；
- mutation test 覆盖文件字节、长度、哈希、条目顺序、重复条目和 central directory；
- 相同输入在 Windows/Linux 和独立进程中产生逐字节相同的 v1 `.sqrun`。

#### C. 安全和对抗测试

- `../`、绝对路径、反斜杠、盘符、ADS 冒号、NUL、case collision；
- symlink、junction、reparse point、hardlink 和目录循环；
- 非 `ZIP_STORED` 方法、ZIP bomb、伪造 uncompressed size、截断 archive、重复 central directory；
- 非法 JSON、BOM、NaN/Infinity、超深对象和超大字段；
- 客户端伪造路径、引用状态、释放空间和 catalog revision；
- 删除引用扫描失败、损坏 audit、重复 run ID 时全部 fail closed。

#### D. 故障注入测试

对归档、trash、restore、purge、catalog rebuild 的每个持久化边界注入异常：

1. 创建暂存前后；
2. 写任意 archive 条目中途；
3. 写 manifest/receipt 前后；
4. flush、close、rename、跨卷 copy 前后；
5. archive verified 事件前后；
6. 删除 hot 目录中途；
7. catalog transaction 提交前后；
8. trash rename 和 trash record 发布前后；
9. purge rename、递归删除和墓碑写入前后；
10. 进程终止后重新启动并重建 catalog。

每个注入点必须证明至少一份完整证据仍存在、状态可重建、操作可幂等重试且不会错误报告成功。

#### E. 并发和 TOCTOU 测试

- 两个 archive 请求；
- archive 与 trash；
- restore 与 purge；
- Web 读取与 hot cleanup；
- cleanup apply 与手工 keep；
- 引用扫描后配置被更新；
- 两个实验同时做容量 reservation；
- catalog rebuild 与 mutation；
- Windows sharing violation 和打开文件句柄。

#### F. 配额和策略测试

- logical/allocated/cluster rounding；
- p95/fallback/二维点数/lockstep 点数计算；
- high/critical watermark 边界；
- latest N 与天数取并集；
- hardware/simulation/failed 策略分流；
- 保护项超过配额时拒绝新实验；
- cleanup plan 过期、策略哈希变化、catalog revision 变化；
- 部分动作失败的 cleanup receipt。

#### G. API 与浏览器测试

- 所有端点正常、错误和方法不允许路径；
- Content-Type、body limit、actor、hash、revision 和确认短语；
- 单项和批量操作的 loading、重复点击、失败重试；
- 引用/keep 按钮禁用和 blocker 展示；
- cleanup preview/apply 数据一致；
- 回收站恢复和永久清除；
- 归档实验的曲线、对象/数据选择、hover 和导出；
- 1440x900、390x844 截图，无重叠、溢出或不可达操作；
- Chromium、Firefox、WebKit 至少各覆盖桌面主流程，Chromium 和 WebKit 各覆盖移动端主流程；
- 键盘导航、焦点、dialog、checkbox 和错误提示可访问性。

#### H. 迁移和兼容测试

- 当前 `qubit_spectroscopy_scan_v1` v0.1/v0.2；
- 旧 `qubit_spectroscopy_calibration_v1`；
- generic plot spec 实验；
- invalid/duplicate/unknown workflow；
- archive-only 和 cleanup-duplicate 两阶段迁移；
- 迁移前后 Web 列表、详情、候选更新和配置引用一致；
- catalog 丢失后从混合 hot/archive/trash/tombstone 完整重建。

#### I. 性能和规模测试

- 1、21、41、1000 和二维 41x41 synthetic points；
- 10 万小文件 inventory 与归档；
- 1000 个归档实验的列表分页和检索；
- catalog rebuild、cleanup preview、归档读取首屏和单点详情耗时；
- Windows 实际分配空间前后对比；
- 归档峰值临时空间和内存不超过策略预算。

### 15.3 数量和覆盖率门禁

最低门禁为：

- 新 storage 模块 statement coverage >= 95%，branch coverage >= 90%；
- 破坏性资格判定、引用保护和状态转换分支 100%；
- 新增存储治理确定性测试不少于 180 项；
- 每个公共错误码至少 1 个 API 测试；
- 每个故障注入边界至少 1 个异常测试和 1 个恢复测试；
- archive property/fuzz seeds >= 10,000，每个发现必须固化为确定性 regression；
- 安全 mutation cases >= 100；
- 浏览器桌面和移动端主流程各至少 1 次完整录像/截图证据；
- 至少 3 次连续真实 Windows NTFS 运行，覆盖分配空间、文件占用、sharing violation、junction、长路径
  和进程终止，不能全部使用 mock；
- 至少 1 次 3 点真实 QuTiP 端到端运行、归档、读取、回收和恢复；
- 所有现有校准 API、run_circuits、QCIS、Stage 4.1/5.1、Web 和 Notebook 回归通过；
- 全项目 `pytest` 必须最终完成且退出码为 0，超时、无输出或只跑定向测试不能作为发布通过。

建议分层运行预算：快速单元 5 分钟、存储集成 15 分钟、故障注入 20 分钟、浏览器 10 分钟、全项目
30 分钟。若当前套件超过预算，应先并行化或修复慢测试，不得通过缩小覆盖范围解决。

### 15.4 独立发布证据

发布报告必须包含：

1. git diff 范围和 `git diff --check`；
2. 各层测试命令、实际计数、耗时和退出码；
3. coverage 和 mutation 报告；
4. 真实 NTFS 归档前后逻辑/实际空间；
5. 失败注入状态恢复清单；
6. 浏览器两种 viewport 截图；
7. 正式 output 未被测试修改的前后 inventory/hash 证明；
8. 独立测试负责人 GO/NO-GO；
9. 未关闭 P0/P1/P2 和剩余风险；
10. 回滚步骤和 catalog rebuild 证据。

任一 P0/P1、引用保护失败、证据丢失、无法恢复的部分删除、全项目测试未完成或正式数据被测试修改，均为
NO-GO。

## 16. AI 团队开发工作包

冻结设计后采用独立 Codex 任务，不使用 subagent。工作包边界为：

| 工作包 | 负责人 | 可编辑范围 | 必须交付 |
| --- | --- | --- | --- |
| WP0 | 项目经理/架构 | 设计、Schema、fixtures | 冻结哈希和接口契约 |
| WP1 | 归档负责人 | `src/sqvm/storage/bundle.py` 及单元测试 | bundle writer/reader/verifier |
| WP2 | 生命周期负责人 | lifecycle/reference/retention/planning | 状态、引用、计划和故障测试 |
| WP3 | 后端负责人 | operations/catalog/migration/API | 原子操作、目录、API 集成 |
| WP4 | Web 负责人 | server adapter、app.js、styles、浏览器测试 | 存储页和完整交互 |
| WP5 | 独立测试负责人 | 独立验收文件，不改实现 | 对抗、故障、浏览器、回归报告 |

WP1/WP2 可以并行；WP3 必须等待 WP1/WP2 契约冻结；WP4 等待 API DTO 冻结；WP5 从 WP0 开始准备
独立 oracle，但不得为促成通过降低门禁。每个工作包在独立分支提交，项目经理逐个审查并集成；禁止多个任务
同时编辑同一文件。

## 17. 分阶段落地与回滚

### Tranche A：可见性和安全删除

- inventory、引用图、policy、catalog；
- storage summary、keep、trash、restore；
- 无自动 purge；
- Web 存储页和回收站。

### Tranche B：单文件归档

- `.sqrun` writer/reader/verifier；
- archive/restore 和 archived Web 透明读取；
- archived_duplicate 恢复；
- 旧数据 archive-only 迁移。

### Tranche C：自动策略和容量准入

- reservation、p95 估算、清理计划；
- cleanup preview/apply；
- 定时归档和到期 trash；v1 只自动生成 purge 预览，不自动永久清除；
- 实验运行前 507 准入。

### Tranche D：正式迁移与发布

- 复制正式 output 到隔离根演练；
- 独立验收完整通过；
- 正式 archive-only；
- 再次验证后清理 verified duplicates；
- 观察至少一个保留周期后再评审是否在新版本开放自动 purge；v1 默认始终需要显式 apply。

任一 tranche 可通过关闭 `policy.enabled` 停止自动操作。回滚只允许停止调度、从已验证 archive 恢复 hot、
重建 catalog；不得回滚已产生的生命周期事件或墓碑。

## 18. 验收条件

1. 41 点实验归档后完整证据逐字节一致，实际占盘显著下降；
2. hot 和 archived 实验在 Web 中返回相同图、数据、候选和验证状态；
3. 引用、keep、unknown reference 实验无法进入 trash/purge；
4. 普通删除可在回收期内恢复，永久清除只作用于 trash；
5. 任意故障注入后至少一份完整证据存在且状态可重建；
6. 空间不足在实验启动前被拒绝，不能写满磁盘后才失败；
7. 自动清理严格执行计划且部分结果完整披露；
8. catalog 删除后可从权威对象完整重建；
9. 正式 output 在测试期间零修改；
10. 第 15 节所有测试和独立发布门禁全部通过。

## 19. AI 团队评审决策记录

本详设由四个独立 Codex 任务使用 `gpt-5.6-terra/high` 只读评审，未使用 subagent，职责分别为归档
架构、生命周期与删除安全、Web/API、独立测试与故障注入。项目经理合并后的关键裁决如下：

1. 接受归档组的 P0：Stage 7 `execution` 证据闭包和 staging 恢复语义先于归档实现；
2. 接受确定性建议：v1 使用 `ZIP_STORED`，增加 format、manifest、verification report、receipt 四层；
3. 接受生命周期组的 fail-closed 引用图、持久锁、append-only 审计和 trash-first；
4. 接受 Web 组的服务端分页、可释放/待释放分离、乐观并发、cleanup preview/apply 和无障碍门禁；
5. 接受测试组更严格的 95% line、90% branch、180 项确定性测试、10,000 fuzz seeds 和真实 NTFS；
6. 拒绝把实验存储 v1 扩大为 Draft/Snapshot 通用资源管理，避免本阶段越界；现有配置删除风险另立工作包；
7. 拒绝让完整热目录保留 90 天，因为小文件放大正是当前容量问题；热数据 1 小时后归档，归档证据再按
   simulation 30 天、hardware 90 天和 latest hold 策略保留；
8. 拒绝批量文件操作宣称全有或全无；每项严格前置校验、幂等执行并在 cleanup receipt 披露部分结果；
9. v1 不自动永久清除，只自动产生 purge preview；正式观察一个保留周期后再评审新版本；
10. 当前 authority/source 漂移和历史 fixture 缺失使实现基线保持 NO-GO，WP0 关闭前不得进入发布。
