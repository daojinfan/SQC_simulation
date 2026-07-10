# 阶段 2 详细设计：2q1c 哈密顿量构建

## 1. 设计目标

阶段 2 的目标是从阶段 1 的器件产物构建一个透明、可检查、可重建的 `2q1c` 静态哈密顿量。

本阶段聚焦三个模式：

```text
q1
c
q2
```

读出谐振腔 `r1/r2` 仍然不进入哈密顿量。读出和测量模型留到后续阶段。

本阶段的核心链路：

```text
stage_01 device_artifacts.json
  -> node capacitance matrix
  -> mode capacitance matrix
  -> E_C matrix
  -> effective SQUID EJ
  -> charge-basis Hamiltonian
  -> lowest eigenvalues
```

## 2. 主要参考

### 2.1 李少炜博士论文

本阶段主要参考论文中以下内容：

```text
LC 电路网络
电容矩阵
多 Transmon 仿真
可调耦合器 flux 调节
双比特门相关的 q1-c-q2 结构背景
```

本阶段只实现静态哈密顿量构建，不实现论文中的完整控制脉冲、门优化或实验校准流程。

### 2.2 旧 V1 项目

本阶段主要参考 V1 的工程思想：

```text
从器件配置进入电路模型
从节点电容矩阵进入后续物理模型
Rn 到 EJ 的参数解析
阶段化输出可检查 artifact
```

### 2.3 阶段 1 产物

阶段 2 不直接解析 `device.yaml`。阶段 2 只依赖：

```text
output/stage_01_device_model/device_artifacts.json
```

或者阶段 1 公开 Python API 返回的等价对象。

这样可以保证阶段边界清楚：

```text
device.yaml -> 阶段 1
hamiltonian config -> 阶段 2
```

## 3. 本阶段边界

本阶段包含：

```text
Hamiltonian 构建配置
固定 2q1c 模式坐标
节点电容矩阵快照
C_mode 计算
E_C 矩阵计算
非对称 SQUID EJ_eff
charge basis 构建
sparse Hamiltonian
低能本征值
验证 notebook
```

本阶段不包含：

```text
控制脉冲
时间依赖 Hamiltonian
QuTiP 演化
dressed-state 标记
门参数提取
读出动力学
Web UI
```

## 4. 文件与目录

阶段 2 实现后应新增：

```text
configs/hamiltonians/2q1c_charge_basis.yaml

scripts/
  run_stage_02_hamiltonian.py

src/sqvm/hamiltonian/
  __init__.py
  config.py
  artifacts.py
  capacitance.py
  junction.py
  basis.py
  builder.py
  solver.py
  notebook.py
  verify.py

tests/
  test_hamiltonian_config.py
  test_hamiltonian_capacitance.py
  test_hamiltonian_junction.py
  test_hamiltonian_builder.py
  test_hamiltonian_verify.py
```

阶段 2 输出目录：

```text
output/stage_02_hamiltonian/
```

## 5. Hamiltonian 配置

第一版配置文件：

```text
configs/hamiltonians/2q1c_charge_basis.yaml
```

推荐结构：

```yaml
schema_version: "0.1"

hamiltonian:
  name: demo_2q1c_charge_basis
  source_device_artifacts: output/stage_01_device_model/device_artifacts.json

  basis:
    q1:
      charge_cutoff: 5
    c:
      charge_cutoff: 5
    q2:
      charge_cutoff: 5

  solver:
    num_eigenvalues: 12
    method: eigh          # 默认稠密直接对角
    tolerance: 1.0e-10    # 仅对 eigsh 有效；eigh 忽略
```

### 5.1 配置职责

`device.yaml` 只保存器件物理结构和物理参数。

Hamiltonian 配置保存数值构建策略，例如：

```text
charge_cutoff
num_eigenvalues
solver method
tolerance
```

阶段 2 不在 `device.yaml` 中保存 `truncation`、`levels_to_keep` 或 `modeled_levels`。这些名字在阶段 2 第一版不使用，避免同一含义在不同配置中重复或冲突。

### 5.2 数值依赖

阶段 2 需要显式依赖：

```text
numpy
scipy
```

用途：

```text
numpy:
  dense array、矩阵乘法、矩阵求逆、dense 对照。

scipy:
  sparse matrix、Kronecker product；
  默认稠密求解用 scipy.linalg.eigh（subset_by_index）；
  sparse eigensolver eigsh 仅作大维度可选，且必须显式指定 which / sigma。
```

物理常数共享：

```text
Phi0、e、h、Delta_Al 等物理常数已在阶段 1 定义（junction.py）。
阶段 2 复用同一来源（如 sqvm.device.constants 或共享模块），不在 hamiltonian 模块重新定义，
避免两处常数值漂移。E_C 使用的 e^2 / (2h) 由共享常数推导。
```

### 5.3 charge_cutoff

`charge_cutoff` 定义 charge basis 的截断范围：

```text
n = -charge_cutoff, ..., 0, ..., +charge_cutoff
```

单模式维度：

```text
dim_i = 2 * charge_cutoff_i + 1
```

默认：

```text
q1: charge_cutoff = 5 -> dim = 11
c : charge_cutoff = 5 -> dim = 11
q2: charge_cutoff = 5 -> dim = 11
```

总 Hilbert 维度：

```text
dim_total = 11 * 11 * 11 = 1331
```

## 6. 模式坐标

阶段 1 节点顺序固定为：

```text
node_order = [q1_p, q1_m, c, q2_p, q2_m]
```

阶段 2 模式顺序固定为：

```text
mode_order = [q1, c, q2]
```

模式定义：

```text
q1 = q1_p - q1_m
c  = c - ground
q2 = q2_p - q2_m
```

第一版使用固定线性变换：

```text
theta_node = A * theta_mode
```

其中：

```text
A =
[
  [ 0.5, 0.0, 0.0],
  [-0.5, 0.0, 0.0],
  [ 0.0, 1.0, 0.0],
  [ 0.0, 0.0, 0.5],
  [ 0.0, 0.0,-0.5],
]
```

模式电容矩阵：

```text
C_mode = A^T C_node A
```

该固定变换的意义：

```text
q1/q2 使用差分模式，先排除 floating transmon 共模。
coupler 是 grounded mode，直接使用 c 节点。
```

后续如果需要更严格的自动电路量化或共模处理，可以扩展该步骤，但阶段 2 第一版先固定。

物理合理性说明（H1 复核结论）：

```text
固定 A 是差分模投影：保留 q1/q2 的 plasma 模（差分），丢掉共模。
共模无 Josephson 恢复力（高 E_C、无 EJ），不承载 qubit-coupler 耦合，
因此投影对近对称 floating transmon 是合理近似；black-box / 正则模量化
对本示例器件会给出相同量级的耦合。
注意：示例器件两 island 对 coupler 的耦合近对称（4.0 / 3.8、4.1 / 3.9），
故模式间耦合天然很小（g ~ 0.4 MHz）。这是几何结果，不是投影 bug。
阶段 2 接受该小耦合；是否在阶段 3 前调整几何以获得真实 g，留作开放问题。
```

## 7. 电容到 E_C

单位约定：

```text
C_node: fF
C_mode: fF
C_mode_F: F
C_inv: 1/F
E_C: GHz
Hamiltonian: GHz
```

换算：

```text
C_mode_F = C_mode_fF * 1e-15
C_inv = inverse(C_mode_F)
E_C_Hz = e^2 / (2 h) * C_inv
E_C_GHz = E_C_Hz / 1e9
```

charging Hamiltonian：

```text
H_charge = 4 * sum_ij E_C,ij n_i n_j
```

单 transmon 极限下回到：

```text
H_charge = 4 E_C n^2
```

正确性检查：

```text
C_mode 对称
C_mode 正定
非正定或不可逆都算 error
E_C 对称
E_C 对角元为正
```

## 8. SQUID 有效 EJ

阶段 1 artifact 中每个 tunable component 输出两个 junction：

```text
component
junction
rn_ohm
ej_GHz
source
```

阶段 2 使用非对称 SQUID 有效 EJ：

```text
EJ_eff(phi) =
  sqrt((EJ1 + EJ2)^2 * cos^2(pi * phi)
       + (EJ1 - EJ2)^2 * sin^2(pi * phi))
```

其中：

```text
phi 使用 flux_bias_phi0，单位 Phi0。
EJ1/EJ2 来自阶段 1 的 junction_parameters。
EJ_eff 单位 GHz。
```

若 `EJ1 == EJ2`，该公式退化为：

```text
EJ_eff(phi) = (EJ1 + EJ2) * abs(cos(pi * phi))
```

阶段 2 第一版不引入 flux 扫描，只使用阶段 1 中记录的 idle `flux_bias_phi0`。

近似与契约说明：

```text
1. 忽略 SQUID 相位偏置 delta（M1）：
   严格势能为 -EJ_eff * cos(phi_i - delta_i)。阶段 2 只用 -EJ_eff * cos(phi_i)，
   即 delta = 0。该近似在 transmon 区（EJ / E_C 远大于 1，电荷色散指数小）可接受；
   flux 接近 0.5 或 coupler 进入电荷敏感区时需复核。

2. 参数拼接契约（N2）：
   EJ1 / EJ2 来自阶段 1 junction_parameters（每个 tunable component 恰好两行 j1 / j2），
   flux_bias_phi0 来自 components.<X>.squid.flux_bias_phi0（不在 junction_parameters 中）。
   resolve_effective_ej 必须按 component 做这个 join。
   禁止使用 squid.asymmetry 字段（M3，阶段 1 死字段；非对称性已由 rn1 != rn2 体现）。
```

## 9. Charge Basis

每个模式的 basis：

```text
n_i = -N_i, ..., 0, ..., +N_i
```

其中：

```text
N_i = charge_cutoff_i
```

总 basis 顺序采用 product basis：

```text
|n_q1, n_c, n_q2>
```

Kronecker product 顺序固定为：

```text
q1 ⊗ c ⊗ q2
```

这必须写入 artifacts，后续阶段不能猜测 basis 顺序。

offset charge 约定（D1）：

```text
阶段 2 第一版固定 offset charge n_g = 0（无门电压 / charge degeneracy 假设）。
该值写入 hamiltonian_artifacts.json，避免阶段 3 猜测本征值对应的偏置点。
```

## 10. Hamiltonian 形式

阶段 2 第一版采用多 transmon charge-basis Hamiltonian：

```text
H = H_charge + H_J
```

其中：

```text
H_charge = 4 * sum_ij E_C,ij n_i n_j
```

Josephson 项：

```text
H_J = -sum_i EJ_eff,i cos(phi_i)
```

在 charge basis 中：

```text
cos(phi_i) = (exp(i phi_i) + exp(-i phi_i)) / 2
```

矩阵元：

```text
<n| -EJ cos(phi) |n+1> = -EJ / 2
<n+1| -EJ cos(phi) |n> = -EJ / 2
```

三模式总 Hamiltonian 使用 sparse Kronecker product 组合：

```text
H = H_charge + H_J,q1 + H_J,c + H_J,q2
```

## 11. Sparse 与 Dense

默认内部构建 sparse Hamiltonian（作为存储表示；求解策略见第 12 节，默认走稠密 eigh）。

原因：

```text
H_charge 是对角项。
H_J 只连接 n 和 n±1。
绝大多数矩阵元素为 0。
```

Sparse 不改变数值精度，只改变存储方式。

正确性检查要求：

```text
在小 charge_cutoff 下，同时构建 sparse 和 dense Hamiltonian。
比较 sparse_H.toarray() 与 dense_H 数值一致。
```

阶段 2 artifact 记录：

```text
is_sparse
shape
nnz
dense_equivalence_check
```

## 12. 本征值求解

默认 solver 配置：

```yaml
solver:
  num_eigenvalues: 12
  method: eigh          # 默认稠密直接对角
  tolerance: 1.0e-10    # 仅对 eigsh 有效；eigh 忽略
```

说明：

```text
默认 method = eigh：scipy.linalg.eigh(H, subset_by_index=[0, num_eigenvalues-1])
  精确取最低 num_eigenvalues 个本征值。当前 dim=1331，约 1-2 秒，稳定无坑。
eigsh 作为大维度可选 solver：
  必须显式指定 which / sigma，不能依赖 scipy 默认
  （默认 which='LM' 取最大模本征值，会返回最高能级而非最低）。
  本例基态能量为负（约 -EJ_eff），shift-invert 的 sigma 应取在估计基态之下并配 which='LM'。
tolerance 仅在 method=eigsh 时有意义；method=eigh 走 LAPACK，忽略该字段。
```

阶段 2 只求最低若干本征值，不保存完整本征态。

输出：

```text
lowest_eigenvalues_GHz
eigenvalue_gaps_GHz
```

`eigenvalue_gaps_GHz` 定义为：

```text
E_k - E_0
```

正确性检查：

```text
本征值有限
本征值升序排列
gaps[0] = 0
第一 gap > 0 且处于合理 GHz 范围（约 0.1–20 GHz），防退化 / 病态谱
```

## 13. 公开接口设计

### 13.1 load_hamiltonian_config

```text
load_hamiltonian_config(path: str | Path) -> HamiltonianConfig
```

读取 YAML 配置，验证：

```text
schema_version 支持
hamiltonian.name 存在
source_device_artifacts 存在
basis 只包含 q1/c/q2
charge_cutoff 为正整数
solver.num_eigenvalues 为正整数
solver.method 阶段 2 第一版只支持 eigh
solver.tolerance 若存在必须为正数；method=eigh 时忽略
```

### 13.2 load_device_artifacts

```text
load_device_artifacts(path: str | Path) -> DeviceArtifacts
```

读取阶段 1 artifact，验证：

```text
artifact_type == stage_01_device_model
artifact_version 支持
capacitance_matrix 存在
junction_parameters 存在
components 存在
```

### 13.3 build_mode_transform

```text
build_mode_transform(device_artifacts: DeviceArtifacts) -> ModeTransform
```

输出：

```text
node_order
mode_order
matrix
description
```

### 13.4 build_mode_capacitance_matrix

```text
build_mode_capacitance_matrix(device_artifacts, transform) -> ModeCapacitanceMatrix
```

执行：

```text
C_mode = A^T C_node A
```

### 13.5 build_ec_matrix

```text
build_ec_matrix(mode_capacitance_matrix) -> ECMatrix
```

执行单位换算和矩阵求逆。

### 13.6 resolve_effective_ej

```text
resolve_effective_ej(device_artifacts) -> EffectiveJunctionTable
```

输出每个模式：

```text
mode
component
ej1_GHz
ej2_GHz
flux_bias_phi0
ej_effective_GHz
formula
```

### 13.7 build_hamiltonian

```text
build_hamiltonian(config, device_artifacts) -> HamiltonianModel
```

输出：

```text
basis
ec_matrix
effective_junctions
matrix
summary
```

### 13.8 verify_hamiltonian

```text
verify_hamiltonian(config_path, output_dir) -> HamiltonianVerificationReport
```

执行完整验证流程并写出：

```text
hamiltonian_artifacts.json
verification.ipynb
```

## 14. VSCode 运行入口

主入口：

```text
scripts/run_stage_02_hamiltonian.py
```

脚本内容应尽量短：

```python
from sqvm.hamiltonian import verify_hamiltonian


if __name__ == "__main__":
    report = verify_hamiltonian(
        config_path="configs/hamiltonians/2q1c_charge_basis.yaml",
        output_dir="output/stage_02_hamiltonian",
    )
    print(report)
```

VSCode 工作流是阶段 2 的主要人工运行方式。CLI 只作为备用自动化入口。

## 15. 输出产物格式

阶段 2 的 notebook 只读取 `hamiltonian_artifacts.json`。因此，`hamiltonian_artifacts.json` 必须保存 notebook 展示所需的阶段 1 电容矩阵快照，而不是让 notebook 再读取 `device_artifacts.json`。

### 15.1 hamiltonian_artifacts.json

必须包含：

```json
{
  "schema_version": "0.1",
  "artifact_type": "stage_02_hamiltonian",
  "artifact_version": "0.1",
  "source_device_artifacts": "output/stage_01_device_model/device_artifacts.json",
  "hamiltonian_config": {},
  "node_order": [],
  "mode_order": ["q1", "c", "q2"],
  "coordinate_transform": {
    "convention": "theta_node = A * theta_mode",
    "matrix": []
  },
  "node_capacitance_matrix_fF": {
    "nodes": [],
    "matrix_fF": []
  },
  "mode_capacitance_matrix_fF": {
    "modes": [],
    "matrix_fF": []
  },
  "ec_matrix_GHz": {
    "modes": [],
    "matrix_GHz": []
  },
  "effective_junctions": [],
  "basis": {},
  "offset_charge_ng": 0,
  "hilbert_dimension": 1331,
  "hamiltonian_summary": {
    "representation": "sparse",
    "solver_method": "eigh",
    "shape": [1331, 1331],
    "nnz": 0,
    "hermiticity_error": 0.0
  },
  "solver": {},
  "lowest_eigenvalues_GHz": [],
  "eigenvalue_gaps_GHz": [],
  "reference_priors": {},
  "mode_coupling_fF": {},
  "checks": []
}
```

新增字段说明（N3 / D1 / N4）：

```text
offset_charge_ng：固定 offset charge（第一版 n_g = 0）。
reference_priors：参与解析极限对照的 prior（如 prior_f01_GHz per mode），
  从阶段 1 device_artifacts 拷贝进来，notebook 只读 hamiltonian_artifacts。
mode_coupling_fF：C_mode 非对角项，用于展示模式间耦合量级。
hamiltonian_summary.solver_method：记录实际求解方法（eigh / eigsh）。
```

### 15.2 verification.ipynb

Notebook 数据来源必须是：

```text
output/stage_02_hamiltonian/hamiltonian_artifacts.json
```

Notebook 应展示：

```text
输入来源
模式定义
坐标变换矩阵
C_node heatmap
C_mode heatmap（含非对角耦合量级，N4）
E_C heatmap
EJ_eff 表
Hamiltonian summary（含 solver_method）
single_transmon_analytic_limit 对照表（H3）
低能能谱表
gaps 折线图
checks pass/fail（区分结构性 / 物理 sanity）
```

Notebook 只用于人工检查，不作为后续阶段接口。

渲染要求（N10）：

```text
notebook 生成时使用 matplotlib Agg backend + UTF-8，保证 headless / CI 可执行。
沿用阶段 1 notebook.py 的渲染模式。
```

## 16. 正确性检查

阶段 2 checks 分两类，严格度不同（见 H3 / H4 决策：物理 sanity 全部只 warn）。

结构性检查（不通过则 verify_hamiltonian 失败）：

```text
device_artifacts_type
node_order_contract            # node_order 严格等于 [q1_p,q1_m,c,q2_p,q2_m]，否则 error（N1）
mode_order_expected
coordinate_transform_shape
mode_capacitance_symmetric
mode_capacitance_positive_definite
ec_matrix_symmetric
effective_ej_positive
junction_flux_pairing          # 每个 tunable component 恰好 2 行 junction + 非空 flux_bias（N2）
basis_dimension_matches_cutoff
hamiltonian_shape
hamiltonian_hermitian
sparse_dense_small_cutoff_equivalent
eigenvalues_finite
eigenvalues_sorted
ground_state_gap_sane          # 第一 gap > 0 且在约 0.1–20 GHz（N9）
```

物理 sanity 检查（不通过只 warning，不阻塞 verify）：

```text
single_transmon_analytic_limit # 解耦单模 H 的数值 f01 vs sqrt(8 Ec EJ)-Ec，并对照 prior（H3 / N5）
charge_basis_convergence       # 逐模 N vs N+2 的低能 gap 漂移（H4 / N6）
coupling_magnitude_displayed   # 展示模式间耦合量级（C_mode 非对角），便于人工判断（N4，降级）
ec_diagonal_range              # E_C 对角元在约 0.05–2 GHz（D2）
```

每个 check 必须包含：

```text
name
passed
message
```

verify_hamiltonian 的 ok 只由结构性检查和 validation 决定；物理 sanity 只写入 checks 并在 notebook 展示。

## 17. 测试设计

### 17.1 test_hamiltonian_config.py

```text
test_load_valid_hamiltonian_config
test_reject_missing_source_device_artifacts
test_reject_unknown_mode
test_reject_invalid_charge_cutoff
test_reject_invalid_solver
test_default_solver_is_eigh
test_reject_unsupported_node_order
```

### 17.2 test_hamiltonian_capacitance.py

```text
test_mode_order_is_q1_c_q2
test_coordinate_transform_values
test_mode_capacitance_matches_a_t_c_a
test_mode_capacitance_positive_definite
test_ec_matrix_is_symmetric
test_ec_units_are_ghz
```

### 17.3 test_hamiltonian_junction.py

```text
test_effective_ej_symmetric_squid_limit
test_effective_ej_asymmetric_formula
test_effective_ej_uses_flux_bias_phi0
```

### 17.4 test_hamiltonian_builder.py

```text
test_charge_basis_dimension
test_hamiltonian_shape
test_hamiltonian_is_hermitian
test_sparse_dense_equivalence_small_cutoff
test_lowest_eigenvalues_sorted
test_single_transmon_analytic_limit
test_charge_basis_convergence_per_mode
test_ground_state_gap_sane
```

### 17.5 test_hamiltonian_verify.py

```text
test_verify_hamiltonian_writes_artifacts
test_hamiltonian_artifacts_contains_checks
test_verification_notebook_reads_hamiltonian_artifacts
test_vscode_runner_smoke
test_cli_verify_hamiltonian_success
test_junction_flux_pairing_check
test_physical_sanity_checks_are_warn_only
```

## 18. 已知限制

阶段 2 第一版限制：

```text
只支持 2q1c。
只支持固定 mode_order = q1, c, q2。
只支持固定坐标变换 A（差分模投影，对近对称 floating transmon 物理合理；见第 6 节）。
只支持静态 flux_bias_phi0。
忽略非对称 SQUID 相位偏置 delta（见第 8 节）。
不使用 squid.asymmetry 字段（阶段 1 死字段；非对称性由 rn1 != rn2 体现）。
不保存完整 Hamiltonian 矩阵。
不保存本征态。
不做 dressed-state 标记。
不计算 qubit frequency、anharmonicity、g、ZZ。
示例器件耦合几何近对称，模式间耦合天然很小（g ~ 0.4 MHz）；
是否调整几何以获得真实 g，留到阶段 3 前决策。
```

这些限制不阻塞阶段 2；它们属于阶段 3 或后续阶段。

## 19. 与阶段 3 的接口边界

阶段 3 只能依赖阶段 2 的公开接口和输出：

```text
HamiltonianModel
hamiltonian_artifacts.json
mode_order
basis
lowest_eigenvalues_GHz
API 可重建 Hamiltonian
```

阶段 3 不应重新解析 Hamiltonian YAML 的内部细节，也不应绕过阶段 2 直接从 device_artifacts 构建自己的 Hamiltonian。

## 20. 阶段 2 验收清单

阶段 2 完成时必须满足：

```text
1. configs/hamiltonians/2q1c_charge_basis.yaml 存在。
2. scripts/run_stage_02_hamiltonian.py 可以在 VSCode 中运行。
3. output/stage_02_hamiltonian/ 下生成 hamiltonian_artifacts.json 和 verification.ipynb。
4. verification.ipynb 从 hamiltonian_artifacts.json 读取数据。
5. Hamiltonian Hermitian 检查通过。
6. sparse/dense 小 cutoff 一致性检查通过。
7. 最低本征值有限且排序。
8. tests/ 中阶段 2 测试通过。
9. docs/logs/DEVELOPMENT_LOG.md 记录实现结果。
10. 用户检查并认可 verification.ipynb 后，再进入阶段 3。
```
