# 配置事务 v0.1 冻结协议

## 1. 目标

本协议将 PlatformConfiguration 的 `current`、`snapshot`、`Active`、pin、audit 和候选来源作为一个
设备级状态提交。唯一提交点是 `transactions/heads/<device_id>.json` 的原子替换。Head 切换前失败时
正式读者只能看到旧代；Head 切换后失败时只能看到新代，兼容投影可在后台向前恢复。

本文件冻结实现合同；字段向量位于
`tests/fixtures/configuration_transaction_v1/vectors.json`。v0.1 只支持单机文件系统、单用户以及同一
配置根内的设备级串行写入。

## 2. 权威与布局

```text
platform-configurations/
  transactions/
    heads/<device_id>.json
    bundles/<device_id>/<operation_id>/
      manifest.json
      receipt.json
      state/
        current.json
        active.json                 # 未激活时缺省
        snapshot_catalog.json
        pin_catalog.json
        audit_head.json
        idempotency_catalog.json
      projection/
        current/
        active/
        snapshots/
        pins/
        audit/
    locks/<device_id>.lock
    projection-status/<device_id>.json
    .s/<random-8>/                  # 短暂存路径，不是恢复 authority
  current/ active/ snapshots/ pins/ audit/  # 可重建兼容投影
  drafts/                                # 非权威工作区
```

Bundle 发布后不可修改。Manifest 的 `files` 对除 `manifest.json` 外的每个文件绑定规范相对路径、字节数
与 raw SHA-256；Manifest 自身由 Head 绑定 raw SHA-256。Manifest 的 aggregate SHA 绑定其除
`aggregate_sha256` 外的全部字段。路径拒绝绝对路径、反斜杠、空段、`.`、`..`、symlink、junction、
reparse point、hardlink 和逃逸配置根的解析结果。

`projection/` 是 Bundle 内自包含的同代只读视图。根目录下的平面目录只是兼容缓存；它被删除、损坏或
未完成时不允许回退为正式 authority，也不能阻断对完整 Bundle 的读取。

## 3. 提交状态机

```text
LOCK -> COPY_OLD -> TRANSFORM -> PREPARE -> PUBLISH_BUNDLE
     -> REPLACE_HEAD -> MATERIALIZE_PROJECTION -> RETURN_RECEIPT
```

- 每设备使用进程内 `RLock` 加 OS advisory file lock，默认等待上限 5 秒。
- 文件先写入短路径 staging，逐文件 flush；目录以 no-replace 原子发布。
- Head 临时文件写入、flush 后原子替换并 flush 父目录；这是唯一提交点。
- Head 前任何失败返回 `configuration_transaction_aborted`，旧 Head 不变。
- Head 后投影失败不回滚，成功响应的 `projection_status` 为 `pending`；下次打开会幂等重放。
- Windows 只对 5、32、33 三种 rename/sharing 错误有界重试；其他 I/O 错误不重试。

## 4. 幂等与并发

客户端可提供规范小写 UUID4 `operation_id`；省略时服务端生成。请求对象以 canonical JSON 计算
`request_sha256`。

- 相同 ID、相同请求：返回首次提交的 response 和 generation，不再执行 transform。
- 旧 generation 的相同请求：仍返回原 receipt，并设置 `superseded=true`。
- 相同 ID、不同请求：409 `idempotency_conflict`。
- 已发布但 Head 未切换的同 ID Bundle，仅当 request SHA 和当前 parent 完全一致时允许向前完成。
- 同一 expected hash 的并发业务写在锁内重新校验，先提交者成功，后提交者返回业务 409。
- `idempotency_catalog` 在 v0.1 中不裁剪，并逐项绑定 receipt 路径与 raw SHA。

## 5. API 合同

七个正式写入口统一事务化：

1. `update_current_configuration`
2. `initialize_current_calibration`
3. `apply_candidates_to_current_configuration`
4. `snapshot_current_configuration`
5. `apply_snapshot_to_current`
6. `set_active`
7. `set_keep`

旧 Draft 发布和未引用 Snapshot 删除也作为兼容事务入口执行，防止它们在 Head 建立后写入非权威平面
目录。Draft 创建、编辑和校验仍只属于非权威工作区。

成功响应保留原业务字段并增加：

```json
{
  "transaction": {
    "transaction_id": "<operation_id>",
    "operation_id": "<operation_id>",
    "generation": 12,
    "durability_status": "committed",
    "projection_status": "complete",
    "superseded": false
  }
}
```

Web JSON 请求接受 `operation_id`。DELETE Snapshot 使用 `X-SQVM-Operation-ID`。锁超时响应同时返回
`Retry-After` header、`retry_after`、稳定 `code` 和 `transaction_id`。Notebook/Python 的通用候选更新
API 也接受 `operation_id`。

## 6. 正式读取边界

以下读者必须先验证 Head、Manifest、完整 Bundle inventory、parent chain 和 idempotency catalog：

- `PlatformConfigurationStore` 的 current/snapshot/Active 列表与详情；
- `PlatformAuthorityResolver` 和 `resolve_active_context()`；
- storage reference graph；
- Web 配置管理接口；
- calibration Python/Notebook API。

Head 存在但不可验证时统一 fail closed：503 `configuration_recovery_required`；不得读取平面目录。引用图
将该错误变成 `scan_incomplete=true`，从而禁止 destructive storage 操作。同一配置根若同时存在已建立
Head 的设备和仍无 Head 的旧设备，也按不完整扫描处理；不得只扫描已迁移设备后继续删除实验数据。

## 7. Legacy generation 0

设备首次写入或读取 current 时，在设备锁内复制现有 current、该设备 snapshots/source candidate、Active、
pins 和可归属 audit，生成 generation 0 Bundle 和 import receipt。导入拒绝非 canonical JSON、链接、
hardlink、未知 Snapshot 文件、目录 ID 不一致及无法读取的 authority。原文件保留并降级为兼容投影。

本版本按设备懒迁移；Draft 不进入 authority Bundle。首次从 Draft 发布有效 Snapshot 时，若 current 仍是
自动 bootstrap 的初始版本，则在同一事务中让 current 采用该 Snapshot，以保持既有用户语义。

## 8. 恢复与路径预算

- Bundle 已发布、Head 未切换：同 operation/request/parent 可恢复提交；否则 409，不猜测。
- Head 已切换、兼容投影不完整：正式读取新 Bundle，并幂等重建投影。
- Head/Manifest/Bundle/parent/receipt 任一缺失、字段漂移或哈希不符：503，禁止实验执行。
- Staging 使用 `.s/<8-char>`，避免 Windows 暂存文件名超过传统 260 字符限制。
- 合同测试配置根最长 150 字符；Windows I/O 对事务 staging、Bundle 和临时文件使用 extended-length
  path，测试覆盖最终文件最长 320 字符以及较长系统临时根下的真实提交。对外 manifest 仍保存普通相对路径，
  不泄露 `\\?\` 平台前缀。

## 9. 变更规则

新增必填字段、改变 canonical bytes、缩短幂等保留期、改变唯一提交点或允许 legacy fallback 都必须发布新
artifact version。新增可选业务操作可复用 v0.1 引擎，但必须先加入冻结向量、故障矩阵和 Web/Python
契约测试。
