# Runtime 0.3 校准批次执行详设

## 1. 目的

Runtime 0.3 在现有 `run_circuits` 单点物理执行能力之上提供可恢复的校准批次。它不解释频谱、Rabi、
Ramsey 或 CZ 的实验原理；实验适配器只负责生成有序 QCIS circuit、定义读出对象和处理返回结果。

本阶段完成后停止在 Rabi 校准实验开发之前。频谱迁移为第一个适配器，用于证明批次接口可复用。

## 2. 范围

本版本实现：

1. 规范 UUID4 `batch_id` / `operation_id`；
2. 全批次预编译和确定性 point identity；
3. 每点不可变 receipt、证据 inventory 和结果投影；
4. 原子 `head.json` 进度提交；
5. 同 operation、同请求的完成态重放和中断后续跑；
6. 同 operation、不同请求的 409 等价冲突；
7. 总 deadline、逐点 watchdog、协作取消；
8. 批次锁与设备资源锁；
9. 频谱 Python API 的 operation ID、恢复和完成态重放；
10. 崩溃、并发、篡改、deadline、取消和发布失败测试。

本版本不实现：

- Rabi、Ramsey、DRAG、CZ 或 FSIM 实验；
- 点并行执行；
- 在 worker 调用中间强制终止线程；
- 多机调度、队列和用户权限；
- 从无法验证的孤儿点猜测结果；
- 修改已发布实验目录。

## 3. 分层

```text
run_spectroscopy / future calibration adapter
  -> build ordered QCISCircuit list
  -> run_circuit_batch (Runtime 0.3)
       -> compile every circuit before the first point
       -> run_circuits([one circuit])
       -> immutable point receipt
       -> atomic batch head
  -> experiment-specific dataset / analysis / candidates
  -> existing immutable experiment publication
```

`run_circuit_batch` 只依赖公开 `QCISCircuit`、`CircuitExecutionContext`、`CircuitResult` 和
`run_circuits` 合同。后续实验不得绕过该层自行实现恢复、锁或批次 deadline。

## 4. 文件布局

```text
<experiment-staging>/execution/
  batch/
    request.json
    head.json
    points/<point-id>.json
  circuit_execution/<point-id>/...
  <point-id>/...                         # model evidence

<experiment-collection>/.runtime-v03/
  locks/batches/<batch-id>.lock
  locks/resources/<resource-key>.lock
  cancellation/<batch-id>.json
```

`request.json` 和 point receipt 创建后不可修改。`head.json` 是批次进度的唯一提交点，使用临时文件、
fsync、原子替换和父目录 flush。协调根不进入已发布证据，只保存 advisory lock 文件和 create-only 取消请求。

## 5. 请求身份

批次请求绑定：

- batch ID 和实验适配器 request；
- circuit 的顺序、ID、源文本和 SHA-256；
- Active PlatformConfiguration snapshot/content/context SHA；
- 编译 authority、idle flux、settable paths、初态和 observable set；
- readout groups、execution profile、逐点 timeout、总 deadline 和 circuit 上限；
- 资源键。

请求使用 canonical JSON 计算 `request_sha256`。相同 ID 的已有请求必须字节等价；否则返回
`batch_idempotency_conflict`。时间戳和 recommendation ID 属于首次尝试 metadata，不参与调用方重试比较，
后续重试只读取首次值。

## 6. 状态机

```text
prepared -> running -> completed
                    -> interrupted -> running
                    -> deadline_exceeded -> running
                    -> cancelled
```

- `completed`：只读验证并返回原结果，不执行 circuit；
- `interrupted` / `deadline_exceeded`：同请求可开始新 attempt；
- `cancelled`：同 ID 保持终态，重新执行必须使用新 ID；
- Head 缺失但 request 已存在、point receipt 不完整、字段或哈希漂移：fail closed；
- 证据已生成但 point receipt 尚未提交时，只能通过注入的正式 result loader 完整验证后收养；验证失败则
  `batch_recovery_required`，不得覆盖原证据。

每完成一个点，顺序为：

```text
RUN_POINT -> VERIFY_RESULT -> WRITE_POINT_RECEIPT -> REPLACE_HEAD
```

因此 Head 前崩溃最多留下可验证孤儿证据；Head 后崩溃时该点必然可重放。

## 7. Deadline 与取消

`point_timeout_s` 继续约束单个隔离 worker。`batch_deadline_s` 从每次有效 attempt 获得资源锁后开始计时；
进入点前和点返回后检查。传给 worker 的 timeout 为 `min(point_timeout_s, remaining_deadline)`。

取消为协作式：进程内 `CancellationToken` 或协调根的 create-only cancellation request 在点之间生效，
不在 QuTiP 调用中间杀线程。取消后 Head 进入 `cancelled`，已完成点保留但同 ID 不再恢复。

## 8. 并发与资源锁

每个批次先取得 batch OS advisory lock，再取得 resource lock。resource key 绑定平台 snapshot、authority context
和执行 profile。锁等待有界；冲突返回 `batch_busy` 和 `retry_after=1`。进程退出会释放 OS lock，锁文件本身
不是占用状态，也不允许按 PID 猜测删除。

## 9. 频谱接入

`run_spectroscopy` 新增：

- `operation_id: str | None`：省略时生成 UUID4；提供时决定 run ID、staging 和发布目录；
- `batch_deadline_s: float`：默认 3600 秒；
- `cancellation_token`：进程内协作取消。

同 operation 的行为：

1. 发布目录完整：验证完整扫描和 batch receipt，返回原 `SpectroscopyRun`；
2. staging 存在：验证首次 request，删除非权威 recovery marker，复用已提交点并继续；
3. ID 已绑定不同频率范围、波形、配置 snapshot 或执行 policy：冲突；
4. 发布结果不完整或不可信：fail closed，不回退 staging。

扫描 workflow 增加 `runtime_batch` 绑定，记录 batch ID、request SHA、head SHA、attempt 次数和 point 数量；
head SHA 继续传递绑定全部 point receipt。
扫描 evidence verifier 必须验证该绑定及 completed batch，Web 仍读取原 dataset/plot DTO。

## 10. 验收

必须覆盖：

1. 多点成功、顺序和完成态零执行重放；
2. 第 N 点失败后只重跑未提交点；
3. receipt 后/Head 前强制退出后的孤儿收养；
4. 同 ID 不同请求冲突；
5. deadline 前后边界和逐点 timeout 收紧；
6. 进程内/跨进程取消；
7. 两批次争用同资源只能有一个执行；
8. request、Head、receipt、evidence 篡改 fail closed；
9. 频谱同 operation 中断恢复与发布后重放；
10. Web、存储归档和配置候选更新保持兼容；
11. contract、integration、physics、evidence 和 clean-checkout CI 全部通过。
