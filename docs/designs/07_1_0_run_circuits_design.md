# Stage 7.1.0 `run_circuits` 统一线路执行接口设计

状态：初版已实现；当前仅具备 bounded smoke 资格，不是生产物理后端 authority

## 1. 架构定位

后续所有校准实验统一遵循：

```text
实验原理
  -> 生成一组 QCIS circuits
  -> run_circuits
  -> 每条 circuit 的四个 dressed population、q1/q2 P0/P1、leakage 和证据
  -> 实验专用数据处理与可视化
  -> 生成配置参数更新候选
  -> 人工确认
  -> 创建新的配置 revision
```

实验模块不得直接调用 Stage 4.1、Stage 5.1、QuTiP worker 或内部 Hamiltonian 接口。

`run_circuits` 只执行线路并返回模型结果，不负责拟合、绘图、推荐或更新配置。

## 2. 公共接口

```python
run_circuits(
    circuits: Sequence[QCISCircuit],
    context: CircuitExecutionContext,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    readout_qubit: Sequence[Sequence[str]] = ((),),
    timeout_s: float = 180.0,
    max_circuits: int = 64,
) -> tuple[CircuitResult, ...]
```

每条 `QCISCircuit` 包含稳定的 `circuit_id` 和完整 QCIS source。返回顺序与输入顺序一致。

`CircuitExecutionContext` 还显式绑定 `initial_state_id` 和 `observable_set_id`。当前 smoke 后端只接受：

```text
initial_state_id = lab_ground
observable_set_id = dressed_computational_populations_v1
```

这两个字段不能由实验静默省略或解释成其他物理对象；新初态和新 observable set 必须通过后续版本化后端 authority 扩展。

### 2.1 `readout_qubit` 结果选择

`readout_qubit` 是有序的 QAgent 分组列表：

```python
readout_qubit=[[]]
# 默认展开为当前后端所有量子比特的 singleton groups。

readout_qubit=[["Q1"], ["Q2"]]
# 分别返回 Q1、Q2 的 P0/P1。

readout_qubit=[["Q1", "Q2"]]
# 返回 P00/P01/P10/P11；bitstring 顺序与组内 QAgent 顺序一致。

readout_qubit=[["Q1"], ["Q2"], ["Q1", "Q2"]]
# 同时返回两个 singleton marginals 和 joint distribution。
```

规则如下：

- `[[]]` 是唯一允许的空组形式，表示“所有支持的量子比特分别返回”。
- 未知 QAgent、非量子比特 QAgent、组内重复、重复 group 和混合空组都拒绝。
- group 可以重叠，因此 singleton 与 joint 可以在同一次调用中同时请求。
- group 内顺序具有物理含义；`["Q2", "Q1"]` 的第一位对应 Q2。
- 所有结果都是 computational-subspace nonconditional population，满足 `sum(Pbits) + leakage = 1`，不按 leakage 重新归一化。
- 该参数只选择模型结果，不执行 QCIS `M`、shot、IQ、assignment 或硬件 readout，claim 仍保持 `readout=false`。

`CircuitResult.readout_probabilities` 按请求 group 顺序返回。原有 `CircuitResult.probabilities` 保留为 singleton groups 的兼容视图；只请求 joint group 时该兼容视图为空。

单次调用最多接收 64 条 circuit，也可以用 `max_circuits` 将本次实验限制收紧。所有 circuit 必须先全部通过 SET、authority 和 QCIS 编译检查，才会开始第一条物理演化；因此后续 circuit 的静态错误不会留下已执行前缀。运行期间的物理失败仍按逐点不可变 artifact 处理，正式批次恢复和幂等语义由后续 Runtime 0.3 batch manifest 承担。

当前执行链为：

```text
QCIS circuit + SET preamble
  -> circuit-local configuration overlay
  -> QCIS v0.3 executable source
  -> compile_qcis
  -> Stage 4.1 verified physical controls
  -> Stage 5.1 verified coefficients
  -> isolated QuTiP evolution + independent replay
  -> circuit execution evidence
  -> CircuitResult
```

## 3. `SET` 指令

### 3.1 语法

```qcis
SET <QAgent> setting.<active_setting_key>.<field_path> <value>
```

示例：

```qcis
SET Q1 setting.active_xy2_setting.amplitude_GHz 0.012
SET C setting.active_cz_setting.waveforms.coupler.coupling_detune_GHz 0.020
X2P Q1
CZ C
```

### 3.2 作用范围

- `SET` 只能出现在 circuit 的前导区，即所有可执行指令之前。
- 一条 circuit 中同一目标路径只能设置一次。
- 同一条 circuit 可以修改同一个 setting 的多个不同字段；这些修改共享同一个 base setting hash，全部应用后生成同一个 effective setting hash。
- v1 只接受 canonical finite binary64 数值；整数字段必须接收整数值。
- `SET` 只影响当前 circuit，不修改内存中的原配置对象，也不写入配置文件。
- 每条 circuit 从同一个传入配置快照独立创建 overlay；前一条 circuit 的 `SET` 不会泄漏到后一条。
- `SET` 只能修改 `CircuitExecutionContext.settable_paths` 白名单中的路径。

### 3.3 路径解析

`setting.active_xy2_setting.amplitude_GHz` 的解析步骤为：

1. 在 `gate_configuration[Q1]` 中读取 `active_xy2_setting`。
2. 得到实际 setting ID，例如 `q1_xy2`。
3. 在 `waveform_registry.settings[q1_xy2]` 中找到已接受 setting。
4. 验证原 `setting_hash`、`status` 和 `target`。
5. 在深拷贝上修改 `amplitude_GHz`。
6. 计算 effective setting hash 和 circuit overlay hash。

以下身份字段禁止通过 `SET` 修改：

```text
setting_id, target, revision, calibration_run_id, status, setting_hash,
gate_type, transition, mapper_id, mapper_type, wave_index
```

### 3.4 直接波形参数

`PLS`、`PLSXY`、`DTN` 等已经在指令中显式给出的波形参数直接写入 QCIS，不使用 `SET`：

```qcis
PLSXY Q1 1 -1 128 0.004 5.105 0 0 32
```

例如 spectroscopy 的 drive frequency 是 `PLSXY` 参数；Rabi 若使用显式 `PLSXY`，其 amplitude 也是指令参数。只有扫描配置表内已有 setting 字段时才使用 `SET`。

## 4. Authority 与证据

- 传入配置 authority 必须先通过原始 hash 验证。
- overlay 在深拷贝上应用，原 authority 对象保持不变。
- circuit SHA-256 覆盖包含 `SET` 的完整 source。
- executable QCIS SHA-256 覆盖移除 `SET` 后实际交给编译器的 source。
- overlay 记录目标、路径、值、setting ID、base setting hash 和 effective setting hash。
- `settable_paths` 白名单具有独立 hash，并与 overlay 一起进入 circuit execution evidence。
- QCIS plan authority hash 绑定 effective waveform registry。
- 临时 overlay 不能成为已接受校准，也不能直接更新正式配置。

每条 circuit 在 Stage 7.1 model evidence 外再发布一层不可变 circuit execution evidence，绑定：

- 包含 SET 的完整 QCIS source 和 hash；
- 移除 SET 后的 executable QCIS source 和 hash；
- SET 白名单 hash、完整 overlay entries、base/effective setting hash；
- AST、compiler trace 和 effective authority hash；
- Stage 7.1 manifest/receipt hash；
- Stage 5.1 evolution manifest/receipt、array inventory 和每个结果数组的 hash；
- 最终 dressed population、P0/P1、leakage 和 norm error；
- requested/effective `readout_qubit`、QAgent-to-component 映射及每组 bitstring probability；
- `measurement=false`、`readout=false`、`calibration_eligible=false` 和 `recommendation_eligible=false` claim。

加入 `readout_qubit` 投影选择后的 circuit execution evidence schema 为 `0.2`。此前 `0.1` 尚未形成正式发布兼容基线，因此不作为已冻结的可写版本；验证器对未知版本 fail closed。

当前 `CircuitExecutionContext` 只接受内部已经完成 hash 自检的 authority snapshot。生产入口不能允许普通实验请求自行构造该对象或自行扩大 `settable_paths`；后续 Runtime 0.3 必须从已批准的 snapshot 和实验 policy 创建 context。

## 5. P0/P1 定义

Stage 5.1 使用 q1/c/q2 张量顺序，并提供 dressed computational projectors：

```text
000, 100, 001, 101
```

`CircuitResult.dressed_populations` 原样保留以下四个 primitive observable，供频谱、两比特条件概率等实验选择：

```text
population_000, population_100, population_001, population_101
```

最终时刻的计算子空间边缘值定义为：

```text
Q1.P0 = population_000 + population_001
Q1.P1 = population_100 + population_101

Q2.P0 = population_000 + population_100
Q2.P1 = population_001 + population_101
```

两者都满足：

```text
P0 + P1 + leakage = 1
```

这里的 `P0/P1` 没有按 `1 - leakage` 条件归一化，不能假设 `P0 + P1 = 1`。接口同时返回 `computational_population = P0 + P1` 和 leakage。

结果 schema 固定携带 `normalization = computational_subspace_nonconditional`，下游不得将其改写为条件概率。

返回的是 dressed computational projector 上的闭系统模型 population，不是 shot、IQ、readout assignment 或硬件测量结果。

## 6. 配置更新边界

`run_circuits` 不更新配置。后续实验必须单独完成：

1. 验证所有 circuit results 和 evidence。
2. 按实验原理进行拟合、排序或目标函数计算。
3. 生成可视化和诊断。
4. 生成不可变参数更新候选及适用范围。
5. 由用户人工接受或拒绝。
6. 只有接受操作才能生成新的配置 revision、`setting_hash` 和 lineage 记录。

发布后的单条结果使用 `verify_circuit_result(evidence_root, context, repository_root)` 独立复核。该入口从 evidence 恢复完整 QCIS，重新应用并验证 SET overlay，重新编译 QCIS，调用 Stage 7.1 独立 verifier 重放模型证据，并重新核对最终 observable。验证不依赖原运行进程中的 `CompiledCircuit` 或 handle。

## 7. 当前限制

- 当前复用 Stage 7.1 `bounded_smoke_only` entrance。
- 每次底层执行仍是一个 bounded model point，最多 64 个逻辑采样点和 96 个有效采样点。
- 当前固定使用 Stage 5.1 smoke 截断和求解策略。
- `M`、`RST`、`SWD`、`SWA` 等 parse-only 指令仍不可执行。
- 宏门必须具有完整的已注册 setting 和 mapper。
- 当前结果不具备校准或推荐资格。
- 当前初态仍由 Stage 5.1 smoke 后端固定为 dressed lab ground；支持 Ramsey、CZ/FSIM、XEB 等正式实验前，生产 authority 必须显式绑定初态或等价的 QCIS 制备线路。
- 当前多条 circuit 是受限 facade 顺序执行，不是具有原子发布、恢复和幂等语义的生产扫描批次。
- 生产使用前仍需正式规模数值批准、物理后端注册和新的 entrance authority。

## 8. 后续校准实验职责

每个实验只需实现：

```text
build_circuits(experiment_config, calibration_snapshot) -> tuple[QCISCircuit, ...]
analyze(results) -> AnalysisArtifact
render(analysis) -> VisualizationArtifact
propose_update(analysis) -> ConfigurationUpdateCandidate
```

实验不得实现自己的 QuTiP 调度、波形旁路、P0/P1 计算或配置写入逻辑。实验可以从统一返回的 primitive dressed populations 中选择已注册 observable，但不能重新解释 projector 顺序或把模型 population 描述为测量结果。
