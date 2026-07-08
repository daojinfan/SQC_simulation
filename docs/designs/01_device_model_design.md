# 阶段 1 详细设计：器件配置与参数模型

## 1. 设计目标

阶段 1 的目标是定义并验证一台最小虚拟超导量子计算机的物理配置。

目标器件：

```text
2q1c2r
```

含义：

```text
q1, q2 : 两个超导量子比特
c      : 一个可调耦合器
r1, r2 : 两个读出谐振腔
```

本阶段只解决“器件是什么”和“参数是否自洽”。暂不构建哈密顿量，也不做 QuTiP 演化。

## 2. 主要参考

### 2.1 李少炜博士论文

本阶段主要参考该论文中关于以下主题的背景：

```text
超导量子比特
高精度控制所需的基础参数
双比特门和耦合器背景
实验校准需要暴露的器件参数
```

阶段 1 不直接从论文中固化具体哈密顿量公式或校准算法。论文在本阶段的作用是帮助确定配置中应保留哪些物理对象和参数。

### 2.2 旧 V1 项目

本阶段主要复用 V1 的工程经验：

```text
device.yaml 的组织方式
q1, q2, c, r1, r2 的器件结构
NodeGraph 思想
电容矩阵构造规则
结电阻 Rn 到 EJ 的解析工具
SQUID 有效 EJ 的参数入口
```

V1 中可参考文件：

```text
D:\claude\superconducting_simulation\superconducting-qc-sim-lab\V1\examples\default_2floatq_grounded_coupler\device.yaml
D:\claude\superconducting_simulation\superconducting-qc-sim-lab\V1\sqcsim\io\schema.py
D:\claude\superconducting_simulation\superconducting-qc-sim-lab\V1\sqcsim\circuit\topology.py
D:\claude\superconducting_simulation\superconducting-qc-sim-lab\V1\sqcsim\circuit\junction.py
```

### 2.3 与李少炜论文的阶段 1 对照

根据论文可抽取内容，第 3 章包含 LC 电路网络、电容矩阵、驱动项和多 Transmon 仿真方法；第 7 章包含两个 qubit 加 coupler 的结构背景、直接耦合与间接耦合、coupler flux 调节、XX/ZZ 影响以及并行量子门需求。

阶段 1 设计与论文背景一致的部分：

```text
1. 用 q1、q2 和 coupler 作为最小双比特门相关结构。
2. 显式保留电容网络，而不是只保存有效频率和有效耦合。
3. 显式保留 qubit-coupler 电容和 qubit-qubit 直接电容。
4. 显式保留 coupler 的 flux_bias_phi0，为后续调节耦合和 ZZ/XX 关断预留接口。
5. 显式保留 truncation，为后续多 Transmon Hilbert 空间构建预留接口。
6. 将控制通道和读出通道放入 device config，为后续真实实验式控制和校准预留接口。
```

阶段 1 设计中已经确认的工程选择：

```text
1. 当前设计沿用 V1 的 q1/q2 floating transmon + grounded coupler 表示。
   这是阶段 1 的已确认结构。论文中的具体实验器件是否完全采用相同接地/浮置约定，后续进入更精细建模时再结合图 7.1 和相关版图复核。

2. 当前阶段使用节点电容矩阵作为中间产物，但暂不做坐标变换、逆电容矩阵、EC 矩阵或哈密顿量构建。
   论文第 3 章会进入这些步骤，因此它们应放在阶段 2，而不是阶段 1。

3. 当前阶段用 Rn -> EJ 的工程估算作为参数解析方式。
   这是阶段 1 的已确认默认路径，符合“从结电阻参数出发”的目标。后续如果有更可靠实验 EJ，可再扩展显式 EJ 覆盖。

4. 当前配置加入 r1/r2 两个读出谐振腔。
   这符合完整虚拟量子计算机需求，但 r1/r2 不进入阶段 1 默认 2q1c 电容矩阵；读出模型应在阶段 8 再展开。
```

因此，阶段 1 与论文主线没有原则性冲突。后续最需要结合论文继续确认的是阶段 2 中从节点电容矩阵到 EC 矩阵和哈密顿量的坐标选择。

## 3. 本阶段边界

本阶段包含：

```text
器件配置文件
配置加载
参数验证
节点展开
电容矩阵
结参数解析
器件 summary
verification report
```

本阶段不包含：

```text
2q1c 哈密顿量构建
charge basis / product basis 截断
本征态求解
控制脉冲
读出动力学
Web UI
实验扫描
```

## 4. 目录与文件

阶段 1 实现后应新增：

```text
configs/devices/2q1c2r.yaml

src/sqvm/
  __init__.py
  __main__.py
  device/
    __init__.py
    spec.py
    validation.py
    capacitance.py
    junction.py
    artifacts.py
    verify.py

tests/
  test_device_spec.py
  test_device_validation.py
  test_device_capacitance.py
  test_device_junction.py
  test_verify_device.py
```

阶段 1 输出目录：

```text
output/stage_01_device_model/
```

## 5. 配置文件设计

第一版配置文件路径：

```text
configs/devices/2q1c2r.yaml
```

推荐结构：

```yaml
schema_version: "0.1"

device:
  name: demo_2q1c2r
  topology: 2q1c2r

  components:
    q1:
      kind: tunable_transmon
      role: qubit
      floating: true
      nodes: [q1_p, q1_m]
      capacitance_fF: 75.0
      squid:
        rn1_ohm: 18038.0
        rn2_ohm: 18038.0
        flux_bias_phi0: 0.10
        asymmetry: 0.0
      truncation: 5

    q2:
      kind: tunable_transmon
      role: qubit
      floating: true
      nodes: [q2_p, q2_m]
      capacitance_fF: 78.0
      squid:
        rn1_ohm: 17423.0
        rn2_ohm: 17423.0
        flux_bias_phi0: 0.00
        asymmetry: 0.0
      truncation: 5

    c:
      kind: tunable_coupler
      role: coupler
      floating: false
      nodes: [c]
      capacitance_fF: 60.0
      squid:
        rn1_ohm: 6188.0
        rn2_ohm: 6188.0
        flux_bias_phi0: 0.27
        asymmetry: 0.0
      truncation: 4

    r1:
      kind: readout_resonator
      role: readout
      coupled_to: q1
      coupling_node: q1_p
      frequency_GHz: 6.4
      coupling_capacitance_fF: 4.0
      kappa_MHz: 2.0

    r2:
      kind: readout_resonator
      role: readout
      coupled_to: q2
      coupling_node: q2_p
      frequency_GHz: 6.4
      coupling_capacitance_fF: 4.0
      kappa_MHz: 2.0

  capacitors:
    - name: C_q1p_c
      between: [q1_p, c]
      capacitance_fF: 4.0
    - name: C_q1m_c
      between: [q1_m, c]
      capacitance_fF: 3.8
    - name: C_q2p_c
      between: [q2_p, c]
      capacitance_fF: 4.1
    - name: C_q2m_c
      between: [q2_m, c]
      capacitance_fF: 3.9
    - name: C_q1p_q2p
      between: [q1_p, q2_p]
      capacitance_fF: 0.3

  channels:
    q1_xy:
      kind: xy
      target: q1
      port: q1_xy
    q2_xy:
      kind: xy
      target: q2
      port: q2_xy
    c_z:
      kind: z
      target: c
      port: c_z
    r1_ro:
      kind: readout
      target: r1
      port: r1_ro
    r2_ro:
      kind: readout
      target: r2
      port: r2_ro

  priors:
    q1:
      estimated_f01_GHz: 5.10
      estimated_anharmonicity_GHz: -0.25
      note: "optional"
    q2:
      estimated_f01_GHz: 5.30
      estimated_anharmonicity_GHz: -0.25
      note: "optional"
    c:
      estimated_idle_frequency_GHz: 7.50
      estimated_flux_sweet_spot_phi0: 0.0
      note: "optional"
    readout:
      r1_frequency_GHz: 6.40
      r2_frequency_GHz: 6.45
      note: "optional"
```

## 6. 单位约定

配置文件必须显式在字段名中带单位。

本阶段固定使用：

```text
capacitance_fF          fF
coupling_capacitance_fF fF
frequency_GHz           GHz
kappa_MHz               MHz
rn1_ohm, rn2_ohm        ohm
ej_GHz                  GHz
ej_sum_GHz              GHz
flux_bias_phi0          Phi0
truncation              dimensionless integer
```

第一版不允许无单位字段，例如 `frequency: 6.4` 或 `capacitance: 75`。

## 6.1 priors 规则

`priors` 用于保存实验或设计先验，只作为参考信息。

阶段 1 允许的 `priors` 字段：

```text
estimated_f01_GHz
estimated_anharmonicity_GHz
estimated_idle_frequency_GHz
estimated_flux_sweet_spot_phi0
r1_frequency_GHz
r2_frequency_GHz
note
```

规则：

```text
priors 不参与阶段 1 电容矩阵。
priors 不参与 Rn -> EJ。
priors 不覆盖 device.components 中的物理参数。
priors 写入 device_artifacts.json。
priors 在 verification.ipynb 中展示。
priors 可用于 warning 或 sanity check 的参考。
第一版不允许任意 priors 字段无限扩展。
```

## 7. 数据对象设计

### 7.1 DeviceSpec

```text
DeviceSpec
  schema_version: str
  name: str
  topology: str
  components: dict[str, ComponentSpec]
  capacitors: tuple[CapacitorSpec, ...]
  channels: dict[str, ChannelSpec]
  source_path: Path | None
```

### 7.2 ComponentSpec

```text
ComponentSpec
  name: str
  kind: str
  role: str
  floating: bool
  nodes: tuple[str, ...]
  capacitance_fF: float | None
  squid: SquidSpec | None
  resonator: ResonatorSpec | None
  truncation: int | None
```

说明：

```text
q1, q2, c 使用 capacitance_fF 和 squid。
r1, r2 使用 resonator 相关字段。
```

### 7.3 SquidSpec

```text
SquidSpec
  rn1_ohm: float | None
  rn2_ohm: float | None
  ej1_GHz: float | None
  ej2_GHz: float | None
  ej_sum_GHz: float | None
  flux_bias_phi0: float
  asymmetry: float
```

规则：

```text
可以通过 rn1_ohm/rn2_ohm 解析 EJ。
也可以直接给 ej1_GHz/ej2_GHz 或 ej_sum_GHz。
第一版要求每个 tunable component 至少能解析出 ej_sum_GHz。
```

### 7.4 CapacitorSpec

```text
CapacitorSpec
  name: str
  node_a: str
  node_b: str
  capacitance_fF: float
```

### 7.5 ChannelSpec

```text
ChannelSpec
  name: str
  kind: xy | z | readout
  target: str
  port: str
```

### 7.6 CapacitanceMatrix

```text
CapacitanceMatrix
  nodes: tuple[str, ...]
  matrix_fF: tuple[tuple[float, ...], ...]
```

### 7.7 JunctionParameterTable

```text
JunctionParameterRow
  component: str
  junction: str
  rn_ohm: float | None
  ej_GHz: float
  source: rn | explicit

JunctionParameterTable
  rows: tuple[JunctionParameterRow, ...]
```

### 7.8 ValidationReport

```text
ValidationIssue
  level: error | warning
  path: str
  message: str

ValidationReport
  ok: bool
  errors: tuple[ValidationIssue, ...]
  warnings: tuple[ValidationIssue, ...]
```

### 7.9 VerificationReport

```text
VerificationCheck
  name: str
  passed: bool
  message: str

VerificationReport
  ok: bool
  device_name: str
  checks: tuple[VerificationCheck, ...]
  artifacts: dict[str, str]
```

## 8. 节点展开规则

本项目第一版只支持 `topology: 2q1c2r`。

节点规则：

```text
固定存在 ground。
q1 使用 q1_p, q1_m 两个 island node。
q2 使用 q2_p, q2_m 两个 island node。
c 使用 c 一个 island node，另一个端点默认为 ground。
r1 使用 r1 一个 resonator node。
r2 使用 r2 一个 resonator node。
```

默认 `2q1c` 电容矩阵节点顺序：

```text
q1_p
q1_m
c
q2_p
q2_m
```

`ground` 不进入默认电容矩阵，但接地电容会贡献到对应节点的对角元。

`r1/r2` 在阶段 1 中只作为读出组件和 metadata 记录，不进入默认电容矩阵。读出谐振腔动力学和读出相关电容矩阵扩展留到读出模型阶段。

## 9. 电容矩阵规则

电容矩阵采用节点电容矩阵 convention。

对每个电容：

```text
C between node_i and node_j
```

若两个节点都不是 ground：

```text
M[i, i] += C
M[j, j] += C
M[i, j] -= C
M[j, i] -= C
```

若其中一个节点是 ground：

```text
M[i, i] += C
```

组件本征电容规则：

```text
floating transmon:
  capacitance_fF 作为 q_p 与 q_m 之间的电容。

grounded coupler:
  capacitance_fF 作为 c 与 ground 之间的电容。

readout resonator:
  frequency_GHz 不进入阶段 1 默认电容矩阵。
  coupling_capacitance_fF 记录在 device_artifacts.json 中。
  是否把 r 节点纳入矩阵留到读出模型阶段。
```

阶段 1 只构建电容矩阵，不进行坐标变换、不求逆电容矩阵、不处理奇异矩阵约化。这些留到阶段 2。

## 10. 结参数解析规则

### 10.1 Rn 到 EJ

沿用 V1 的工程公式作为第一版默认估算：

```text
Ic = pi * Delta / (2 e Rn)
EJ = Phi0 * Ic / (2 pi)
EJ_GHz = EJ / h / 1e9
```

默认常数：

```text
Phi0 = 2.067833848e-15 Wb
e    = 1.602176634e-19 C
h    = 6.62607015e-34 J*s
Delta_Al = 180e-6 eV
```

### 10.2 SQUID

若给出 `rn1_ohm` 和 `rn2_ohm`：

```text
ej1_GHz = rn_to_ej_GHz(rn1_ohm)
ej2_GHz = rn_to_ej_GHz(rn2_ohm)
ej_sum_GHz = ej1_GHz + ej2_GHz
```

阶段 1 第一版默认只支持从 `rn1_ohm` 和 `rn2_ohm` 估算 EJ。`ej1_GHz`、`ej2_GHz` 和 `ej_sum_GHz` 暂不作为输入主路径，后续如需要再加入显式 EJ 覆盖。

第一版只在 artifacts 中输出 `ej1_GHz`、`ej2_GHz` 和 `ej_sum_GHz`。有效 EJ 随 flux 的变化只在 notebook 中预留说明，不作为阶段 1 验收核心。

## 11. 验证规则

### 11.1 必须报错的情况

```text
schema_version 缺失或不支持
device.name 缺失
device.topology 不是 2q1c2r
缺少 q1、q2、c、r1、r2
q1/q2 不是 qubit role
c 不是 coupler role
r1/r2 不是 readout role
电容小于等于 0
rn_ohm 小于等于 0
ej_GHz 小于等于 0
truncation 小于 2
capacitor.between 不是两个节点
capacitor 引用了不存在的节点
channel 引用了不存在的 target
```

### 11.2 应给 warning 的情况

```text
q1/q2 truncation 小于 3
coupler truncation 小于 3
r1/r2 frequency_GHz 不在 4-10 GHz
r1/r2 kappa_MHz 不在 0.01-50 MHz
q1/q2/c capacitance_fF 不在 20-200 fF
rn_ohm 不在 1000-100000 ohm
flux_bias_phi0 不在 -1 到 1
未设置 XY channel
未设置 readout channel
```

warning 不阻止 artifact 生成，但必须写入 `device_artifacts.json`，并在 `verification.ipynb` 中展示。

这些阈值只是宽松 sanity check，不是论文参数的严格约束。真正物理合理性在阶段 2/3 通过能谱、耦合和静态表征继续检查。

## 12. 公开接口

### 12.1 Python API

```text
load_device(path: str | Path) -> DeviceSpec
```

读取 YAML，返回标准化后的 `DeviceSpec`。非法 YAML 或结构错误应抛出带路径信息的异常。

```text
validate_device(device: DeviceSpec) -> ValidationReport
```

只做验证，不写文件。

```text
build_capacitance_matrix(device: DeviceSpec) -> CapacitanceMatrix
```

构建确定性电容矩阵。

```text
resolve_junction_parameters(device: DeviceSpec) -> JunctionParameterTable
```

解析每个 tunable component 的 junction / SQUID 参数。

```text
write_device_artifacts(device: DeviceSpec, output_dir: str | Path) -> DeviceArtifactSet
```

写出唯一机器可读产物 `device_artifacts.json`。

```text
verify_device(path: str | Path, output_dir: str | Path) -> VerificationReport
```

加载、验证、生成 `device_artifacts.json` 和 `verification.ipynb`，并返回 verification report。

### 12.2 CLI

阶段 1 只需要一个命令：

```text
python -m sqvm verify-device configs/devices/2q1c2r.yaml --output output/stage_01_device_model
```

返回码：

```text
0: 验证通过
1: 验证失败
2: 命令参数错误
```

CLI 输出应简短，详细检查过程写入 `verification.ipynb`。

## 13. 输出产物格式

阶段 1 默认只输出两个文件：

```text
device_artifacts.json
verification.ipynb
```

设计原则：

```text
device_artifacts.json 是唯一机器可读数据源。
verification.ipynb 只读取 device_artifacts.json，不重复计算核心结果。
后续阶段依赖 Python API 或 device_artifacts.json，不依赖 notebook。
```

### 13.1 device_artifacts.json

`device_artifacts.json` 必须包含版本字段：

```text
schema_version:
  输入 device.yaml 的配置格式版本。

artifact_type:
  输出产物类型，阶段 1 固定为 stage_01_device_model。

artifact_version:
  输出产物格式版本，阶段 1 固定为 0.1。
```

内容：

```json
{
  "schema_version": "0.1",
  "artifact_type": "stage_01_device_model",
  "artifact_version": "0.1",
  "source_config": "configs/devices/2q1c2r.yaml",
  "device_summary": {
    "name": "demo_2q1c2r",
    "topology": "2q1c2r"
  },
  "components": {},
  "channels": {},
  "priors": {},
  "capacitance_matrix": {
    "nodes": ["q1_p", "q1_m", "c", "q2_p", "q2_m"],
    "matrix_fF": []
  },
  "junction_parameters": [],
  "validation": {
    "ok": true,
    "errors": [],
    "warnings": []
  },
  "checks": []
}
```

### 13.2 verification.ipynb

Notebook 是阶段 1 的人工检查入口。它的数据来源必须是：

```text
output/stage_01_device_model/device_artifacts.json
```

Notebook 应展示：

```text
器件名称与拓扑
组件表
通道表
电容矩阵表
电容矩阵 heatmap
结参数表
validation errors
validation warnings
checks pass/fail
阶段结论
```

Notebook 不作为后续阶段的数据接口。它只用于人工检查和展示。

阶段 1 只生成已执行的 `verification.ipynb`，不额外生成未执行版本。用户打开 notebook 时应能直接看到表格、图和检查结果。

如果后续需要非交互版本，可以从 notebook 渲染 HTML，但阶段 1 不强制。

## 14. 正确性检查

阶段 1 的 correctness evidence 包括：

```text
配置加载测试
验证规则测试
电容矩阵数值测试
结参数解析测试
CLI smoke test
verification.ipynb 人工检查
```

电容矩阵检查：

```text
shape 等于 len(node_order) x len(node_order)
矩阵对称
对角元非负
已连接的非 ground 节点对应非对角元为负
每一行求和等于该节点到 ground 的总电容
```

结参数检查：

```text
Rn 为正
解析得到的 EJ 为正
相同 Rn 的两个 junction 得到相同 EJ
ej_sum_GHz 等于 ej1_GHz + ej2_GHz
```

## 15. 测试设计

### 15.1 test_device_spec.py

```text
test_load_minimal_valid_device
test_reject_missing_device_name
test_reject_unsupported_topology
test_requires_q1_q2_c_r1_r2
```

### 15.2 test_device_validation.py

```text
test_reject_negative_capacitance
test_reject_negative_rn
test_reject_unknown_capacitor_node
test_warn_small_truncation
```

### 15.3 test_device_capacitance.py

```text
test_capacitance_matrix_node_order
test_capacitance_matrix_is_symmetric
test_floating_transmon_internal_capacitance
test_grounded_coupler_capacitance
test_readout_coupling_capacitance
```

### 15.4 test_device_junction.py

```text
test_rn_to_ej_positive
test_squid_ej_sum_from_two_rn
test_explicit_ej_sum
test_reject_unresolvable_squid
```

### 15.5 test_verify_device.py

```text
test_verify_device_writes_all_artifacts
test_verify_device_artifacts_contains_pass_fail
test_verification_notebook_reads_device_artifacts
test_cli_verify_device_success
test_cli_verify_device_failure
```

## 16. 与阶段 2 的接口边界

阶段 2 只能依赖阶段 1 的这些输出：

```text
DeviceSpec
CapacitanceMatrix
JunctionParameterTable
node_order
component -> node mapping
component -> ej_sum_GHz mapping
```

阶段 2 不应直接重新解析 YAML 内部结构。这样可以保证阶段 1 的接口是后续物理建模的唯一入口。

## 17. 阶段 1 验收清单

阶段 1 完成时必须满足：

```text
1. configs/devices/2q1c2r.yaml 存在。
2. python -m sqvm verify-device 可以运行。
3. output/stage_01_device_model/ 下生成 device_artifacts.json 和 verification.ipynb。
4. verification.ipynb 从 device_artifacts.json 读取数据，并明确显示所有检查 pass/fail。
5. tests/ 中阶段 1 测试通过。
6. docs/logs/DEVELOPMENT_LOG.md 记录实现结果。
7. 用户检查并认可 verification.ipynb 后，再进入阶段 2。
```
