# 阶段 2 计划：2q1c 哈密顿量构建

## 阶段目的

从阶段 1 的器件产物出发，构建 `q1-c-q2` 三模式 charge-basis 静态哈密顿量。

本阶段目标不是完成控制脉冲或门操作，而是先形成一条可检查、可重建、单位明确的哈密顿量构建链路：

```text
device_artifacts.json
  -> 模式坐标
  -> 模式电容矩阵
  -> E_C 矩阵
  -> SQUID 有效 EJ
  -> charge-basis Hamiltonian
  -> 低能能谱和正确性检查
```

## 参考来源

本阶段主要参考：

```text
1. 李少炜博士论文：
   参考 LC 电路网络、电容矩阵、多 Transmon 仿真、耦合器 flux 调节和双比特门相关背景。

2. 旧 V1 项目：
   参考从节点电容矩阵、结参数和器件配置进入哈密顿量构建的工程组织方式。

3. 阶段 1 输出：
   只依赖阶段 1 公开接口或 device_artifacts.json，不重新解析 device.yaml 内部结构。
```

本阶段暂不处理：

```text
时间依赖控制脉冲
QuTiP 时间演化
读出谐振腔动力学
门标定实验
Web lab
```

## 范围

本阶段包含：

```text
Hamiltonian 构建配置
模式坐标变换
节点电容矩阵快照
模式电容矩阵
E_C 矩阵
非对称 SQUID 有效 EJ
charge-basis sparse Hamiltonian
低能本征值求解
hamiltonian_artifacts.json
verification.ipynb
VSCode runner 脚本
```

本阶段不包含：

```text
完整 Hamiltonian 矩阵默认落盘
本征态持久化
dressed-state 标记
有效耦合 g/ZZ 提取
控制信号
读出模型
```

## 输入

阶段 2 输入包括：

```text
output/stage_01_device_model/device_artifacts.json
configs/hamiltonians/2q1c_charge_basis.yaml
```

第一版 Hamiltonian 配置：

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
    method: eigh
    tolerance: 1.0e-10  # 仅对未来 eigsh 有效；eigh 忽略
```

## 输出

本阶段输出目录：

```text
output/stage_02_hamiltonian/
```

默认输出文件：

```text
hamiltonian_artifacts.json
verification.ipynb
```

默认不输出完整 Hamiltonian 矩阵。完整矩阵应由 Python API 根据 device artifact 和 Hamiltonian config 确定性重建。

`hamiltonian_artifacts.json` 必须保存阶段 2 verification notebook 需要展示的全部数据。Notebook 只读取 `hamiltonian_artifacts.json`，不再读取阶段 1 artifact。

如果后续性能需要，可增加可选缓存：

```text
hamiltonian_sparse.npz
```

但阶段 2 第一版不实现缓存。

## 核心对象

阶段 2 初始对象：

```text
HamiltonianConfig
BasisConfig
SolverConfig
DeviceArtifacts
ModeTransform
ModeCapacitanceMatrix
ECMatrix
EffectiveJunctionTable
HamiltonianModel
HamiltonianArtifactSet
HamiltonianVerificationReport
```

这些对象第一版只支持 `2q1c` 子系统和固定模式顺序：

```text
q1, c, q2
```

## 公开接口

阶段 2 应暴露以下稳定接口：

```text
load_hamiltonian_config(path) -> HamiltonianConfig
load_device_artifacts(path) -> DeviceArtifacts
build_mode_transform(device_artifacts) -> ModeTransform
build_mode_capacitance_matrix(device_artifacts, transform) -> ModeCapacitanceMatrix
build_ec_matrix(mode_capacitance_matrix) -> ECMatrix
resolve_effective_ej(device_artifacts) -> EffectiveJunctionTable
build_hamiltonian(config, device_artifacts) -> HamiltonianModel
write_hamiltonian_artifacts(model, output_dir) -> HamiltonianArtifactSet
verify_hamiltonian(config_path, output_dir) -> HamiltonianVerificationReport
```

接口约定：

```text
电容矩阵使用 fF。
E_C、EJ、Hamiltonian 和本征值使用 GHz。
Hamiltonian 默认使用 sparse matrix。
artifact 中不保存完整 Hamiltonian 矩阵。
错误信息必须指出导致问题的配置路径或 artifact 路径。
```

阶段 2 需要的数值依赖：

```text
numpy
scipy
```

其中 sparse matrix 和 `eigsh` 来自 `scipy`。

## 用户工作流

后续阶段统一以 VSCode 运行脚本作为主要人工入口。

阶段 2 推荐流程：

```text
1. 在 VSCode 中打开 scripts/run_stage_02_hamiltonian.py。
2. 点击 Run Python File。
3. 查看终端中的简短结果摘要。
4. 打开 output/stage_02_hamiltonian/verification.ipynb。
5. 人工检查模式定义、电容矩阵、E_C、EJ_eff、Hamiltonian 摘要和低能能谱。
```

主运行脚本：

```text
scripts/run_stage_02_hamiltonian.py
```

脚本内部调用：

```python
from sqvm.hamiltonian import verify_hamiltonian

verify_hamiltonian(
    config_path="configs/hamiltonians/2q1c_charge_basis.yaml",
    output_dir="output/stage_02_hamiltonian",
)
```

CLI 作为备用自动化入口：

```text
python -m sqvm verify-hamiltonian configs/hamiltonians/2q1c_charge_basis.yaml --output output/stage_02_hamiltonian
```

## 正确性检查

本阶段必须提供以下正确性证据：

```text
1. Hamiltonian config 可以加载和验证。
2. source_device_artifacts 可以读取，且 artifact_type 是 stage_01_device_model。
3. mode_order 固定为 q1, c, q2。
4. coordinate_transform A 与 node_order 匹配。
5. C_mode = A^T C_node A。
6. C_mode 对称。
7. C_mode 正定；非正定或不可逆都算 error。
8. E_C 矩阵对称。
9. EJ_eff 为正且由 EJ1/EJ2/flux_bias_phi0 可复现。
10. Hamiltonian shape 等于 Hilbert dimension x Hilbert dimension。
11. Hamiltonian Hermitian。
12. 小 cutoff 下 sparse 构建与 dense 构建一致。
13. lowest_eigenvalues_GHz 有限且升序排列。
14. node_order 严格等于 [q1_p, q1_m, c, q2_p, q2_m]，否则 error（N1）。
15. 每个 tunable component 恰好 2 行 junction + 非空 flux_bias_phi0（N2）。
16. 第一 gap > 0 且处于合理 GHz 范围（约 0.1–20 GHz）（N9）。
17. single_transmon_analytic_limit：解耦单模数值 f01 vs sqrt(8 Ec EJ)-Ec，并对照 prior（H3）。
18. charge_basis_convergence：逐模 N vs N+2 的低能 gap 漂移（H4）。
19. 模式间耦合量级（C_mode 非对角）在 notebook 展示（N4）。
```

严格度（H3 / H4 决策）：

```text
1–16 为结构性检查，不通过则 verify_hamiltonian 失败。
17–19 为物理 sanity，不通过只 warning，不阻塞 verify。
```

## verification notebook

`verification.ipynb` 必须从 `hamiltonian_artifacts.json` 读取数据，并展示：

```text
输入来源
node_order
mode_order
coordinate_transform
C_node heatmap
C_mode heatmap（含非对角耦合量级，N4）
E_C matrix heatmap
EJ1/EJ2/flux_bias_phi0/EJ_eff 表
basis charge_cutoff
Hilbert dimension
sparse shape / nnz（含 solver_method）
hermiticity error
single_transmon_analytic_limit 对照（H3）
lowest_eigenvalues_GHz
energy gaps relative to ground
checks pass/fail（区分结构性 / 物理 sanity）
```

Notebook 不作为后续阶段的数据接口。后续阶段依赖 Python API 或 `hamiltonian_artifacts.json`。

渲染要求（N10）：

```text
notebook 生成时使用 matplotlib Agg backend + UTF-8，保证 headless / CI 可执行。
沿用阶段 1 notebook.py 的渲染模式。
```

## 实现任务

第一批实现任务：

```text
1. 新增 configs/hamiltonians/2q1c_charge_basis.yaml。
2. 新增 scripts/run_stage_02_hamiltonian.py。
3. 新增 src/sqvm/hamiltonian/ 模块。
4. 实现 Hamiltonian 配置解析和验证。
5. 实现 device_artifacts.json 读取。
6. 实现模式坐标变换。
7. 实现 C_mode 和 E_C 矩阵。
8. 实现非对称 SQUID EJ_eff。
9. 实现 charge basis sparse Hamiltonian。
10. 实现低能本征值求解。
11. 实现 hamiltonian_artifacts.json。
12. 实现 verification.ipynb。
13. 补充测试。
```

## 测试

必需测试：

```text
加载合法 Hamiltonian config
拒绝缺少 source_device_artifacts
拒绝 charge_cutoff 小于 1
拒绝未知 mode
构建固定 mode_order
构建固定 coordinate_transform
C_mode 与手工计算一致
E_C 矩阵对称
EJ_eff 非对称 SQUID 公式正确
Hamiltonian shape 正确
Hamiltonian Hermitian
小 cutoff sparse/dense 一致
lowest eigenvalues 有限且排序
写出 hamiltonian_artifacts.json
生成已执行 verification.ipynb
VSCode runner smoke test
默认 solver 是 eigh 且返回最低本征值（H2）
拒绝不支持的 node_order（N1）
junction / flux_bias 配对 check（N2）
single_transmon_analytic_limit check（H3）
charge_basis_convergence 逐模 check（H4）
物理 sanity 检查为 warn-only
```

## 输出产物

`hamiltonian_artifacts.json` 至少包含：

```text
schema_version
artifact_type
artifact_version
source_device_artifacts
hamiltonian_config
node_order
mode_order
coordinate_transform
node_capacitance_matrix_fF
mode_capacitance_matrix_fF
ec_matrix_GHz
effective_junctions
basis
offset_charge_ng
hilbert_dimension
hamiltonian_summary
solver
lowest_eigenvalues_GHz
eigenvalue_gaps_GHz
reference_priors
mode_coupling_fF
checks
```

## 验收标准

阶段 2 完成条件：

```text
1. VSCode 中运行 scripts/run_stage_02_hamiltonian.py 可以成功。
2. output/stage_02_hamiltonian/ 下生成 hamiltonian_artifacts.json 和 verification.ipynb。
3. Hamiltonian 可以由 API 确定性重建。
4. Hamiltonian 维度、单位和 sparse/dense 规则清楚。
5. Hamiltonian Hermitian 检查通过。
6. 最低本征值可以复现。
7. verification.ipynb 能清楚展示矩阵、能谱和 checks。
8. 阶段 2 测试通过。
9. 开发日志记录实现结果。
```

## 开放问题

阶段 2 第一版暂不解决但需要记录：

```text
1. 坐标变换：阶段 2 保持固定 A（差分模投影，对近对称 floating transmon 物理合理，见设计文档第 6 节）。
   是否在阶段 3 前扩展为可配置 / 自动推导，待定。
2. 共模处理：已确认共模不承载 qubit-coupler 耦合，固定 A 投影合理（H1 复核）。
   阶段 3 前需决定：是否调整示例器件耦合几何以获得真实 g（当前 g ~ 0.4 MHz 偏小）。
3. charge_cutoff 默认值：阶段 2 已加逐模收敛 check；是否根据漂移自动推荐，待定。
4. 本征态标记和 dressed-state 分析留到阶段 3。
5. 完整 Hamiltonian 缓存留到性能需要时再加入。
6. squid.asymmetry 字段（阶段 1 死字段）延后到统一清理 stage 1 时删除（M3）。
```
