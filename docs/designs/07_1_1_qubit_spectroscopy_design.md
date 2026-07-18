# Stage 7.1.1 通用量子比特频谱实验详细设计

状态：核心 bounded pilot 已实现；不是生产 Runtime 准入 authority

## 1. 目的

`qubit_spectroscopy_v1` 是统一建立在 `run_circuits` 上的量子比特 `|0> -> |1>` 频谱实验。实验目标不写死为 `Q1` 或 `Q2`，而是由 QAgent registry 中的能力记录决定。

一个合法目标必须同时满足：

- QAgent 已注册且 `component_role = qubit`；
- 具有独立 XY 物理通道；
- 具有有效的 `reference_frequency_authority`；
- 具有本次 bootstrap 或已接受 calibration snapshot 中的频率先验；
- 后端 observable registry 能把该 QAgent 映射到确定的 dressed computational projector。

该映射由新增的 `qubit_capability_adapter_v1` authority 提供，至少包含：

```text
qagent
backend_component_slot
xy_channel
reference_frequency_key
idle_flux_key
projector_bit_index
canonical_order_index
```

当前后端的 component slot 仍只有 `q1`、`q2`，primitive projector 也仍是固定二比特模型。因此 v1 可以让任意注册 QAgent 名称映射到这两个物理 slot，但不能凭改名扩展成第三个比特。未知、重复或不完整映射必须拒绝。

当前二比特模型后端最多支持两个目标，但协议本身不依赖目标名。当前设备可以选择：

```text
[Q1]
[Q2]
[Q1, Q2]
```

后续设备可以使用其他 QAgent 名称，只要后端 capability 和 projector registry 明确支持。

## 2. 实验模式

v1 支持两种执行模式：

| 模式 | 目标数 | 含义 |
| --- | ---: | --- |
| `single` | 1 | 对一个注册量子比特执行独立频谱扫描 |
| `parallel_lockstep` | 2 | 每条 circuit 在两个独立 XY 通道上同时施加频谱脉冲，两条频率轴按索引配对 |

这里的“并行”指同一条 QCIS circuit、同一次 Hamiltonian 演化中的物理并行驱动，不表示 `run_circuits` 同时启动多个 QuTiP 进程。

v1 不支持隐式笛卡尔积。完整二维 `f_a x f_b` 响应面属于单独的 `qubit_spectroscopy_crosstalk_map_v1` 实验，因为它需要二维分析、不同的点数预算和不同的校准资格。

## 3. 统一工作流

```text
实验请求与已批准 policy
  -> 生成冻结的 point expansion table
  -> 为每个 point 生成 concrete QCIS circuit
  -> run_circuits
  -> primitive dressed populations 与 circuit evidence
  -> 每个目标的频谱曲线和并行诊断
  -> 粗扫/细扫/单驱动确认分析
  -> 每个目标独立的配置更新候选
  -> 用户逐目标确认
  -> 创建新的 calibration snapshot revision
```

实验 adapter 不得直接调用 Stage 4.1、Stage 5.1、QuTiP worker 或 Hamiltonian 内部接口，也不得自行更新配置。

## 4. 注册标识

| 项目 | 标识符 |
| --- | --- |
| 实验 | `qubit_spectroscopy_v1` |
| circuit builder | `qubit_spectroscopy_circuit_builder_v1` |
| 脉冲 policy | `qubit_spectroscopy_pulse_policy_v1` |
| observable set | `qubit_spectroscopy_observables_v1` |
| point result | `qubit_spectroscopy_point_result_v1` |
| 粗扫分析 | `qubit_spectroscopy_coarse_peak_v1` |
| 细扫分析 | `qubit_spectroscopy_refined_peak_v1` |
| 并行等价性检查 | `parallel_spectroscopy_equivalence_v1` |
| 校准候选 | `qubit_reference_frequency_delta_v1` |

所有标识符必须解析到不可变内容和 authority hash。请求不能自行注册 builder、observable 或分析器。

## 5. 实验请求

建议的 Runtime 0.3 实验层请求如下：

```yaml
schema_version: "0.3"
experiment_id: qubit_spectroscopy_v1
execution_mode: parallel_lockstep
run_phase: coarse
backend_id: stage51_qutip_closed_system_v1
device_snapshot_ref:
  path: configs/devices/2q1c2r.yaml
  sha256: ...
calibration_snapshot_ref:
  path: configs/calibration/platform_uncalibrated_v1.json
  sha256: ...
targets:
  - qagent: Q1
    pulse_policy_ref: sha256:...
  - qagent: Q2
    pulse_policy_ref: sha256:...
scan:
  pairing: lockstep
  axes:
    - qagent: Q1
      name: drive_frequency
      unit: GHz
      values: [...]
    - qagent: Q2
      name: drive_frequency
      unit: GHz
      values: [...]
execution:
  initial_state_id: lab_ground
  backend_observable_set_id: dressed_computational_populations_v1
  readout_qubit:
    - [Q1]
    - [Q2]
    - [Q1, Q2]
  max_points: 64
  point_budget_seconds: ...
  run_budget_seconds: ...
  fail_fast: true
  solver_policy_ref: sha256:...
  numerical_policy_ref: sha256:...
analysis:
  experiment_observable_set_id: qubit_spectroscopy_observables_v1
publication:
  allow_existing_target: false
```

`single` 模式使用一个 target、一个 axis，并固定 `scan.pairing = single`。

`backend_observable_set_id` 是 `run_circuits`/Stage 5.1 返回 primitive dressed populations 的物理契约；`experiment_observable_set_id` 是频谱实验如何从 primitive populations 聚合每个目标响应量的分析契约。二者必须分别哈希绑定，不能互换。

`single` 模式显式请求 `readout_qubit=[[target]]`。`parallel_lockstep` 请求两个 singleton groups 和一个 joint group：

```python
readout_qubit=[[target_a], [target_b], [target_a, target_b]]
```

这样同一个 circuit receipt 同时绑定两个目标 marginal 和 `P00/P01/P10/P11`。primitive dressed populations仍始终保留，实验分析不能只保存聚合结果。

判别字段必须满足精确交叉约束：

```text
execution_mode = single
  <=> scan.pairing = single 且 target_count = 1

execution_mode = parallel_lockstep
  <=> scan.pairing = lockstep 且 target_count = 2
```

任何其他组合直接拒绝，不能由 adapter 猜测调用者意图。

### 5.1 目标顺序

请求中的目标必须无重复，并按 `qubit_capability_adapter_v1.canonical_order_index` 严格递增排列。circuit builder 使用同一顺序生成 QCIS 行，避免物理等价的线路仅因文本行顺序不同而产生两个身份。order index 缺失、重复或请求顺序不规范时直接拒绝，不做静默排序。

### 5.2 轴规则

- 每个目标必须恰好有一个 `drive_frequency` 轴，单位为 `GHz`。
- 频率数组必须显式、有限、严格升序且无重复。
- Runtime 不推导范围、步长，不排序，也不在运行中插点。
- `single` 模式只有一个频率数组。
- `parallel_lockstep` 要求两个数组长度完全相同。
- `parallel_lockstep` 的 point `i` 精确定义为 `(f_target_0[i], f_target_1[i])`。
- 禁止 broadcast、自动补齐、截断较长数组或隐式笛卡尔积。

两条并行频率轴通常围绕各自不同的先验频率构造，因此 point `i` 的两个频率数值不要求相等。

现有 `runtime.scan.expand_scan()` 固定执行笛卡尔积，而且现有 coordinate map 不能容纳两个同名 `drive_frequency`。Runtime 0.3 的 spectroscopy adapter 必须实现专用、版本化的 `expand_qubit_spectroscopy_points_v1`，直接生成以 QAgent 为键的 `coordinates_GHz` mapping；禁止复用旧 expander。两个各含 2 个频率的 lockstep axes 必须得到 2 个 point，而不是 4 个。

## 6. Point expansion 与身份

实验 adapter 必须在执行前生成并发布完整 point table：

```yaml
points:
  - point_index: 0
    coordinates_GHz:
      Q1: 4.95
      Q2: 5.15
    point_input_sha256: ...
    circuit_id: sp_...
```

`point_input_sha256` 至少绑定：

- 实验、builder、pulse policy、backend observable 和 experiment observable authority hash；
- device 与 calibration snapshot hash；
- execution mode、run phase 和有序目标集合；
- 当前 point 的每个目标频率；
- 初态、后端、求解器和数值 policy；
- concrete QCIS hash。

`circuit_id` 精确定义为：

```text
"sp_" + point_input_sha256.lower()[0:32]
```

它满足当前 `[a-z][a-z0-9_]{0,63}` 接口限制。完整 hash 才是严格身份。expansion table 必须验证所有 `circuit_id` 唯一；短前缀冲突、已有输出目标冲突均 fail closed，不得临时延长前缀或覆盖旧结果。不得用浮点字符串拼接或单纯 point index 作为物理身份。

所有 point 必须在第一次调用 `run_circuits` 前完成构造和 QCIS 编译检查。

## 7. QCIS circuit 生成

### 7.1 单目标

以目标 `QX` 为例：

```qcis
PLSXY QX 1 0 <length> <amplitude> <frequency> 0 0 <r_sigma>
```

### 7.2 双目标并行

```qcis
PLSXY QA 1 0 <shared_length> <amplitude_a> <frequency_a> 0 0 <r_sigma_a>
PLSXY QB 1 0 <shared_length> <amplitude_b> <frequency_b> 0 0 <r_sigma_b>
```

冻结规则：

- `waveIndex = 1`，使用已审核的 gaussian 波形。
- `tStart = 0`，使用绝对起始时间。
- 并行线路的两条脉冲必须具有相同 `tStart`、`length` 和结束时刻。
- amplitude 与 `r_sigma` 可以按目标使用不同的 pulse policy 值。
- v1 固定 `phase = 0`、`dragAlpha = 0`。
- 两条 PLSXY 位于不同物理 XY 通道，实际区间均为 `[0, shared_length)`。
- 当前线路在脉冲后立即结束，因此不需要 `B`；`B` 也不能移动绝对 `tStart`。
- 如果两个目标的已批准 policy 无法给出共同长度，则拒绝并行模式，改用两个 `single` 运行。

频率、amplitude、length 和 sigma 都是 concrete `PLSXY` 操作数。它们可能来自扫描轴或已批准 policy，但都不使用 `SET`。

`qubit_spectroscopy_v1` 禁止 SET、已校准 XY 宏、`X12`、`CZ`、`FSIM` 和读出指令。`run_circuits` 的通用 SET 能力保留给其他实验。

## 8. 参考帧与波形语义

每个目标的 `frequency` 是绝对物理载频。编译器计算：

```text
detuning_target = drive_frequency_target - reference_frequency_target
```

并在全局绝对采样时间上把失谐相位编译进对应 I/Q 数组。Stage 4.1 和 Stage 5.1 不得再次添加该相位。

每个目标独立消费自己的 `reference_frequency_authority`。并行线路不共享载频、不共享 RZ frame，也不能用其中一个目标的 reference frequency 计算另一个目标的相位。

idle flux 和电子学链转换仍由既有链路各消费一次。频谱轴不使用 `F012ZBIAS_MAPPER` 或 `G2ZBIAS_MAPPER`。

## 9. 初态与 primitive populations

v1 初态固定为 `lab_ground`，即已批准 idle Hamiltonian 的 dressed ground state。当前后端张量顺序为 `q1 / c / q2`，并返回：

| 字段 | 含义 |
| --- | --- |
| `population_000` | 两个比特均处于 dressed 0 计算态 |
| `population_100` | Q1 为 dressed 1、Q2 为 dressed 0 |
| `population_001` | Q1 为 dressed 0、Q2 为 dressed 1 |
| `population_101` | 两个比特均处于 dressed 1 |
| `leakage` | 离开上述四个 projector 的总 population |

它们是闭系统模型的 dressed projector population，不是 bare occupation、shot、IQ 或硬件测量概率。

## 10. 目标 observable

实验必须同时注册并保留两类目标响应量：

```text
Q1.target_excited_spectators_ground = population_100
Q2.target_excited_spectators_ground = population_001

Q1.target_excited_marginal = population_100 + population_101
Q2.target_excited_marginal = population_001 + population_101
```

对于其他 QAgent 名称，由 observable registry 根据它在 dressed projector bit ordering 中的位置聚合，实验代码不得写死名字或自行猜测 tensor index。

主分析量按执行模式冻结：

- `single` 使用 `target_excited_spectators_ground`。此时 spectator 被激发不是预期结果，`population_101`、spectator excited marginal 和 leakage 都是 hard diagnostics，不能并入目标峰。
- `parallel_lockstep` 使用 `target_excited_marginal`。此时另一个目标被正常激发，必须把 `population_101` 计入两个目标各自的边缘响应。

因此，单目标和并行模式共享 primitive observable 与证据，但不共享主峰聚合公式。point result 必须显式记录 `primary_observable_id`，分析器不得根据目标数临时猜测。

每个 point 必须保留：

```text
所有 primitive dressed populations
每个目标的 target_excited_population
computational_population
joint_excited_population = population_101
leakage
norm_error
```

所有边缘 population 均不按 `1 - leakage` 条件归一化。conditional quantity 只能作为诊断，并必须显式记录分母。

## 11. 并行模式的物理边界

同一次 Hamiltonian 演化中的双驱动能够真实包含耦合、AC Stark shift、串扰和条件频移，因此不能把它当作两次完全独立实验的简单计算合并。

`parallel_lockstep` 只沿二维频率空间的一条配对路径取样。它适合同时寻找两个候选峰，但不能独立给出完整二维串扰响应面。

默认流程为：

```text
并行粗扫
  -> 每个目标得到粗峰
并行细扫
  -> 每个目标得到细峰候选
单驱动局部确认扫描
  -> 分别在候选附近运行完整、奇数点的 single 小范围频率轴
并行等价性检查
  -> 通过：允许生成两个目标的独立候选
  -> 不通过：并行结果只作筛查，转为完整 single 细扫
```

并行结果不能仅凭 `population_101` 判断独立性。`population_101` 既可能来自两个独立激发的乘积，也可能包含耦合和双驱动效应。

## 12. 并行等价性 hard gate

`parallel_spectroscopy_equivalence_v1` 至少检查：

1. 每个目标的并行峰均不在扫描边界，具有唯一性和最低对比度。
2. 所有 point 的 leakage、norm error、求解器状态和数值收敛均通过 policy。
3. 单驱动确认使用相同 amplitude、length、sigma、绝对开始时刻和 reference authority，仅移除另一条驱动。
4. 每个目标的确认运行是独立的小范围 `single` 扫描；point count 必须是 policy 固定的奇数且不小于注册下限，频率轴以并行细峰为中心，使用显式范围和步长。
5. 确认扫描复用细扫的内部峰、对比度、唯一性、边界拒绝和峰值估计规则；任意点集合不能被称为 confirmation peak。
6. 每个目标的并行峰与单驱动确认峰之差不超过冻结的 `max_parallel_peak_shift_GHz`。
7. Q1-only 时 Q2 激发边缘、Q2-only 时 Q1 激发边缘不超过 `max_cross_excitation`。
8. 并行运行的 leakage 相对对应 single 确认运行的增量不超过 `max_parallel_leakage_delta`。
9. 粗扫、细扫和确认运行的候选值保持一致。

v1 不比较“由两个 single run 推导的 primitive population 乘积向量”。当前结果只有全局 leakage，不能唯一确定各子系统的局部泄漏，也就不能构造唯一的独立乘积参考。后续只有增加局部分辨 leakage observable 或注册额外参考 control circuit 后，才能引入该类 residual gate。

任何一项失败都产生明确 reason code，例如：

```text
parallel_peak_shift_exceeded
cross_excitation_exceeded
parallel_leakage_exceeded
parallel_peak_nonunique
```

失败时仍可发布数据集和诊断，但 `recommendation_eligible = false`。

## 13. 粗扫、细扫与分析

粗扫和细扫是两次独立、不可变运行。两种模式都使用显式奇数点、严格升序的频率数组。

每个目标独立执行峰值分析：

1. 读取该目标自己的 frequency axis 和 `target_excited_population`。
2. 选择离散最大值。
3. 边界峰、平坦峰、低对比度或多重近等峰均判无效。
4. 并列时优先选择最接近该目标获批准先验值的点，再选择较低频率。
5. 细扫可在注册条件通过时使用有界局部二次顶点估计；否则保留离散最大值。
6. 不执行 Lorentzian 线宽拟合，不输出 `T1/T2` 或统计测量误差。

在 `parallel_lockstep` 中，两条曲线共享 point index，但横轴是各自的频率数组。可视化和分析不得错误地把 Q1、Q2 放到同一个频率横轴上。

## 14. 校准候选和人工确认

一次并行运行最多生成两个相互独立的 target candidate：

```yaml
schema: qubit_reference_frequency_delta_v1
target: Q1
field: values.qagents.Q1.reference_frequency_authority
proposed_value:
  reference_frequency_GHz: ...
  frequency_source: accepted_simulation
source:
  coarse_run_id: ...
  refined_run_id: ...
  confirmation_run_id: ...
  parallel_equivalence_id: ...
claim:
  simulation_only: true
  hardware_measurement: false
  recommendation_eligible: true
```

用户可以分别接受或拒绝每个目标。配置写入器在一次新 snapshot revision 中原子应用所有被接受的 target delta；未接受目标保持父 revision 的值。

`run_circuits`、dataset builder 和分析器均不能直接写配置。

## 15. 证据链

```text
canonical experiment request
  -> authority-bound point expansion table
  -> concrete QCIS circuits
  -> run_circuits circuit execution receipts
  -> primitive dressed populations
  -> immutable point dataset
  -> per-target coarse/refined analyses
  -> optional single-drive confirmation dataset
  -> parallel equivalence artifact
  -> per-target calibration candidates
  -> optional human acceptance artifact
  -> new calibration snapshot revision
```

一个双目标 point 只有一条 QCIS circuit 和一个模型 evidence，但 dataset 中包含两个绑定到同一 circuit receipt 的 target result。不得把它伪装成两个独立演化。

## 16. 可视化

标准可视化至少包含：

- 每个目标独立的 frequency vs 主响应曲线，并标明使用 spectator-ground projector 还是 excited marginal；
- 粗扫峰、细扫峰、先验频率和最终候选标记；
- 并行曲线与单驱动确认点的叠加比较；
- `population_000/100/001/101`、joint excitation 和 leakage 诊断；
- 并行等价性各 hard gate 的通过/失败状态。

图表必须注明 `model-derived closed-system population`，不得标为硬件测量结果。

## 17. 失败与恢复

- 任一 axis、目标 capability、pulse policy、共同长度或 QCIS 编译检查失败时，整批在第一次演化前拒绝。
- 双目标 point 是一个 circuit；任一通道参数无效时不能发布“半个成功 point”。
- 运行期失败沿用 `run_circuits` 的逐点不可变语义；之前成功 point 可以保留，但完整 dataset 不得发布为成功。
- 正式恢复由 Runtime 0.3 batch manifest 创建新运行身份并显式绑定可复用 point。
- 当前 smoke facade 单次最多 64 条 circuit；生产规模必须由新的 Runtime 和后端 authority 批准。

## 18. 验证矩阵

### 18.1 请求与展开

- 任意合法单目标 QAgent，而不是只测 Q1；
- Q1 single、Q2 single、双目标 lockstep；
- 目标重复、非 qubit、缺少 XY 通道、缺少 reference authority；
- lockstep 轴不等长、非升序、重复、非有限、错误单位；
- 两个各含 2 个值的 lockstep axes 精确展开成 2 个 point，禁止落入旧 Runtime 的 4 点笛卡尔积；
- 64 点接受、65 点在当前 smoke 入口拒绝；
- point table、hash 和 circuit ID 确定性。
- 注入两个完整 hash 不同但前 32 位相同的 synthetic point，验证短 ID 碰撞 fail closed；
- 已有输出目录占用同一 circuit ID 时在首次演化前拒绝，禁止覆盖或自动改名。

### 18.2 QCIS 时序

- 双 PLSXY 的 `actual_start_sample`、length 和 end 完全相同；
- 指令顺序固定但两个物理通道波形均在同一窗口；
- 不同 absolute start 或不同 length 被实验 builder 拒绝；
- 当前 smoke 下验证 `tStart + shared_length <= 64`，越界在执行前拒绝；
- `I/B` 不能移动绝对 PLSXY；
- 每个目标使用自己的 frequency/reference authority 和相位。

### 18.3 Observable

- 单目标验证 target-excited/spectators-ground projector；并行模式验证 target marginal 聚合；
- 并行时 Q1 主响应包含 `population_101`，Q2 同理；
- primitive populations、computational population 和 leakage 归一关系；
- projector ordering 或目标映射被篡改时拒绝；
- 单目标的 `population_101` 与 spectator excitation 必须作为 hard diagnostic；
- conditional diagnostic 不得替换主 observable。

### 18.4 分析与物理边界

- 内部峰、边界峰、并列峰、低对比度、高 leakage、多峰；
- parallel/single 峰位一致与不一致；
- cross excitation、parallel leakage delta 和条件频移超限；
- 任一 hard gate 失败时禁止 recommendation；
- 两个 target candidate 可分别接受或拒绝。

### 18.5 端到端

- 已知 Q1 频率的 single 恢复；
- 已知 Q2 频率的 single 恢复；
- 两路无串扰合成模型的 parallel lockstep 同时恢复两个频率；
- 引入 AC Stark 或串扰后，parallel equivalence gate 拒绝推荐；
- replay 产生相同 point table、QCIS、dataset、analysis 和 candidate identity。

## 19. 当前限制与实施顺序

当前 `run_circuits` 仍使用 `bounded_smoke_only` Stage 7.1 entrance、`lab_ground` 初态和最多 64 个逻辑样本。它可以验证 circuit builder、并行时序、observable 和证据链，但不能直接授予生产校准资格。

建议实施顺序：

1. 实现通用请求 validator 和 capability-based target resolver。
2. 实现 single/parallel-lockstep point expansion 与 circuit builder。
3. 实现通用 observable adapter 和不可变 dataset。
4. 实现每目标粗扫/细扫分析与可视化。
5. 实现 single confirmation builder 和 parallel equivalence gate。
6. 完成 bounded pilot 与独立代码、测试、物理审核。
7. 冻结数值 policy，建立生产 Runtime 0.3 和后端准入 authority。
8. 生成 target-specific candidates，等待用户确认后更新配置 revision。

## 20. 需要确认的设计决定

1. 基础双目标扫描采用按索引配对的 `parallel_lockstep`，不在 v1 中做隐式笛卡尔积。
2. 双目标脉冲使用相同绝对 `tStart=0` 和相同 length；不使用 `B` 对齐。
3. 单目标使用 target-excited/spectators-ground projector，并行模式使用 target excited marginal；二者共享 primitive 数据但主峰公式不同。
4. 并行粗扫和细扫允许产生候选，但必须通过单驱动确认和 parallel equivalence gate 后才具有 recommendation 资格。
5. 一次并行运行产生每目标独立候选，用户可以分别接受或拒绝。

## 21. 已实现接口

当前实现位于 `sqvm.calibration.spectroscopy`，公共入口为：

```python
from sqvm import (
    SpectroscopyAxis,
    SpectroscopyMode,
    SpectroscopyPulsePolicy,
    SpectroscopyRequest,
    analyze_qubit_spectroscopy,
    run_qubit_spectroscopy,
)

request = SpectroscopyRequest(
    execution_mode=SpectroscopyMode.PARALLEL_LOCKSTEP,
    run_phase="coarse",
    targets=("Q1", "Q2"),
    axes=(
        SpectroscopyAxis("Q1", (4.95, 5.00, 5.05)),
        SpectroscopyAxis("Q2", (5.15, 5.20, 5.25)),
    ),
    pulse_policies=(
        SpectroscopyPulsePolicy("Q1", 5, 0.001, 2.0),
        SpectroscopyPulsePolicy("Q2", 5, 0.001, 2.0),
    ),
)

dataset = run_qubit_spectroscopy(
    request,
    context,
    output_root=repository_root / "o_00000001",
    repository_root=repository_root,
)
analysis = analyze_qubit_spectroscopy(dataset, min_contrast=0.01)
```

已实现并验证：

- capability-based 任意 QAgent 名称到当前 `q1/q2` slot 的严格映射；
- single 与双目标 `parallel_lockstep` 请求校验；
- 全批次预编译、确定性 point hash 和 `sp_<32 hex>` circuit ID；
- gaussian `PLSXY` concrete QCIS 生成，不使用 `SET`；
- `readout_qubit` 自动选择，以及 single/parallel 两种主 observable 聚合；
- primitive dressed populations、leakage、norm error 和 circuit receipt 保留；
- 边界峰、非唯一峰、低对比度拒绝，以及有界局部二次峰值估计；
- 一点真实 QuTiP bounded smoke 链路。

Windows 未开启长路径支持时，`output_root` 应使用仓库内的短运行目录。Stage 7.1 同时要求输出目录位于 `repository_root` 内；过长的实验名应保存在 manifest 字段中，而不是目录名中。

单次扫描分析只可能把 `peak_quality_eligible` 置真，`recommendation_eligible` 固定为假，不能直接获得配置写入资格。

## 22. 已实现完整校准流程

完整流程位于 `sqvm.calibration.spectroscopy_workflow`。粗扫、细扫和 single confirmation 没有各自的执行器，全部通过 `run_qubit_spectroscopy` 调用同一个 `run_circuits`：

```python
from sqvm import (
    SpectroscopyCalibrationPolicy,
    SpectroscopyCalibrationRequest,
    decide_qubit_spectroscopy_calibration,
    run_qubit_spectroscopy_calibration,
)

calibration_request = SpectroscopyCalibrationRequest(
    coarse_request=request,
    policy=SpectroscopyCalibrationPolicy(
        fine_span_GHz=0.02,
        fine_points=9,
        confirmation_span_GHz=0.01,
        confirmation_points=7,
        min_contrast=0.05,
        max_leakage=0.02,
        max_norm_error=1e-8,
        max_coarse_refined_shift_GHz=0.05,
        max_parallel_peak_shift_GHz=0.005,
        max_cross_excitation=0.02,
        max_parallel_leakage_delta=0.01,
    ),
)

run = run_qubit_spectroscopy_calibration(
    calibration_request,
    context,
    parent_calibration_path="configs/calibration/platform_uncalibrated_v1.json",
    output_root="output/calibration/spectroscopy_run_001",
    repository_root=repository_root,
)
```

调用顺序冻结为：

```text
single:
  coarse -> refined -> gates -> candidate

parallel_lockstep:
  parallel coarse -> parallel refined
  -> target 0 single confirmation
  -> target 1 single confirmation
  -> parallel equivalence gates -> per-target candidates
```

refined axis 以 coarse 的有效峰值为中心，由 `fine_span_GHz` 和奇数 `fine_points` 生成。parallel 的每个 confirmation axis 以该目标 refined 峰值为中心，由 `confirmation_span_GHz` 和奇数 `confirmation_points` 生成。脉冲 amplitude、length 和 sigma 始终复用 coarse request 的同一 pulse policy。

工作流原子发布：

```text
workflow.json
receipt.json
spectroscopy.png
datasets/coarse.json
datasets/refined.json
datasets/confirmation_<target>.json  # parallel only
c/、f/、s0/、s1/                     # run_circuits 原始证据
```

工作流级 `recommendation_eligible` 只在以下条件全部通过时为真：coarse/refined 峰质量、leakage、norm error、粗细峰一致性，以及 parallel 模式的 single confirmation 峰质量、峰位偏移、cross excitation 和 parallel leakage delta。任一 gate 失败仍发布数据、图表和诊断，但所有候选均不可接受。

用户确认通过独立事务完成：

```python
decision = decide_qubit_spectroscopy_calibration(
    run.root,
    parent_calibration_path="configs/calibration/platform_uncalibrated_v1.json",
    output_root="output/calibration/decision_001",
    context=context,
    decision="accept",
    actor_id="project.manager",
    reason="accept verified model spectroscopy result",
    confirmation_phrase=(
        f"ACCEPT SIMULATION CALIBRATION {run.recommendation_id}"
    ),
    accepted_targets=("Q1", "Q2"),
    repository_root=repository_root,
)
```

`accept` 原子发布 `decision.json`、`calibration.json` 和 `receipt.json`；`reject` 只发布 decision 和 receipt。双目标候选可以只接受其中一个。新参考频率 authority 使用 `frequency_source=accepted_simulation`、递增 revision、新 `setting_hash`，未接受目标保持父值。

`context_with_spectroscopy_calibration(context, decision.calibration_path)` 会验证 decision/calibration/receipt 三方哈希绑定，并返回新的不可变执行 context；不会修改原 context。

当前仍属于 bounded pilot：没有硬件 measurement/shot/IQ claim，没有生产 Runtime catalog、并发 lineage-head 锁、崩溃恢复和完整 source/environment snapshot。`verify_qubit_spectroscopy_calibration` 当前验证 canonical dataset、analysis/candidate/gate 派生绑定和 receipt 哈希，不替代未来生产 authority 的独立 QuTiP 全量重放。
