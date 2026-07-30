# Stage 7.1.6 本地校准扫描执行档位

状态：第三版已实现；用于本地模拟器校准，不是硬件或未投影全空间演化 authority

## 1. 问题

原频谱工作流把多点校准扫描接到了 `bounded_smoke` 入口。该入口的职责是验证一个
QCIS 点能够完整通过 Stage 4.1 和 Stage 5.1，因此每点包含 isolated worker 和多次独立
数值重放，并限制 `max_worker_wall_seconds=180`。

2026-07-18 对真实 Q1 频谱点的测量结果：

```text
严格 Stage 5.1 worker                 约 122 s/点
原 bounded 发布与重复验证             最多重复求解 4 次
18 点双比特频谱估算                    约 2 h
```

这适合资格审核，不适合日常校准实验。把 Notebook 超时改成 180 秒只能通过 admission，
不能解决实际运行问题。

第一版直接复用了 Stage 5.1 的 `charge_cutoffs=[1,1,1]` 烟雾模型。2026-07-18 的真实
粗扫暴露出更严重的问题：该截断模型在当前偏置下给出的 Q1/Q2 跃迁约为
`10.986/11.893 GHz`，而已验收的静态频谱是 `5.19348/5.33163 GHz`。因此第一版即使
增加超时也不可能在 5 GHz 附近得到有效谱线。烟雾模型只能证明管线可执行，不能作为
校准模型。

## 2. 双档位

| 档位 | 用途 | 单点求解 | 数值重放 | watchdog |
| --- | --- | --- | --- | --- |
| `bounded_smoke` | 入口资格与物理链审核 | isolated worker | 同步、完整 | 180 s |
| `calibration_scan` | 本地校准扫描 | isolated projected-charge worker | 批后延迟复验 | 600 s |

`calibration_scan` 不修改 Stage 7.1 smoke authority。它继续使用生产 Stage 4.1 context，
先发布并验证 Stage 5.1 coefficient artifact 作为控制数组适配层，然后把有效 I/Q 数组交给
独立的投影电荷基 QuTiP worker。演化模型由
`configs/runtime/calibration_scan/model_authority_v1.json` 单独绑定；Stage 5.1 烟雾
physics authority 不再被记录为校准演化依据。

第三版不再把 Q1/Q2 `f01`、非谐性和交换耦合作为手工有效模型参数。worker 使用设备电容、
结参数、idle flux 和正式 Hamiltonian 配置重建 2Q1C 模型。Web 当前配置默认使用
`(7,7,7)` 电荷截断，即 `15×15×15=3375` 维底层 charge basis；各模态局域本征基默认保留
`5×3×5=75` 维后执行动力学。每点还默认重建 `(8,8,8)`、`6×4×6=144` 维对照模型，检查 `f01` 和 XY 驱动矩阵元
收敛。它目前只接收 idle-flux XY 频谱线路；包含 Z/DTN/CZ/FSIM 的线路会在 worker
admission 阶段拒绝，直到相应磁通响应模型完成资格验证。

基础和收敛对照的 charge cutoff、局域保留能级数都保存在
`control_values.simulation.calibration_model`，可以在 Web 的“控制链 > 仿真截断”中修改。
当前配置保存时自动生成并切换不可变 Active 运行版本；解析时把仿真截断和 idle flux 一并
冻结进 `CircuitExecutionContext`。`run_circuits`、worker 请求、扫描结果和外层证据均绑定
同一个配置及模型 authority 哈希。旧快照缺少仿真截断字段时使用上述默认值。

校准扫描把该 idle flux 同时绑定到 Stage 4.1 控制上下文和 projected-charge 模型：Stage 4.1
只覆盖生产电子学模板中的空闲工作点，DAC、FIR、静态混合矩阵和器件限制仍使用跟踪的生产
authority；运行时工作点另存独立 SHA-256。无 Z 指令的频谱线路因此在电子学输出中得到该
Active 工作点的绝对磁通，worker 使用同一数值构建静态 Hamiltonian 并拒绝两者不一致。

## 3. 用户入口

`run_circuits` 增加显式 `execution_profile`：

```python
run_circuits(
    circuits,
    context,
    output_root,
    execution_profile=CircuitExecutionProfile.CALIBRATION_SCAN,
    timeout_s=600.0,
)
```

底层 `run_circuits` 默认仍是 `BOUNDED_SMOKE`，防止旧调用静默改变证据语义。
`run_spectroscopy` 是面向用户的正式频谱入口。用户只给出目标频率范围和公共步进，
API 自动构造底层请求并选择 `CALIBRATION_SCAN`。一次调用只执行一份 dataset，不自动
派生其他范围、步进或确认扫描。原有 `run_active_qubit_spectroscopy_calibration` 仅作为
历史工件和旧调用兼容接口保留。

基础扫谱 Notebook 的流程为：导入 API、设置扫描参数、运行一次实验、查看本次数据与
候选校准值。有效峰通过 leakage 和 norm error 门限后可以由用户显式确认，再通过配置事务
更新 mutable current；更新成功会自动生成不可变运行版本并立即供下一次实验使用。更复杂的校准实验可以多次
调用基础接口并定义自己的候选规则。

配置写入统一使用 `apply_calibration_candidates_to_current_configuration`。实验候选采用
`calibration_candidate_v1`，一个候选可携带多个原子 changes；实验之间只改变候选值、
参数路径和质量门限，不再增加实验专用的配置更新函数。

## 4. 超时语义

`timeout_s` 是每个隔离 QuTiP worker 的 watchdog，不是整个实验的总超时。扫描 policy
采用版本化文件：v1 保留用于验证历史 64 点产物；新实验使用
`configs/runtime/calibration_scan/execution_policy_v2.json`，单线路硬上限为 10000 个逻辑采样点、
5000 ns 和 600 秒。Active 配置可通过 `max_formal_samples_per_scenario` 进一步收紧采样点上限，
但不能扩大 v2 policy 的硬上限。

调用者不能通过传参扩大 policy。后续批次总 deadline、取消和恢复由 Runtime batch
manifest 负责，不与单点 watchdog 混用。

Windows 可能在防病毒扫描或短时文件句柄占用期间，让已验证 staging 的目录重命名返回
错误 5、32 或 33。校准发布边界仅对这三类临时错误执行有界指数退避，最长约 34 秒；
每次重试仍使用原子 no-replace 发布并重新检查目标不存在。其他错误、目标冲突或 staging
消失均立即失败，不会改用复制、覆盖已有结果或降低证据验证要求。

## 5. 证据边界

每点发布：

```text
Stage 4.1 control artifact
Stage 5.1 coefficient artifact（仅作已验证控制数组适配层）
calibration model authority
single-worker projected-charge numerical arrays
scan result / manifest / report / receipt
outer QCIS circuit execution evidence
```

证据明确记录：

```text
qualification_scope          local_calibration_scan_v1
hardware_measurement         false
formal_scale_qualified       false
independent_numerical_replay false
numerical_replay_policy      deferred_batch_review
calibration_update_scope     simulator_configuration_only
evolution_model              projected_charge_basis_2q1c_v1
```

验证器重新检查 QCIS、控制工件、系数工件、校准模型 authority、solver、数组哈希、75 维状态、
截断配置、收敛证据、概率、leakage、norm error、manifest 和 receipt，但不会在读取结果时
重新运行 QuTiP。

## 6. 求解器决策

同一真实点进行了求解器收敛对比：

| 档位 | 耗时 | 最大 population 误差 | 末态 infidelity | 最大 norm error |
| --- | ---: | ---: | ---: | ---: |
| 严格基线 | 116.0 s | 0 | 0 | `1.43e-11` |
| balanced 候选 | 71.0 s | `1.38e-9` | `1.41e-9` | `1.39e-9` |
| scan 候选 | 55.3 s | `1.35e-8` | `1.36e-8` | `1.35e-8` |

两个候选都会突破现有 Stage 5.1 `norm_error=1e-9` authority，因此未用于 Stage 5.1。
第三版校准扫描采用独立的投影模型和 solver authority；它不是 `charge_cutoff=1`，也不再
通过手工 `f01` 绕开底层 Hamiltonian。正式基线与对照结果为：

| 模型 | 动态维度 | Q1 f01 (GHz) | Q2 f01 (GHz) |
| --- | ---: | ---: | ---: |
| `(7,7,7)` + `5×3×5` | 75 | `5.193479910702` | `5.331633051849` |
| `(8,8,8)` + `6×4×6` | 144 | `5.193479525968` | `5.331631961872` |

Q1/Q2 截断漂移分别约 `0.385 kHz` 和 `1.090 kHz`；驱动矩阵元漂移分别约
`6.6e-8` 和 `1.8e-7`。这些指标随每个 worker 结果发布并由读取端验证。该模型仍是低能
投影演化，结果不能冒充未投影的 3375 维全空间演化。

代表性 Q1 共振点还使用同一组 Stage 4.1 系数完成了 75/144 维完整动力学对照：Q1 `P1`
分别为 `0.3984635290` 和 `0.3984638221`，绝对差约 `2.93e-7`；leakage 差约
`5.2e-14`。75/144 维 QuTiP 求解耗时分别约 `19.1 s` 和 `55.4 s`，因此 144 维用于
资格抽样，不放入每个扫描点的同步路径。

## 7. 进度与耗时

`run_circuits` 和高层 API 支持逐点 progress callback。Notebook 显示点序号和 circuit
ID，不向用户暴露内部阶段名称。

第三版 Q1 共振单点端到端实测约 `19.1 s`，`P1≈0.3985`、Q2 串扰约 `2.2e-11`、
leakage 约 `1.9e-10`、norm error 约 `2.9e-15`。一次调用的 circuit 数量等于用户范围和
步进生成的数据点数量；`timeout_s` 仍只约束单个 worker。

## 8. 后续落地项

1. 批次 manifest、已完成点恢复和幂等重启。
2. 整个实验的 deadline、取消和资源锁。
3. 按 policy 抽样执行独立 replay，并把批后复验状态写回工作流。
4. 长驻 worker 缓存局域本征基、投影 Hamiltonian、projector 和 frame。
5. 增加 flux-to-frequency、coupler 和 ZZ 模型后，再开放 DTN/CZ/FSIM 校准扫描。
6. 把当前单个代表点扩展为多频点、多幅度的 75/144 维完整动力学收敛资格集。
7. 评估稀疏 Krylov 全空间演化，建立投影模型与 3375 维演化的抽样对照。

在这些项目完成前，Web 不启动实验或重新拟合数据。扫谱候选由 Python 实验运行时生成，
仍必须由用户显式确认后才能更新配置。
