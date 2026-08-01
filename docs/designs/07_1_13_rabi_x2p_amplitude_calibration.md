# Rabi / X2P 幅度校准实验详设

## 1. 文档状态

- 实验 ID：`qubit_rabi_x2p_amplitude_v1`
- 工作流 ID：`qubit_rabi_x2p_amplitude_scan_v1`
- 首版 artifact version：`0.1`
- 依赖：QCIS v0.3、Runtime 0.3、Stage 4.1 电子学链、Stage 5.1 QuTiP 演化、通用候选参数协议
- 范围：Rabi 首瓣扫描，用两个连续 `X2P` 校准 active XY2 setting 的幅度

本文档冻结实验边界、坐标系、QCIS、数据、分析、候选、发布和恢复合同。分析阈值的具体数值必须由
首个可行性实验产生并写入版本化 analysis policy，不在代码中写入无依据的临时常量。

## 2. 目的

在已经完成比特频率校准的 Active PlatformConfiguration 上，固定 `X2P` 的脉宽、波形类型、DRAG、
相位偏置和驱动频率，仅扫描 active XY2 setting 的 `amplitude_GHz`。每个扫描点执行两个连续
`X2P`，通过末态 `P1` 的第一个 Rabi 峰得到新的 `X2P` 幅度候选。

本实验必须满足：

1. 校准线路使用 QCIS 门指令，不直接生成 `PLSXY`；
2. 扫描值通过 circuit 内的 `SET` preamble 覆盖配置，绝不修改父配置；
3. 两个 `X2P` 使用同一个 effective setting、相同幅度和相同旋转系逻辑相位；
4. 两个门处于不同绝对时间，实验室系载波相位按全局时间连续推进；
5. 电子学链对完整线路只处理一次；
6. QuTiP 继续消费旋转参考系中的有效复数 IQ，不能重复乘入实验室系载波；
7. 实验只生成候选值，配置更新必须由用户确认并通过通用配置事务完成。

## 3. 非目标

首版不实现：

- 单个 `PLSXY` 的 Rabi 扫描；
- `X` 或 `active_xy_setting` 的幅度校准；
- 脉宽扫描、幅度与脉宽二维扫描；
- DRAG、频率、相位偏置或 X/Y 轴正交误差校准；
- Q1、Q2 同时驱动的并行 Rabi；
- 重复门误差放大或 randomized benchmarking；
- 自动接受候选、自动修改当前配置；
- 把实验室系高频预览波形再次送入旋转系 QuTiP Hamiltonian。

## 4. 前置条件

开始创建 Runtime 批次前必须完成所有检查。

### 4.1 Active 配置

- `device_id` 恰好有一个 Active PlatformConfiguration；
- Active 配置能够解析为完整 `CircuitExecutionContext`；
- 父配置 content SHA、authority context SHA 和当前 revision 被绑定到实验 request；
- target 是 capability registry 中支持 XY drive 的 qubit QAgent，不按 `Q1`、`Q2` 写死实现。

### 4.2 频率前置条件

目标比特必须存在有效的：

```text
calibration_values.qagents.<target>.reference_frequency_authority
```

其 `reference_frequency_GHz`、revision、setting hash 和 calibration run binding 必须通过现有配置解析器。
Rabi 的工作流顺序是 spectroscopy -> Rabi，因此 production recommendation eligibility 要求
`frequency_source=accepted_simulation`。bootstrap seed 可以用于明确标记的开发 fixture，但不得产生可接受候选。

### 4.3 XY2 setting 前置条件

从：

```text
calibration_values.gate_configuration.<target>.active_xy2_setting
```

解析实际 setting ID，并要求对应 waveform setting：

- status 为 accepted；
- target 与请求 target 一致；
- gate type 为 `XY2`、transition 为 `01`；
- `length_samples` 为正整数；
- `amplitude_GHz` 为有限正数；
- waveform class、shape 参数、`dragAlpha_samples`、`phase_offset_rad` 满足 QCIS v0.3；
- setting hash 和 revision 有效。

完整逻辑线路长度为 `2 * length_samples`，必须落在当前 calibration execution authority 的
`max_logical_sample_count` 和 `max_logical_duration_ns` 内；电子学链处理后的长度还必须落在
`max_effective_sample_count` 和 `max_effective_duration_ns` 内。任何超限都在进入 QuTiP 前拒绝，不能截断脉冲。

允许扫描的 SET path 必须已存在于 `context.settable_paths`：

```text
<target>.setting.active_xy2_setting.amplitude_GHz
```

`xy_pi_impl` 不改变本实验语义。本实验始终校准两个显式 `X2P`，不使用 `X` 宏。

## 5. 用户 API

首版公开接口：

```python
def run_rabi(
    target: str,
    amplitude_range_GHz: Sequence[float],
    amplitude_step_GHz: float,
    *,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    timeout_s: float = 600.0,
    batch_deadline_s: float = 3600.0,
    operation_id: str | None = None,
    cancellation_token: CancellationToken | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> RabiRun:
    ...
```

取消接口：

```python
cancel_rabi(operation_id, *, device_id="demo_2q1c2r", ...)
```

首版要求显式提供扫描范围和步进，避免将某个模拟器 fixture 的幅度尺度作为设备通用默认值。
`output_root`、repository、timeout、deadline 和 operation ID 行为与 `run_spectroscopy` 一致。

### 5.1 幅度轴

`amplitude_range_GHz=(start, stop)`，首瓣模式要求：

- `start == 0.0`；
- `stop > 0.0`；
- step 为有限正数；
- `(stop-start)/step` 必须是整数，端点全部包含；
- 使用 Decimal 构造网格后再转换为 canonical float；
- 点数不少于 analysis policy 的最小点数且不超过 Runtime 0.3 的 64 点上限；
- 幅度按严格递增顺序排列且不允许重复。

不区分“粗扫”和“细扫”。不同范围与步进调用同一个 API，产生一个独立、不可变实验。

## 6. 请求模型

内部不可变模型建议为：

```text
RabiAmplitudeAxis
  target
  amplitudes_GHz

RabiRequest
  experiment_id
  target
  axis
  setting_selector = active_xy2_setting
  scan_parameter = amplitude_GHz
  gate_sequence = [X2P, X2P]
  analysis_policy_id
  max_points
```

request identity 必须绑定：

- target 和完整 amplitude axis；
- 固定的两门序列；
- 父配置和解析后的 active setting；
- reference frequency authority；
- QCIS/编译器/电子学链/QuTiP authority；
- readout group、execution profile、point timeout、batch deadline；
- analysis policy ID 和内容 SHA。

## 7. QCIS 线路生成

每个 amplitude 点只生成以下三行：

```qcis
SET <target> setting.active_xy2_setting.amplitude_GHz <canonical_amp>
X2P <target>
X2P <target>
```

示例：

```qcis
SET Q1 setting.active_xy2_setting.amplitude_GHz 0.075
X2P Q1
X2P Q1
```

规则：

- `SET` 必须位于 preamble；
- 一个 circuit 只能设置一次该路径；
- 不允许同时覆盖 length、frequency、phase、DRAG 或 waveform class；
- executable QCIS 删除 SET 后必须精确为两个 `X2P`；
- overlay receipt 同时绑定 base setting hash 和 effective setting hash；
- 两个 `X2P` 必须解析到同一个 effective setting hash；
- circuit ID 使用 point index，例如 `rabi_q1_0000`，不将浮点文本作为 ID；
- circuit source、overlay 和 point coordinate 全部进入 Runtime request SHA。

Runtime 0.3 在创建第一个物理点前预编译全部 circuit，因此任一幅度点不能编译时，整个实验不得开始。

## 8. 时序与坐标系

### 8.1 逻辑时序

两个 `X2P` 使用同一 XY lane cursor：

```text
first.start_sample  = current_xy_cursor
first.end_sample    = first.start_sample + length_samples
second.start_sample = first.end_sample
second.end_sample   = second.start_sample + length_samples
```

首版不在两个门之间插入 `I`、`B` 或任何 gap。电子学 padding 只能发生在完整逻辑 schedule 的外部，
不能改变两个门的逻辑连续性。

### 8.2 旋转参考系

设第 j 个脉冲第 k 个采样中心为：

```text
t_jk = (start_sample_j + k + 0.5) * dt_ns
```

逻辑 X 轴相位：

```text
phi_total_j = gate_phase_X + setting.phase_offset_rad + virtual_frame_phase
```

两个 `X2P` 之间没有 RZ 或 composite phase correction，因此：

```text
phi_total_1 == phi_total_2
```

QCIS v0.3 旋转系复包络遵循现有规则：

```text
theta_rot_jk = wrap(
    phi_total_j
    + 2*pi*(f_drive_GHz-f_ref_GHz)*t_jk
)

epsilon_logical_jk = base_envelope_jk * exp(-i*theta_rot_jk)
```

在共振条件 `f_drive_GHz == f_ref_GHz` 下，两段逻辑 IQ 可以相同。这是同一旋转轴的正确结果，
不表示实验室系载波在两个局部脉冲起点被重置。

### 8.3 实验室系真实载波

用于检查和显示的实验室系相位定义为：

```text
theta_lab_jk = wrap(phi_total_j + 2*pi*f_drive_GHz*t_jk)
drive_lab_jk = Re(base_envelope_jk * exp(-i*theta_lab_jk))
```

两个脉冲第一采样点的未折叠相位推进为：

```text
delta_theta_lab_unwrapped
  = 2*pi*f_drive_GHz*(second.start_sample-first.start_sample)*dt_ns
```

折叠到 `[-pi, pi]` 后可能非零，也可能因脉宽恰好包含整数载波周期而等于零。验收条件是满足绝对时间公式，
不是强制两个 wrapped phase 数值不相等。

### 8.4 电子学链

电子学链必须对包含两个门的完整 I/Q schedule 调用一次：

```text
full logical IQ
  -> DAC/滤波/延迟/串扰等电子学链
  -> full effective IQ on one global time axis
```

禁止分别处理两个脉冲后拼接。否则会错误重置滤波器状态、边界响应和载波时间参考。

电子学链后的实验室系预览从 effective IQ 和全局 time centers 派生：

```text
drive_lab_effective(t)
  = Re(epsilon_effective(t) * exp(-i*2*pi*f_ref_GHz*t))
```

该预览只用于相位审计与可视化，不是新的 QuTiP 输入。

### 8.5 QuTiP

Stage 5.1 继续使用：

- effective complex IQ `epsilon_q1/epsilon_q2`；
- 全局 `frame_reference_frequency_GHz`；
- 同一全局时间轴上的 interaction-picture unitary `u(t)`。

不得把 `drive_lab_effective` 再乘入 Hamiltonian，否则会把 reference carrier 计算两次。

## 9. 相位审计

每个编译点必须从两个 drive event 生成 `RabiPhaseAudit`。其中逻辑事件和绝对相位公式在
Runtime 预留输出前完成静态审计；完整 effective schedule 的连续时间轴由通用 Stage 4.1 verifier 在
control publication 后、QuTiP evolution 前验证。Rabi 不向 Runtime 注入实验专用可调用 hook。

```text
point_index
setting_id
effective_setting_hash
length_samples
dt_ns
first_start_sample
second_start_sample
phase_total_rad
f_drive_GHz
f_ref_GHz
detuning_GHz
rotating_first_sample_phase_rad[2]
lab_first_sample_phase_rad[2]
lab_phase_advance_unwrapped_rad
lab_phase_advance_wrapped_rad
```

检查项：

1. 恰好存在两个 transition 01 drive event；
2. source instruction 分别绑定两个 `X2P`；
3. 两个事件 setting ID/effective hash 相同；
4. amplitude overlay 相同；
5. 第二事件紧接第一事件；
6. `phase_total_rad` 相同；
7. drive/reference/detuning 相同；
8. logical IQ 满足 absolute detuning phase rule；
9. lab phase 满足 absolute drive phase rule；
10. effective schedule 使用一条连续全局时间轴。

任一项失败，使用稳定错误 `rabi_phase_audit_failed`，不得执行 QuTiP 或生成候选。

## 10. Runtime 0.3 执行

Rabi adapter 将全部 circuit 交给 `run_circuit_batch`：

```text
build request
  -> build all QCISCircuit
  -> precompile all points
  -> phase-audit all compilations
  -> run_circuit_batch
  -> one run_circuits call per uncommitted point
```

参数：

- batch ID 等于 operation ID；
- experiment request 使用 Rabi request payload；
- `readout_qubit=[[target]]`；
- execution profile 为 `CALIBRATION_SCAN`；
- point timeout 和 batch deadline 来自公开 API；
- coordinator 为 experiment collection 下的 `.runtime-v03`。

完成、续跑、deadline、取消、锁、孤儿证据收养和篡改检查完全复用 Runtime 0.3，不在 Rabi 模块复制实现。

## 11. 原始数据

`RabiDataset` 采用列式主数据和点级证据绑定：

```json
{
  "experiment_id": "qubit_rabi_x2p_amplitude_v1",
  "target": "Q1",
  "axis": {
    "name": "amplitude_GHz",
    "unit": "GHz",
    "values": [0.0, 0.005, 0.01]
  },
  "series": {
    "Q1": {
      "P0": [1.0, 0.98, 0.91],
      "P1": [0.0, 0.02, 0.08],
      "leakage": [0.0, 0.0, 0.001],
      "norm_error": [0.0, 0.0, 0.0]
    }
  },
  "points": [
    {
      "point_index": 0,
      "circuit_id": "rabi_q1_0000",
      "circuit_sha256": "...",
      "overlay_sha256": "...",
      "phase_audit": {}
    }
  ]
}
```

要求：

- amplitude、P0、P1、leakage、norm_error 长度完全一致；
- 数据有限且概率满足现有 readout 合同；
- point index 连续，circuit 顺序与 Runtime receipt 一致；
- dataset SHA 绑定 canonical JSON；
- dataset 不复制底层大数组，只引用 Runtime 证据图；
- 实验室系波形预览按需从受验证的 effective IQ 派生，不写入 dataset 大数组。

## 12. 分析

### 12.1 拟合对象

只用目标比特 `P1(amplitude)` 拟合；P0、leakage 和 norm error 作为质量门。

两个连续 `X2P` 的首版模型为：

```text
P1(A) = offset + contrast * sin^2(pi*A/(2*A_x2p))
```

参数：

- `offset`：零驱动下的有限基线；
- `contrast`：首瓣对比度；
- `A_x2p`：模型第一个正峰位置，也是候选幅度。

模型不加入自由 phase 参数。零幅度不产生 XY rotation，允许自由 phase 会使首瓣编号产生不必要的歧义。
明显不符合该模型的数据应分析失败或 recommendation ineligible，而不是用更多自由参数掩盖。

### 12.2 确定性算法

1. 验证 dataset SHA 和所有列；
2. 在正幅度数据中查找第一个被左右邻点包围的离散局部最大值；
3. 用三个邻点进行有界二次插值，得到 deterministic initial guess；
4. 使用固定版本、固定 method、固定 tolerance 和固定最大迭代数的 bounded least-squares；
5. `offset`、`contrast` 和 `A_x2p` 使用 analysis policy 给出的物理边界；
6. `A_x2p` 必须位于离散首峰左右邻点形成的 bracket 内；
7. 生成实测点上的 fitted values 和用于 Web 的有界 dense fit curve；
8. 记录算法版本、输入 SHA、收敛状态、迭代数和残差。

### 12.3 质量指标

至少输出：

```text
fit_converged
offset
contrast
x2p_amplitude_GHz
rmse
normalized_rmse
r_squared
first_peak_index
peak_bracket_GHz
candidate_distance_to_edge_steps
candidate_leakage
max_norm_error
phase_audit_passed
```

### 12.4 Analysis policy

版本化 policy 至少包含：

```text
minimum_point_count
minimum_points_before_peak
minimum_points_after_peak
minimum_contrast
minimum_r_squared
maximum_normalized_rmse
maximum_candidate_leakage
maximum_norm_error
minimum_edge_guard_steps
fit_parameter_bounds
```

policy 内容 SHA 进入 request 和 analysis。首个 pilot 完成前允许生成分析诊断，但
`recommendation_eligible=false`；批准 policy 后才开放候选接受。

## 13. 候选参数

使用通用 `calibration_candidate_v1`：

```text
candidate_type = xy2_amplitude
calibration_subjects = [target]
parameter_path = calibration_values.waveform_registry.settings.<setting_id>.amplitude_GHz
current_value = parent snapshot 中的 amplitude_GHz
proposed_value = fitted A_x2p
unit = GHz
configuration_resource = waveform_setting/<setting_id>
source_dataset_sha256s = [dataset_sha256]
```

候选 eligibility 要求：

- 所有配置、QCIS、phase、Runtime、dataset 和 fit gates 通过；
- 使用 accepted spectroscopy frequency；
- 候选位于首峰 bracket 和扫描区间内部；
- analysis policy 的对比度、残差、leakage、norm 阈值全部通过；
- parent configuration 仍为候选绑定的内容。

实验完成不修改配置。用户在 Notebook 或 Web 确认后调用现有通用候选更新事务。事务成功后：

- 生成新的 setting revision/hash；
- 写入 calibration run ID；
- 原子更新当前配置并立即对后续新实验生效；
- 原实验仍永久绑定旧 parent snapshot；
- parent 已变化时返回 stale candidate 冲突，不静默覆盖。

通用候选入口 `_verified_candidate_workflow` 必须增加
`qubit_rabi_x2p_amplitude_scan_v1 -> verify_rabi_scan` 的显式分派。未知 workflow 继续 fail closed，
不得仅因 JSON 中存在 `candidates` 字段就允许更新配置。

## 14. 发布文件

沿用 spectroscopy 的不可变发布模式：

```text
output/experiments/qubit_rabi_<operation-id-without-dashes>/
  workflow.json
  dataset.json
  manifest.json
  verification_report.json
  receipt.json
  execution/
    batch/
      request.json
      head.json
      points/*.json
    circuit_execution/<circuit-id>/...
    <circuit-id>/...
```

`workflow.json` 包含：

- request 和 parent configuration binding；
- active setting/reference frequency binding；
- runtime batch binding；
- analysis、quality metrics 和 gates；
- normalized calibration candidate；
- recommendation ID/eligibility；
- archive eligibility。

reader verifier 必须通过有界 `EvidenceReader` 校验完整证据闭包，不解压后信任路径。

## 15. Web

Web 不启动实验，只负责读取结果和确认候选。

Rabi plot 使用现有统一 plot protocol：

- object：目标 QAgent；
- metrics：P0、P1、leakage、P1 fit；
- x：`Drive amplitude (GHz)`；
- y：Probability；
- group：raw / fit；
- candidate：候选幅度竖线；
- 支持对象/指标选择、点选、0.3 秒 hover 坐标和 source point lookup。

详情页显示：

- 扫描范围、步进、父配置、active setting；
- 两个 `X2P` 的 QCIS source；
- fit 参数和质量门；
- phase audit 摘要，包括两个 start sample 和 lab phase advance；
- 候选 current/proposed value；
- 统一“确认更新”入口；
- 归档、回收站和删除能力沿用存储模块。

Web 首屏和目录只读取投影与小型 metadata，不递归读取 execution evidence；点开 phase/evidence 详情时再按需加载。

## 16. Notebook 用户流程

新增 `user/02_x2p_rabi_calibration.ipynb`，固定四段流程：

```python
# 1. 导入
from uuid import uuid4
from sqvm.calibration import (
    run_rabi,
    apply_calibration_candidates_to_current_configuration,
)

# 2. 设置扫描参数
OPERATION_ID = str(uuid4())

# 3. 运行实验
run = run_rabi(
    target="Q1",
    amplitude_range_GHz=(0.0, 0.2),
    amplitude_step_GHz=0.005,
    operation_id=OPERATION_ID,
)

# 4. 用户检查后更新
update = apply_calibration_candidates_to_current_configuration(
    run,
    confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
    candidate_ids=[run.candidates["Q1"]["candidate_id"]],
    actor_id="project.manager",
)
```

Notebook 必须保留 operation ID。同参数重试使用相同 ID，Runtime 只执行未提交点；已发布实验零执行重开。

## 17. 状态、恢复与取消

实验级行为与 spectroscopy 一致：

1. 无 staging/target：创建 staging 并执行；
2. staging 存在且 request 一致：恢复批次，只执行 tail；
3. target 已完成：完整验证并零执行返回；
4. 同 operation ID 但幅度轴、配置、policy 或 execution contract 不同：409 idempotency conflict；
5. point deadline：保留已提交 prefix，下次同 ID 恢复；
6. cancellation：点间协作取消，同 ID 进入 cancelled 终态；
7. publication failure：保留 recovery evidence，不删除已完成物理点；
8. evidence/hash/phase audit 漂移：fail closed，要求人工恢复。

## 18. 稳定错误分类

Rabi 层至少提供：

```text
rabi_request_invalid
rabi_axis_invalid
rabi_target_unsupported
rabi_reference_frequency_unqualified
rabi_xy2_setting_invalid
rabi_set_path_unavailable
rabi_compilation_invalid
rabi_phase_audit_failed
rabi_result_invalid
rabi_fit_not_converged
rabi_first_peak_not_bracketed
rabi_candidate_ineligible
rabi_publication_failed
rabi_recovery_required
```

Runtime 0.3 的 `batch_busy`、`batch_deadline_exceeded`、`batch_cancelled`、
`batch_idempotency_conflict` 和 `batch_recovery_required` 保持原始语义，不重新包装成不可识别文本。

## 19. 测试矩阵

### 19.1 QCIS 与 SET

- 每点 source 精确为 SET + X2P + X2P；
- SET 只覆盖 amplitude，父 authorities 不变；
- 两门使用同 effective setting hash；
- SET 不在 preamble、重复 SET、非 allowlist path 全部拒绝；
- 全批预编译失败时零物理点执行。

### 19.2 相位

- 两个门 start/end 连续；
- rotating-frame phase 相同；
- 使用非整载波周期 fixture 验证 wrapped lab phase 不同且公式正确；
- 使用整载波周期 fixture 验证 wrapped phase 可以相同但 unwrapped advance 正确；
- detuned fixture 验证 IQ 第二段包含 absolute detuning phase；
- 人为按局部时间重置第二段相位时，phase audit 必须失败；
- 电子学链一次处理与错误的分段处理产生不同边界结果，只有前者通过；
- lab preview 与 rotating-frame QuTiP 输入之间的 frame transform 可逆；
- 确认 QuTiP 没有重复乘入 carrier。

### 19.3 分析

- deterministic fake sine fixture 恢复已知 `A_x2p`；
- 零对比度、无峰、峰在边界、多峰歧义、NaN、长度不一致全部拒绝或 ineligible；
- leakage/norm/残差门逐项覆盖；
- 相同 dataset 在 Windows/Linux 给出相同 canonical analysis bytes；
- 候选 parameter path 指向实际 active setting ID。

### 19.4 Runtime 与发布

- 成功扫描、顺序和零执行重放；
- 第 N 点失败后只恢复 tail；
- deadline、token cancel、external cancel；
- 同 ID 不同 axis/config/policy 冲突；
- batch/head/point/evidence 篡改 fail closed；
- staging 发布失败与恢复；
- reader/path verifier 对同一篡改都拒绝；
- archive、trash、purge 和 catalog 列表兼容。

### 19.5 Web 与 Notebook

- amplitude/P0/P1/leakage 为独立 list；
- object/metric filter、point lookup、hover 坐标、candidate marker；
- Web 确认更新成功后当前配置立即变化；
- stale candidate 和重复确认返回稳定冲突；
- Notebook 从项目根目录直接 import、运行 fake fixture、查看候选并更新配置；
- 一个受控的真实 QuTiP 小网格实验必须完整跑完并通过证据验证，不能只 mock 到 API 返回。

### 19.6 全库门禁

- contract；
- integration；
- physics_slow；
- hosted evidence；
- strict marker collection；
- clean checkout CI；
- Windows 与 Linux fixture integrity。

## 20. 实现顺序

1. 冻结本文档和 Rabi request/dataset/analysis fixture；
2. 为两个连续 `X2P` 增加编译相位审计与跨平台向量；
3. 实现纯 Rabi planner、dataset builder 和 deterministic analyzer；
4. 接入 Runtime 0.3 和 operation ID 恢复；
5. 实现 workflow publication、reader verifier、候选验证分派和通用候选；
6. 增加 `run_rabi`、`cancel_rabi` 和 root lazy exports；
7. 接入 Web plot/read model、candidate confirmation 和 storage catalog；
8. 增加用户 Notebook 和 README；
9. 跑 fake、真实 QuTiP、故障注入、全量回归和 CI；
10. 通过 PR 合并到 dev 后，再开始 Ramsey 设计。

## 21. 冻结结论

首版 Rabi 的唯一校准对象是 active XY2 setting 的 `amplitude_GHz`。每个点使用：

```qcis
SET <target> setting.active_xy2_setting.amplitude_GHz <amp>
X2P <target>
X2P <target>
```

两个门在旋转系中保持同一 X 轴；实验室系载波不在门边界重置，而是按绝对时间连续推进。电子学链处理
完整 schedule 一次，QuTiP 使用全局旋转系有效 IQ。拟合得到首个正峰的 `A_x2p` 后，只生成统一候选，
由用户确认后通过配置事务使其立即成为当前配置。
