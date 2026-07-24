# 2026-07-23 配置事务进入检查与故障注入矩阵

## 结论

**NO-GO：不得开始 Step 4 配置事务实现。** 本文只做进入前审查；未修改 `src/` 或 `tests/`。

设计规定 Step 4 的前置是 Step 1 fixture 闭包、Step 2 clean Windows test lock/marker，以及 Step 3 的
`fixture-integrity` 和 `contract-windows` 已成为 required 且稳定通过
([详设](../designs/07_1_10_development_baseline_stabilization_design.md) 第 1018--1024 行)。当前详设明示
Step 3 未实施，且工作区没有 `.github/workflows/`；因此 required checks 不存在，不能以本机测试替代。
此外，transaction schema、operation ID、legacy migration 的非实现者测试向量尚未作为可审查产物交付。

解除 NO-GO 前必须同时满足：

1. 项目负责人确认 Step 1/2 闭包在 clean Windows 环境可安装、收集，且不读取仓库根 `output/`。
2. Step 3 的 `fixture-integrity`、`contract-windows` 已配置为 `dev` required checks，连续稳定通过的记录可查。
3. 非实现者审查并冻结 transaction head、manifest、receipt、idempotency 和 legacy import 的 JSON 测试向量。
4. 为 WP4 指定独占写入窗口和文件所有权；现有工作树已含其他并行修改，实施前不得覆盖它们。

## 当前入口与风险

| 入口 | 现有事实 | Step 4 风险与实施要求 |
| --- | --- | --- |
| `PlatformConfigurationStore.__init__` ([configuration.py](../../src/sqvm/web/configuration.py:103)) | 仅定义 `drafts/current/snapshots/active/pins/audit` 平铺根。 | 新增 `transactions/{heads,bundles,staging,locks,...}`，但不改变 Active v0.2 字段集合；平铺根降为 projection。 |
| `current_configurations()` / `current_configuration()` ([configuration.py](../../src/sqvm/web/configuration.py:136)) | 直接枚举/读取 `current/*.json`，读取失败被跳过。 | head 存在后必须 head-aware，不能以遗漏条目或 projection 回退掩盖损坏；读协议为 head -> bundle 校验 -> head，重试后取锁。 |
| `update_current_configuration()` ([configuration.py](../../src/sqvm/web/configuration.py:175)) | 第 232 行先写 current，随后第 233--245 行分别 audit、snapshot、Active。 | 进程中断可暴露新 current 与旧 Active/snapshot/audit；迁入统一 `_commit_configuration_transaction()`。 |
| `initialize_current_calibration()` / `apply_candidates_to_current_configuration()` ([configuration.py](../../src/sqvm/web/configuration.py:248)) | 分别在第 281、346 行写 current，后续才写 audit/snapshot。 | 与上同；候选来源、snapshot、Active 和 audit 必须位于同一 bundle。 |
| `snapshot_current_configuration()` ([configuration.py](../../src/sqvm/web/configuration.py:369)) | 第 442--482 行依次建目录、写 snapshot/source candidate/pin、重写 current、写 audit、写 Active、prune。 | 这是最大混代窗口；目录创建不是 durable publish，自动 prune/物理删除必须移出提交路径。 |
| `apply_snapshot_to_current()` ([configuration.py](../../src/sqvm/web/configuration.py:485)) | 第 527--543 行先写 current/audit，再 Active 或再建新 snapshot。 | 必须用同代 catalog 解析 source candidate；恢复不 eligible snapshot 也必须一次事务完成。 |
| `set_active()` / `_activate_snapshot()` ([configuration.py](../../src/sqvm/web/configuration.py:944)) | 第 983 行写 Active pointer，随后第 984 行写 audit。 | Active 与 audit 现可分裂；事务 head 独立于 Active，避免破坏严格 Active v0.2 验证。 |
| `set_keep()` / `_pin()` ([configuration.py](../../src/sqvm/web/configuration.py:987)) | 直接创建或 `unlink` pin，再另写 audit。 | pin catalog 是 generation 状态；取消 keep 不能在提交前物理删除 authority 文件。 |
| `_prune_automatic_snapshots()` / `_snapshot_referenced()` ([configuration.py](../../src/sqvm/web/configuration.py:1345)) | 直接删除 snapshot；引用判断扫描 `current/drafts/snapshots/output`。 | 提交中只从 catalog 移除候选；GC 异步且失败只报 health。引用图必须改为 committed head chain/catalog，不能从 projection 或 orphan 推导。 |
| `_atomic_json()` ([configuration.py](../../src/sqvm/web/configuration.py:1862)) | 单文件 temp 写入和文件 `fsync` 后 `os.replace`；没有 parent directory flush、无 Windows sharing-violation 分类重试。 | 只能作为 projection 写入辅助；WP4-A 必须复用 runtime 的 same-volume、tree flush、no-replace publish 和受限 Windows rename retry 原语。 |
| `build_reference_graph()` ([references.py](../../src/sqvm/storage/references.py:80)) | 第 102/104 行直接扫描配置平铺目录与 `pins/`。 | head 存在时须只扫描验证过的 committed bundle/catalog；错误必须 fail closed，不把 orphan 或 projection 重复计入。 |
| `_scan_configurations()` ([references.py](../../src/sqvm/storage/references.py:118)) | 第 119 行 allowlist 不含 `transactions`；第 126--197 行直接读取 current/snapshot/active/audit。 | 首先扩展 authority layout/无链接校验，再实现 head-chain/hash 验证；不能仅把 `transactions` 加入 allowlist。 |
| Web/Notebook API ([server.py](../../src/sqvm/web/server.py:890)) | current candidate、PUT current、initialize、snapshot、activate、restore、keep 分别直调 Store（第 890、937--1033 行）。 | 新增可选 `operation_id` 并保持旧请求可用；所有 mutating route 统一映射稳定事务错误与新响应字段。 |

`resolve_active_context()` 在 [configuration.py](../../src/sqvm/web/configuration.py:1053) 委托给
`PlatformAuthorityResolver`。WP4-C 必须连同 resolver、实验 API 与 Notebook 调用点进行静态检索，证明
head 存在后没有官方读者直接读取 live projection。

## 现有测试基线与缺口

- [test_platform_configuration_v02.py](../../tests/test_platform_configuration_v02.py:317) 已覆盖未初始化 Active 拒绝、Active resolver 防篡改、current 保存后立即 Active、恢复 snapshot；它是 v0.2 主字段兼容基线，不覆盖崩溃/事务。
- [test_calibration_web.py](../../tests/test_calibration_web.py:304) 已覆盖 Web current/candidate/save 的 HTTP 422/409 和成功字段；它应在不传 `operation_id` 的兼容路径上保持成立。
- [test_experiment_storage_references.py](../../tests/test_experiment_storage_references.py:46) 已覆盖平铺配置引用、hash、link/hardlink fail-closed 和 Active sidecar evidence；它说明 `references.py` 是 Step 4 的 authority 边界，当前尚不覆盖 head/bundle/orphan。
- [test_calibration_api.py](../../tests/test_calibration_api.py:72) 覆盖 Active 配置驱动的 API；迁移后需证明 provenance 仍绑定同一 snapshot identity/content SHA。

这些测试不能证明跨文件原子性：当前写序列有多个 `_atomic_json()` 和直接 `unlink()`，也没有进程锁、跨进程锁、head recovery 或 subprocess hard-kill 测试。

## 获准后的可执行顺序

1. **WP4-A: schema 与存储原语。** 先冻结 JSON vectors；实现路径验证、canonical bytes/raw SHA、same-volume staging、文件/目录 fsync、atomic no-replace bundle publish、head replace、进程内 `RLock` 与跨进程 advisory lock。此阶段不接 Web API。
2. **WP4-A: reader/recovery 单元。** 实现 head/manifest/receipt/parent chain/idempotency catalog 校验和 `recover_if_needed()`；head 已存在时禁止 legacy fallback，损坏返回 recovery-required。
3. **WP4-B: 单一写入口。** 将七个设计指定的写操作逐个接入 `_commit_configuration_transaction()`，每迁一个操作先保留既有 v0.2 contract 测试，再加入该操作的 receipt/idempotency test。
4. **WP4-C: 官方读取与迁移。** 改 Store、resolver、reference graph、Web/Notebook 读取为 head-aware；完成 strict legacy inventory、generation 0 import、projection replay，最后才允许 transaction authority 切换。
5. **WP4-D: API 兼容与端到端。** Web 自动生成 operation ID，Python/Notebook 可传入；旧 payload 省略该字段仍成功，响应只增加 `transaction` 非破坏字段。
6. **平台验收。** 先函数级注入，再 subprocess 强制终止/重启，再同进程线程与跨进程并发；Windows NTFS 必测 sharing violation，Linux 为补充。最后才将 Windows integration 纳入 Step 3 已定义的 required 流程。

每步均以新建临时 configuration root 执行；重启判定必须重新构造 Store、resolver 和 reference graph，以磁盘事实而非内存对象为准。每个失败用例至少再恢复两次，验证 generation、head raw SHA 和公开 receipt 不变。

## 故障注入矩阵

| 次序 | 注入点与方式 | 提交点前后 | 立即与重启后断言 |
| --- | --- | --- | --- |
| 1 | staging 中每个 state/object JSON 的 open/write/file `fsync` 前后抛 `OSError` | 前 | head 不变；所有官方读者看到完整旧代；staging 留作隔离诊断或可安全清理。 |
| 2 | manifest/receipt/idempotency catalog 写入、复读、raw SHA 或 aggregate SHA 验证失败 | 前 | 不发布 bundle，不切 head；不得产生可被 reference graph 计数的对象。 |
| 3 | staging 子目录与根目录 `fsync` 失败（含目录不支持的 Windows 分支） | 前 | 按平台原语的明确失败路径返回 aborted；不得假设 file `fsync` 已持久化目录项。 |
| 4 | bundle no-replace publish 前、rename 中、发布后目录 flush 前失败/kill | 前 | old generation 可读；已出现但不在 chain/index 的 bundle 是 orphan，不可重试为已提交。 |
| 5 | bundle 已 durable publish 后、head temp 写/`fsync`/replace 前失败 | 前 | old generation 可读；同 operation ID 仅在 request SHA、parent、expected state 完全一致时可继续，否则隔离。 |
| 6 | head temp 文件写/`fsync`、`os.replace(head)` **前**失败或 hard-kill | 前 | old generation；不得 materialize 新 projection。 |
| 7 | `os.replace(head)` **后**、head parent directory flush 后 hard-kill | 后，唯一提交点 | 新 generation 可验证；启动恢复只向前 replay projection，绝不回滚 head。 |
| 8 | current/Active/snapshot/pin/audit 任一 compatibility projection 写入、flush、replace 失败 | 后 | transaction 仍 committed，响应为 complete 或 pending；恢复幂等重放且 projection 最终同代。 |
| 9 | snapshot prune/GC 删除失败、权限失败 | 后且非主路径 | committed catalog 不变、受保护对象未删；只记录 health，不回滚 head。 |
| 10 | `PermissionError`/Windows sharing violation 于 bundle publish、head replace、projection replace | 前或后按实际 cut point | 仅已枚举的瞬时 rename 错误可在发布原语内有界重试并记录尝试数；否则明确失败。不得广义重试 schema、hash、disk-full、permission 或非 transient I/O。 |
| 11 | disk-full/permission 于任意 staging/head/projection 点 | 分界明确 | head 前旧代；head 后新代且 recovery pending。测试不得以异常类别猜测提交结果，应读 head 验证。 |
| 12 | 两个线程、两个独立 Python 进程同 device + 同 expected hash 同时提交 | 前 | 恰一 generation 增加；另一者在持锁复读后为 409 `stale_configuration`；不同 device 可并行。 |
| 13 | 相同 operation ID + 相同 canonical request，服务端已 commit 但响应丢失后重试 | 后 | 返回首次 receipt/transaction，不再写 audit、snapshot 或 generation；后续 generation 后重试标 `superseded=true`。 |
| 14 | 相同 operation ID + 不同 request SHA；或伪造同 ID orphan bundle | 前 | 409 `idempotency_conflict`，或隔离 orphan；绝不以目录名认定 committed。 |
| 15 | head 指向缺失、link/junction/reparse/hardlink bundle；parent hash 断链/环/跳 generation | 读/恢复 | 503 `configuration_recovery_required`，禁止实验执行、禁止 fallback 到 legacy projection。 |
| 16 | source candidate 缺失、路径逃逸、字节长度/raw SHA 不符；pin/audit catalog 不符 | 读/恢复 | fail closed；不得将候选恢复为无来源状态，也不得丢引用边。 |
| 17 | legacy import 在每个设备 bundle publish、每次 head switch、bootstrap receipt 记录处 hard-kill | 迁移 | 重启按 inventory/receipt 完成；同一设备只能来自 legacy 或 head，不混读；未知/冲突/悬空 legacy 数据 503。 |

每一行均要覆盖两层测试：函数级 monkeypatch 精确命中原语，以及 subprocess 在 cut point 写出同步标记后
`terminate`/hard-kill。第二层完成后重新打开数据根，使用官方 head-aware Store、`resolve_active_context()`
和 `build_reference_graph()` 同时断言 old-or-new、不混代、reference 不重复。

## API 兼容测试顺序

1. 冻结当前 Web/Python 成功响应和 409/422 主字段；现有客户端不传 `operation_id`，服务端生成 UUID4，原字段字节语义不变。
2. 对七个 mutating API 逐个测试显式 `operation_id`：首次提交返回 `transaction_id`、generation、durability/projection status；同请求重放返回同一 receipt。
3. 测试相同 ID 不同请求的 409、过期 expected hash 的 409、锁超时的 503 + `Retry-After`、head 前失败的 aborted、head 后响应构造失败的 recovery-pending。
4. 使用 Web 保存 current 后立即调用实验 Active resolver；验证新 snapshot/source candidate/provenance 与 transaction generation 属于同一 committed bundle。
5. 使用 legacy-shaped fixture 执行 migration，再运行未改 payload 的 Web、Notebook/Python 和 reference graph contract；确认 projection 仅兼容，head 存在后内部不直接依赖它。

## 开工准入记录

项目负责人在满足上述 GO 条件后，应为 WP4-A 提供：冻结设计 revision、Windows/Linux CI run URL 或等价
required-check evidence、schema vector 审查记录、允许修改文件清单、以及一次独占配置事务写入窗口。未具备这些
材料前，本审查结论保持 **NO-GO**。
