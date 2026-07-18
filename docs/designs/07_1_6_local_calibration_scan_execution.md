# Stage 7.1.6 本地校准扫描执行档位

状态：第二版已实现；用于本地模拟器校准，不是硬件或正式规模物理 authority

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
| `calibration_scan` | 本地校准扫描 | isolated effective-model worker | 批后延迟复验 | 600 s |

`calibration_scan` 不修改 Stage 7.1 smoke authority。它继续使用生产 Stage 4.1 context，
先发布并验证 Stage 5.1 coefficient artifact 作为控制数组适配层，然后把有效 I/Q 数组交给
独立的双 qutrit QuTiP worker。演化模型由
`configs/runtime/calibration_scan/model_authority_v1.json` 单独绑定；Stage 5.1 烟雾
physics authority 不再被记录为校准演化依据。

第二版模型参数为已验收静态频谱的 Q1/Q2 `f01`、设备先验的非谐性和明确为零的交换耦合。
它适合把 idle-flux XY 频谱流程打通；包含 Z/DTN/CZ/FSIM 的线路会在 worker admission
阶段拒绝，直到相应磁通响应模型完成资格验证。

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
`run_active_qubit_spectroscopy_calibration` 是面向用户的正式频谱入口，默认选择
`CALIBRATION_SCAN`。

## 4. 超时语义

`timeout_s` 是每个隔离 QuTiP worker 的 watchdog，不是整个实验的总超时。扫描 policy
位于 `configs/runtime/calibration_scan/execution_policy_v1.json`，第一版上限为 600 秒。

调用者不能通过传参扩大 policy。后续批次总 deadline、取消和恢复由 Runtime batch
manifest 负责，不与单点 watchdog 混用。

## 5. 证据边界

每点发布：

```text
Stage 4.1 control artifact
Stage 5.1 coefficient artifact（仅作已验证控制数组适配层）
calibration model authority
single-worker effective-model numerical arrays
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
evolution_model              effective_two_qutrit_v1
```

验证器重新检查 QCIS、控制工件、系数工件、校准模型 authority、solver、数组哈希、概率、leakage、
norm error、manifest 和 receipt，但不会在读取结果时重新运行 QuTiP。

## 6. 求解器决策

同一真实点进行了求解器收敛对比：

| 档位 | 耗时 | 最大 population 误差 | 末态 infidelity | 最大 norm error |
| --- | ---: | ---: | ---: | ---: |
| 严格基线 | 116.0 s | 0 | 0 | `1.43e-11` |
| balanced 候选 | 71.0 s | `1.38e-9` | `1.41e-9` | `1.39e-9` |
| scan 候选 | 55.3 s | `1.35e-8` | `1.36e-8` | `1.35e-8` |

两个候选都会突破现有 Stage 5.1 `norm_error=1e-9` authority，因此未用于 Stage 5.1。
第二版校准扫描采用独立的 9 维双 qutrit 模型及自己的 solver authority；这不是放松
Stage 5.1 精度，而是缩小模型声明范围。模型参数、solver 和来源文件都进入独立 authority
哈希，结果不能冒充完整电路 Hamiltonian 的正式规模演化。

## 7. 进度与耗时

`run_circuits`、频谱工作流和高层 API 支持逐点 progress callback。Notebook 会显示当前
phase、点序号和 circuit ID。

第二版单点端到端实测约 `6.3 s`；三点双比特并行粗扫约 `16.5 s`。示例粗扫在
`5.2/5.3 GHz` 得到 Q1/Q2 激发概率约 `0.290/0.148`，两个峰均位于扫描内部且通过
`min_contrast=0.01`。完整 28 点示例预计约 3 分钟，`timeout_s` 仍只约束单个 worker。

## 8. 后续落地项

1. 批次 manifest、已完成点恢复和幂等重启。
2. 整个实验的 deadline、取消和资源锁。
3. 按 policy 抽样执行独立 replay，并把批后复验状态写回工作流。
4. 长驻 worker 缓存静态 Hamiltonian、projector 和 frame。
5. 从静态频谱工件自动生成双 qutrit 参数，替换当前显式 authority 参数。
6. 增加 flux-to-frequency、coupler 和 ZZ 模型后，再开放 DTN/CZ/FSIM 校准扫描。
7. 有效模型与完整 charge-basis 模型的多频点、多幅度收敛资格验证。

在这些项目完成前，Web 继续只读实验结果，且候选更新必须由用户显式确认。
