# Stage 7.1.6 本地校准扫描执行档位

状态：第一版已实现；用于本模拟器配置，不是硬件或正式规模物理 authority

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

## 2. 双档位

| 档位 | 用途 | 单点求解 | 数值重放 | watchdog |
| --- | --- | --- | --- | --- |
| `bounded_smoke` | 入口资格与物理链审核 | isolated worker | 同步、完整 | 180 s |
| `calibration_scan` | 本地校准扫描 | isolated worker | 批后延迟复验 | 600 s |

`calibration_scan` 不修改 Stage 7.1 smoke authority，也不降低 Stage 5.1 solver 精度。它仍然
使用生产 Stage 4.1 context、Stage 5.1 physics authority、隔离 worker、原子发布和 SHA-256
数组绑定，但每点只运行一次 solver。

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
Stage 5.1 coefficient artifact
single-worker numerical arrays
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
```

验证器重新检查 QCIS、控制工件、系数工件、solver authority、数组哈希、概率、leakage、
norm error、manifest 和 receipt，但不会在读取结果时重新运行 QuTiP。

## 6. 求解器决策

同一真实点进行了求解器收敛对比：

| 档位 | 耗时 | 最大 population 误差 | 末态 infidelity | 最大 norm error |
| --- | ---: | ---: | ---: | ---: |
| 严格基线 | 116.0 s | 0 | 0 | `1.43e-11` |
| balanced 候选 | 71.0 s | `1.38e-9` | `1.41e-9` | `1.39e-9` |
| scan 候选 | 55.3 s | `1.35e-8` | `1.36e-8` | `1.35e-8` |

两个候选都会突破现有 Stage 5.1 `norm_error=1e-9` authority，因此本版本不采用。后续
若增加快速 solver，必须单独完成多频点、多幅度和边界磁通的收敛资格验证，不能只修改
`max_step` 或 tolerance。

## 7. 进度与耗时

`run_circuits`、频谱工作流和高层 API 支持逐点 progress callback。Notebook 会显示当前
phase、点序号和 circuit ID。

当前严格 solver 实测约两分钟每点，18 点双比特频谱预计需要 35 分钟以上。第一版优先
保证结果可信并消除重复重放；进一步降到交互式耗时需要长驻 batch worker、模型预检缓存
和经过资格验证的扫描 solver。

## 8. 后续落地项

1. 批次 manifest、已完成点恢复和幂等重启。
2. 整个实验的 deadline、取消和资源锁。
3. 按 policy 抽样执行独立 replay，并把批后复验状态写回工作流。
4. 长驻 worker 缓存静态 Hamiltonian、projector 和 frame。
5. 快速 solver 的多场景收敛资格验证。

在这些项目完成前，Web 继续只读实验结果，且候选更新必须由用户显式确认。
