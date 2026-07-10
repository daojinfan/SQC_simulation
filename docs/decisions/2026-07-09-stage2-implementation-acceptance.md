# 阶段 2 实现验收清单（给 Codex 自检用）

状态：

```text
实现就绪 / Codex 实现前交付
```

日期：

```text
2026-07-09
```

## 用法

Codex 实现完成后、提交审查前，逐项核对。

```text
"必须"项：全部满足才能交付，任一缺失或不符即审查不通过。
"物理 sanity"项：允许只 warning，但必须真实计算并在 artifact / notebook 展示。
红线项（第 I 节）：命中任一即直接不通过，不必继续审。
```

依据：

```text
docs/designs/02_hamiltonian_design.md
docs/stages/02_hamiltonian_plan.md
docs/decisions/2026-07-09-stage2-hamiltonian-review.md
```

## A. 文件与接口（必须）

```text
[ ] configs/hamiltonians/2q1c_charge_basis.yaml 存在，字段与设计第 5 节一致：
    schema_version / hamiltonian.name / source_device_artifacts /
    basis.{q1,c,q2}.charge_cutoff / solver.{num_eigenvalues, method=eigh, tolerance}
[ ] scripts/run_stage_02_hamiltonian.py 存在且尽量短（调用 verify_hamiltonian）
[ ] src/sqvm/hamiltonian/ 按设计第 4 节文件划分（config / artifacts / capacitance /
    junction / basis / builder / solver / notebook / verify）
[ ] 设计第 13 节公开接口全部实现，并从 sqvm.hamiltonian.__init__ 导出
```

## B. 物理实现（必须正确，审查重点）

```text
[ ] C_mode = A^T C_node A；A 的行顺序与读到的 node_order 严格对齐
[ ] E_C = e^2 / (2h) * C_mode^-1，单位 GHz
[ ] 物理常数（Phi0 / e / h / Delta_Al）复用阶段 1 来源，不在 hamiltonian 模块重新定义
[ ] SQUID EJ_eff 用非对称公式；phi 用 flux_bias_phi0（Phi0 单位，sweet spot 在整数 flux）
[ ] EJ1 / EJ2 来自 junction_parameters；flux_bias 来自 components.<X>.squid.flux_bias_phi0；
    resolve_effective_ej 按 component 做这个 join
[ ] 不读取 squid.asymmetry 字段（M3）
[ ] charge basis：n in [-N, N]，dim = 2N+1；Kronecker 顺序固定 q1 ⊗ c ⊗ q2
[ ] H_charge = 4 Σ_ij E_C,ij n_i n_j（含非对角项；在 product charge basis 中是对角矩阵）
[ ] H_J：cos(phi) 矩阵元 <n|n±1> = -EJ_eff / 2（实三对角，Hermitian）
[ ] offset charge 固定 n_g = 0
```

## C. 求解器（必须，H2 高风险区）

```text
[ ] 默认 method = eigh，用 scipy.linalg.eigh(H, subset_by_index=[0, num_eigenvalues-1]) 取最低 k 个
[ ] 若提供 eigsh：必须显式传 which / sigma，不得依赖 scipy 默认
    （默认 which='LM' 取最大模本征值，会返回最高能级而非最低）
[ ] 基态能量为负（约 -EJ_eff），shift-invert 的 sigma 应取在估计基态之下并配 which='LM'
[ ] tolerance 字段仅在 method=eigsh 时生效；method=eigh 忽略
```

## D. Checks（结构性 fail / 物理 warn）

```text
[ ] 结构性 check 全部实现（见设计第 16 节列表）；任一失败 -> verify_hamiltonian.ok = False
[ ] node_order_contract：node_order != [q1_p, q1_m, c, q2_p, q2_m] -> error（N1）
[ ] junction_flux_pairing：每个 tunable component 恰好 2 行 junction + 非空 flux_bias（N2）
[ ] ground_state_gap_sane：第一 gap > 0 且在约 0.1–20 GHz（N9）
[ ] 物理 sanity（single_transmon_analytic_limit / charge_basis_convergence /
    coupling_magnitude_displayed / ec_diagonal_range）只产生 warning，不改变 ok
[ ] 解析极限在"解耦单模 H"上做（off-diag E_C 置零），不是耦合 3 模 H（N5）
[ ] 收敛 check 逐模（coupler 的 EJ/E_C ≈ 118 比 qubit 的 ≈ 60 高，同样 N 下 coupler 更易欠收敛）（N6）
```

## E. Artifact 与 notebook

```text
[ ] hamiltonian_artifacts.json 含设计第 15.1 节全部字段
[ ] 新增字段齐备：offset_charge_ng / reference_priors / mode_coupling_fF /
    hamiltonian_summary.solver_method
[ ] reference_priors 从 device_artifacts 拷贝 prior f01；notebook 只读 hamiltonian_artifacts（N3）
[ ] verification.ipynb 已执行，含：C_mode 非对角耦合量级、single_transmon_analytic_limit 对照、
    checks（区分结构性 / 物理 sanity）
[ ] notebook 用 matplotlib Agg backend + UTF-8，headless / CI 可执行（N10）
```

## F. 依赖与运行

```text
[ ] pyproject.toml 显式加入 numpy、scipy
[ ] CLI python -m sqvm verify-hamiltonian 退出码正确（0 通过 / 1 失败）
[ ] VSCode runner scripts/run_stage_02_hamiltonian.py 可运行
```

## G. 数字自检（Codex 输出应接近这些值）

用 configs/devices/2q1c2r.yaml + 默认配置，输出应接近：

```text
E_C 对角：q1 ≈ 0.251, c ≈ 0.255, q2 ≈ 0.242 GHz
f01：q1 ≈ 5.2, q2 ≈ 5.34, c ≈ 7.57 GHz（与阶段 1 priors 5.10 / 5.30 / 7.50 吻合）
C_mode 非对角：约 0.1 fF（对应 g ~ 0.4 MHz）
  注意：小耦合是预期的（示例器件近对称几何），不是 bug，无需"修"它。
Hilbert 维度：1331（N = 5）
```

若数值明显偏离（如 E_C 差一个数量级、f01 不匹配 prior、本征值非有限），先排查 B / C 节实现。

## H. 自测命令（交付前必跑）

```text
python -m pytest -q
  期望：全绿（含本次新增的 check 相关测试）

python -m sqvm verify-hamiltonian configs/hamiltonians/2q1c_charge_basis.yaml \
    --output output/stage_02_hamiltonian
  期望：ok=true，生成 hamiltonian_artifacts.json + verification.ipynb
```

## I. 交付时请附上（便于审查）

```text
最低 12 个本征值 / gaps
C_mode 矩阵、E_C 对角、EJ_eff per mode
single_transmon_analytic_limit 与 prior 的偏差
charge_basis_convergence 逐模漂移
任何与设计文档不符的偏差及理由
```

## J. 红线（命中任一即审查不通过）

```text
eigsh 路径未显式 which / sigma（会取错本征值）
误用 squid.asymmetry 字段
物理 sanity check 直接让 verify 失败（违反 H3/H4 严格度决策）
node_order 不校验就直接套固定 A（N1）
EJ1/EJ2 与 flux_bias 拼接错位（N2）
解析极限在耦合 3 模 H 上做而非解耦单模 H（N5）
artifact 缺关键字段，或 notebook 越界读取 device_artifacts（违反 N3）
物理常数在 hamiltonian 模块重新定义而非复用（N8）
```
