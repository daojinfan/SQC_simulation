# 阶段 3 计划：静态能谱与 dressed-state 分析

## 阶段目的

在阶段 2 已经可以构建 `2q1c` 静态 Hamiltonian 的基础上，计算并解释静态能谱。

本阶段目标是把“能级列表”变成可用于后续实验和控制设计的物理表征：

```text
Hamiltonian
  -> eigenvalues / eigenvectors
  -> bare-basis overlap
  -> dressed-state labels
  -> q1/q2/c 频率
  -> anharmonicity
  -> mode participation
  -> 可获得的 residual ZZ 类指标
  -> coupler flux scan 与 avoided-crossing 指标
  -> 静态表征 artifact 和 verification notebook
```

本阶段不做时间演化，不做控制脉冲，不做实验运行框架。

## 参考来源

本阶段主要参考：

```text
1. 李少炜博士论文：
   参考多 Transmon 静态能谱、耦合器 flux 调节、避免交叉、ZZ/XX 影响和双比特门背景。

2. 阶段 2 输出和 API：
   依赖阶段 2 的 HamiltonianModel、mode_order、basis、E_C、EJ_eff、可重建 Hamiltonian。

3. 旧 V1 项目：
   参考静态表征结果组织方式和 artifact / notebook 检查习惯。
```

## 范围

本阶段包含：

```text
静态 spectrum 配置
Hamiltonian 重建
低能 eigenvalues / eigenvectors 求解
bare product basis 构造
dressed-state 标记
q1/q2/c 频率提取
anharmonicity 估计
mode participation 表
residual ZZ 类指标
flux 点单点分析
coupler flux scan
q1-c / c-q2 avoided-crossing 解析证据、最小 splitting 与阶段门状态
关键指标的 charge-cutoff 收敛检查
static_spectrum_artifacts.json
verification.ipynb
VSCode runner 脚本
```

本阶段不包含：

```text
时间依赖控制
QuTiP 演化
读出谐振腔
真实实验数据拟合
自动校准参数更新
CZ 脉冲搜索
Web UI
```

## 输入

阶段 3 输入包括：

```text
configs/spectra/2q1c_static.yaml
configs/spectra/2q1c_static_smoke.yaml
configs/hamiltonians/2q1c_charge_basis.yaml
output/stage_02_hamiltonian/hamiltonian_artifacts.json
output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json
output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json
```

推荐配置：

```yaml
schema_version: "0.1"

spectrum:
  name: demo_2q1c_static_spectrum
  source_hamiltonian_config: configs/hamiltonians/2q1c_charge_basis.yaml
  source_hamiltonian_artifacts: output/stage_02_hamiltonian/hamiltonian_artifacts.json
  source_rebaseline_manifest: output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json
  source_rebaseline_approval: output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json

  eigen:
    num_states: 48
    solver: validated_eigsh
    eigsh:
      which: SA
      tolerance: 1.0e-10
      maxiter: 200000
      ncv: 97
      v0_rule: sha256_counter_v1
    solver_validation:
      gap_dense_tolerance_GHz: 1.0e-6
      near_degenerate_gap_threshold_GHz: 1.0e-5
      partition_boundary_margin_GHz: 2.0e-6
      projector_error_norm: spectral_2
      projector_dense_tolerance: 1.0e-6
      projector_repeat_tolerance: 1.0e-8

  dressed_labeling:
    max_excitations:
      q1: 2
      c: 2
      q2: 2
    max_total_excitations: 2
    min_overlap: 0.50
    assignment: global_overlap
    continuity_min_overlap: 0.90

  metrics:
    compute_zz: true
    compute_anharmonicity: true
    compute_mode_participation: true

  convergence:
    cutoff_increment: 2
    crossing_refinement_coarse_points: 17
    crossing_refinement_levels: 4
    frequency_tolerance_MHz: 0.50
    anharmonicity_tolerance_MHz: 1.00
    zz_absolute_tolerance_MHz: 0.01
    avoided_crossing_absolute_tolerance_MHz: 0.01
    avoided_crossing_relative_tolerance: 0.05
    participation_fraction_tolerance: 0.02

  crossing_evidence:
    endpoint_character_min_fraction: 0.80
    character_exchange_min_delta: 0.60
    target_pair_min_fraction_at_crossing: 0.80
    splitting_significance_min_ratio: 5.0

  flux_scan:
    enabled: true
    target: c
    start_phi0: 0.20
    stop_phi0: 0.45
    coarse_points: 25
    min_refinement_levels: 4
    max_refinement_levels: 8
    refinement_points: 11
    flux_key_decimal_places: 12
    splitting_level_tolerance_MHz: 0.005
    flux_energy_resolution_MHz: 0.005

  runtime:
    acceptance_budget_seconds: 1800
    smoke_budget_seconds: 90
    over_budget_fallback: fail
    solver_validation_artifact: output/stage_03_solver_validation/eigsh_validation.json
    solver_validation_approval: output/stage_03_solver_validation/eigsh_validation_approval.json
```

说明：

```text
source_hamiltonian_config 只交给阶段 2 API 读取，用于重建 Hamiltonian。
source_hamiltonian_artifacts、source_rebaseline_manifest 和独立 approval 共同用于内容 provenance、
矩阵摘要和阶段 2.1 继承检查。
阶段 3 不直接解析 Hamiltonian YAML 的内部字段，不绕过阶段 2 构建自己的 Hamiltonian。
flux scan 只通过阶段 2 的 effective-junction / Hamiltonian 构建接口传入临时 flux override，
不得修改 device_artifacts.json 或 Hamiltonian YAML。
上述收敛容差已由设计负责人采纳为阶段 3 v0.1 基线；阶段 4 正式控制误差预算形成后必须复核。
```

## 输出

本阶段输出目录：

```text
output/stage_03_static_spectrum/
```

默认输出文件：

```text
static_spectrum_artifacts.json
verification.ipynb
```

solver validation 产物：

```text
output/stage_03_solver_validation/dense_pilot.json
output/stage_03_solver_validation/eigsh_validation.json
output/stage_03_solver_validation/eigsh_validation_approval.json
```

默认不保存完整本征向量矩阵。artifact 保存 dressed-state 表、overlap 摘要、mode participation、
收敛结果、flux scan 和 avoided-crossing 指标；如果后续需要缓存本征态，可再加入可选 `.npz`。

## 核心对象

阶段 3 初始对象：

```text
SpectrumConfig
EigenConfig
DressedLabelConfig
ConvergenceConfig
CrossingEvidenceConfig
RuntimeConfig
Stage2RebaselineManifest
Stage2RebaselineApproval
ProvenanceReport
Stage2GapConsistencyReport
SpectrumBuildContext
EigenstateTable
BareStateLabel
BareStateCatalog
DressedStateAssignment
DressedStateTable
StaticMetricTable
ModeParticipationTable
StaticSpectrumPointResult
StaticMetricConvergenceReport
FluxScanConfig
FluxScanPoint
FluxScanResult
AvoidedCrossingCandidate
CrossingConvergenceReport
RuntimeReport
ExecutionPlan
ValidatedSolverSpec
SolverBackendReport
SolverBackendValidationArtifact
SolverBackendValidationApproval
StageGateDecision
StaticSpectrumResult
StaticSpectrumArtifactSet
StaticSpectrumVerificationReport
```

## 公开接口

阶段 3 应暴露以下接口：

```text
load_spectrum_config(path) -> SpectrumConfig
load_stage2_rebaseline_manifest(path) -> Stage2RebaselineManifest
load_stage2_rebaseline_approval(path) -> Stage2RebaselineApproval
validate_spectrum_provenance(config, manifest, approval) -> ProvenanceReport
run_stage2_dense_gap_consistency(config, provenance) -> Stage2GapConsistencyReport
load_solver_backend_validation(path) -> SolverBackendValidationArtifact
load_solver_backend_validation_approval(path) -> SolverBackendValidationApproval
validate_solver_backend(validation, approval, provenance, config) -> SolverBackendReport
build_spectrum_context(config, provenance, solver_backend_report) -> SpectrumBuildContext
rebuild_hamiltonian_for_spectrum(context, flux_overrides_phi0=None, basis_overrides=None) -> HamiltonianModel
solve_static_eigensystem(model, solver_spec: ValidatedSolverSpec) -> EigenstateTable
build_bare_state_catalog(model, label_config) -> BareStateCatalog
assign_dressed_states(eigenstates, bare_catalog, label_config) -> DressedStateTable
compute_mode_participation(eigenstates, bare_catalog, dressed_states) -> ModeParticipationTable
compute_static_metrics(dressed_states, eigenstates, participation) -> StaticMetricTable
analyze_static_point(context, config, flux_overrides_phi0=None, basis_overrides=None) -> StaticSpectrumPointResult
check_static_metric_convergence(config, context, baseline) -> StaticMetricConvergenceReport
scan_coupler_flux(config, context) -> FluxScanResult
build_execution_plan(config, flux_scan, solver_backend_report, elapsed) -> ExecutionPlan
check_crossing_convergence(config, context, flux_scan) -> CrossingConvergenceReport
evaluate_stage3_gate(config, provenance, metric_convergence, flux_scan, crossing_convergence, runtime) -> StageGateDecision
assemble_static_spectrum_result(config, provenance, baseline, metric_convergence, flux_scan, crossing_convergence, runtime, gate) -> StaticSpectrumResult
write_static_spectrum_artifacts(result: StaticSpectrumResult, output_dir) -> StaticSpectrumArtifactSet
verify_static_spectrum(config_path, output_dir) -> StaticSpectrumVerificationReport
```

Solver binding contract:

```text
SolverBackendReport binds the validation artifact, independent approval, Stage 2.1 provenance,
Stage 3 solver source-tree SHA-256, environment fingerprint, and the exact SpectrumConfig eigsh
which / tolerance / maxiter / ncv / v0_rule fields. It exposes an immutable ValidatedSolverSpec;
every acceptance solve receives that object through SpectrumBuildContext.

Solver validation defines near-degenerate blocks from maximal contiguous dense-reference index ranges
whose adjacent gaps are <= 1e-5 GHz. It rejects partition gaps within 2e-6 GHz of that threshold and
rejects a block truncated at the requested-state boundary. Dense/eigsh and repeat projectors use the same
dense index ranges and spectral matrix 2-norm, with limits 1e-6 and 1e-8 respectively.

sha256_counter_v1 uses the detailed design's normative UTF-8/LF/no-BOM seed encoding, fixed q1/c/q2
cutoff text, 12-place Decimal flux text, 8-byte big-endian counter, and normative seed/block-0 digests.

The 1e-9 GHz Stage 2 gap consistency gate calls the Stage 2.1 dense 12-state rebuild path.
It is separate from the 48-state acceptance eigsh baseline. The artifact stores them separately as
provenance.stage2_dense_gap_consistency and acceptance_baseline.eigsh_eigenvalue_gaps_GHz.

ExecutionPlan does not pre-enumerate adaptive keys. It uses conservative remaining-job upper bounds
per cutoff signature and recomputes after baseline coarse scan, every adaptive level, and every refined
signature. The conservative acceptance ceiling is 509 eigensystem solves.
```

接口约定：

```text
能量和频率单位为 GHz。
所有 transition frequency 使用 E_label - E_ground。
所有可观测频率优先看 gaps，不直接解释绝对本征值。
overlap 使用概率，即 |<bare|eigen>|^2。
同一 eigenstate 只能分配给一个 bare label；单点 assignment 使用全局最大 overlap 匹配。
flux scan 使用相邻点 eigenvector overlap 做全局一一匹配，保留 adiabatic branch 连续性。
无法可靠标记的 dressed state 必须给 warning。
flux override 使用 Phi0 单位，且只在内存中生效。
FluxScanResult 必须携带每个 crossing 的最终 bracket、branch identity 和逐点 participation，
CrossingConvergenceReport 只能基于这些显式数据判断，不能重新猜测 branch。
StaticSpectrumVerificationReport.ok 只有在 StageGateDecision.status=ready_for_stage4 时才为 true。
geometry_too_weak 是合法分析结论，但 report.ok=false、CLI 退出码 1、stage4_ready=false。
```

## 用户工作流

阶段 3 继续沿用 VSCode runner 作为主要入口。

推荐流程：

```text
1. 在 VSCode 中打开 scripts/run_stage_03_static_spectrum.py。
2. 点击 Run Python File。
3. 查看终端摘要。
4. 打开 output/stage_03_static_spectrum/verification.ipynb。
5. 人工检查 dressed-state 标记、mode participation、频率、anharmonicity、ZZ、
   charge-cutoff 收敛、flux scan、avoided crossing 和 warning。
```

备用 CLI：

```text
python -m sqvm verify-spectrum configs/spectra/2q1c_static.yaml --output output/stage_03_static_spectrum
```

## 正确性检查

阶段 3 必须提供以下正确性证据：

```text
1. Spectrum config 可以加载和验证。
2. Stage 2.1 approval decision=approved，且绑定的 manifest / artifact SHA-256 完全匹配。
3. source_hamiltonian_artifacts 是 stage_02_hamiltonian，schema_version=0.2 且 artifact_version=0.2。
4. config、device artifact、阶段 2 model source tree 和 stage 2 artifact 的 SHA-256 与 manifest 完全一致。
5. source_hamiltonian_config 可以通过阶段 2 API 重建 Hamiltonian。
6. eigenvalues 有限且升序排列，eigenvectors 归一且正交。
7. bare basis catalog 与 mode_order / charge_cutoff 一致，ground state 可标记为 |000>。
8. 频率、三模 anharmonicity 和三组 ZZ 所需 labels 全部获得一一 assignment；低 overlap 可 warning，缺失则失败。
9. mode participation 的 mean excitation 非负，单激发态 fraction 归一化。
10. 阶段 3 在 spectrum 实现之外单独调用 Stage 2.1 dense 12-state rebuild，
    与阶段 2.1 artifact gaps 差异 <= 1e-9 GHz；失败为 error。
11. 关键频率、anharmonicity 和 ZZ 通过逐模 charge-cutoff refinement 收敛检查。
12. flux scan key、level grid、bracket、端点复用和终止原因可确定性复现，且不修改输入。
13. 相邻 flux 点的 adiabatic branch assignment 是一一映射，最终 bracket continuity 全部达到阈值。
14. 每个 scan 点至少保存 |100>、|010>、|001> 三条 tracked branch 的 q1/c/q2 mode participation。
15. q1-c 和 c-q2 都证明 crossing 前后 character exchange，且 crossing 点目标 mode pair participation 达标。
16. 每个 avoided crossing 都分别提高 q1、c、q2 cutoff，记录 splitting、flux 和 participation 漂移。
17. refined cutoff 通过 bare character 语义锚定 branch，不做跨维 eigenvector overlap；minimum 移出 baseline bracket 则失败。
18. flux drift 使用局部 gap slope 转为 MHz，并计入 U_total。
19. 每个 scan 点保存单模 bare frequencies / detunings，geometry_too_weak 需要有容差化 sign-change 证据。
20. crossing 的 cutoff、flux、level 和已验证 solver 误差满足绝对 / 相对容差与显著性要求。
21. 两个 crossing 都达到 resolved，StageGateDecision 才能为 ready_for_stage4。
22. 未解析但证据表明几何耦合过弱时记录 geometry_too_weak，同时阻塞阶段 4。
23. acceptance 只使用 hash/approval 有效的 validated eigsh，并通过 dimension-specific ExecutionPlan runtime gate。
24. smoke / dense_pilot / solver_validation profiles 不得生成可验收物理结论。
```

严格度：

```text
provenance、结构性检查、gap 重建一致性和必需指标缺失会导致 verify_static_spectrum 失败。
dressed-state overlap、频率范围、anharmonicity / ZZ 数值范围属于物理 sanity，第一版只 warning，不阻塞。
关键指标的 charge-cutoff 收敛属于下游可依赖性检查；超过配置容差会导致
verify_static_spectrum 失败，不能把未收敛的频率或 ZZ 传给控制与校准阶段。
avoided crossing 位于边界、分支连续性不足、没有 character exchange、splitting 不显著、
任一 q1/c/q2 refinement 不收敛或 refinement 未达到终止条件时，candidate 不能标为 resolved。
这些状态可以作为 artifact 中的诊断结论，但 `report.ok=false` 且阶段 4 被阻塞。
```

## verification notebook

`verification.ipynb` 必须只读取 `static_spectrum_artifacts.json`，并展示：

```text
输入来源
Stage 2.1 SHA-256 provenance 与重基线摘要
阶段 2 Hamiltonian 摘要
eigenvalues / gaps 表
低能 spectrum 图
dressed-state assignment 表
bare-state overlap heatmap
mode participation 表和热图
q1/q2/c transition frequency 表
anharmonicity 表
ZZ 类指标表
charge-cutoff convergence 表
coupler flux scan 能级图
逐 flux 点 q1/c/q2 mode participation 曲线
crossing 前后 character exchange 证据
q1-c / c-q2 avoided-crossing 摘要
q1/c/q2 三模式 crossing convergence 表
runtime / solver backend validation / execution plan 摘要
stage gate status 与 stage4_ready
unassigned / low-overlap state warning
checks pass/fail
```

Notebook 只用于人工检查，不作为后续阶段接口。

## 实现任务

第一批实现任务：

```text
1. 先完成并批准 Stage 2.1 rebaseline gate；未通过不得开始本列表后续任务。
2. 新增 acceptance / smoke 两份 spectrum config 和 VSCode runner。
3. 新增 src/sqvm/spectrum/ 模块与统一 StaticSpectrumResult 聚合对象。
4. 实现 spectrum config、rebaseline manifest 和 SHA-256 provenance 校验。
5. 通过阶段 2 API 重建 Hamiltonian，并把最低 12 gaps 一致性作为 error check。
6. 实现 eigensystem、bare catalog、全局 assignment 和逐点 mode participation。
7. 实现频率、anharmonicity、ZZ 与 idle-point 三模式 cutoff 收敛。
8. 实现确定性 flux key、level-specific bracket、端点缓存和 branch tracking。
9. 实现逐 flux 点 participation、character exchange 和 pairwise avoided-crossing 证据。
10. 对每个 crossing 分别提高 q1/c/q2 cutoff，形成 CrossingConvergenceReport。
11. 实现 dense_pilot、solver_validation、smoke、acceptance profiles 和 ExecutionPlan runtime gate。
12. 实现 StageGateDecision；geometry_too_weak / unresolved 都必须阻塞阶段 4。
13. 写出 static_spectrum_artifacts.json 和已执行 verification.ipynb。
14. 补充测试并交独立测试 / 审查 AI。
```

## 测试

必需测试：

```text
加载合法 spectrum config
拒绝缺少 source_hamiltonian_config
拒绝缺少的 Stage 2.1 manifest
拒绝缺少、rejected 或 hash 绑定错误的 Stage 2.1 approval
拒绝任一 SHA-256 与 manifest 不一致
拒绝 num_states 小于 required bare states
重建 Hamiltonian 成功
eigenvalues finite and sorted
eigenvectors normalized and orthogonal
bare catalog 包含 |000>, |100>, |010>, |001>, |110>, |101>, |011>, |200>, |020>, |002>
dressed labeling 可得到 ground / q1 / c / q2 基本态
全局 assignment 不重复使用 eigenstate
强混合构造案例中全局 assignment 优于贪心结果
mode participation 对解耦 product state 给出预期 mean excitation / fraction
transition frequencies 为正
anharmonicity 公式正确
ZZ 指标公式正确
超过收敛容差时 verify 失败
满足收敛容差时 verify 通过
flux override 不修改输入 artifact 或配置对象
flux scan 点序、范围和缓存键正确
flux key 在 12 位小数规则下确定性一致
每一级 bracket 只取该 candidate 上一级 level grid 的相邻点
refinement 端点复用且终止原因确定
相邻点 branch tracking 保持一一映射
合成二能级 avoided crossing 的最小 splitting 和 flux 位置正确
每个 flux 点保存三条单激发 branch participation
character exchange 缺失时 candidate 不得 resolved
q1/c/q2 任一 cutoff refinement 超容差时 crossing 不得 resolved
geometry_too_weak 使 report.ok=false / stage4_ready=false / CLI exit 1
Stage 3 重建 gaps 与 Stage 2.1 artifact 超过 1e-9 GHz 时失败
smoke profile 标记 acceptance_eligible=false
solver validation spec must exactly match the spectrum config
stale Stage 3 solver source-tree hash or environment fingerprint blocks acceptance
sha256_counter_v1 produces a deterministic v0
sha256_counter_v1 matches the normative seed length, seed digest, and block-0 digest
near-degenerate blocks use dense maximal contiguous index ranges and reject ambiguous boundaries
validation rejects a near-degenerate block truncated at num_states
projector dense/repeat errors use spectral matrix 2-norm on the same dense-defined ranges
repeated eigsh runs preserve gaps, near-degenerate projectors, assignment, and participation
the 1e-9 GHz dense Stage 2 gap check is independent of the acceptance eigsh baseline
缺少或 hash / approval 无效的 eigsh validation 时 acceptance 失败
validated eigsh 在 baseline / refined cutoff 与 crossing 区域对 dense 的 gaps、低能子空间和指标一致
ExecutionPlan 分别使用 3375 / 4275 signature p95，预计超预算时失败
ExecutionPlan uses adaptive remaining-job upper bounds and recomputes after every refinement level
the conservative acceptance solve ceiling is 509
写出 static_spectrum_artifacts.json
生成已执行 verification.ipynb
VSCode runner smoke test
CLI verify-spectrum 成功和失败退出码正确
```

## 输出产物

`static_spectrum_artifacts.json` 至少包含：

```text
schema_version
artifact_type = stage_03_static_spectrum
artifact_version = 0.1
source_hamiltonian_config
source_hamiltonian_artifacts
source_rebaseline_manifest
source_rebaseline_approval
provenance
provenance.stage2_dense_gap_consistency
spectrum_config
mode_order
basis
hamiltonian_summary
eigenvalues_GHz
eigenvalue_gaps_GHz
acceptance_baseline.eigsh_eigenvalue_gaps_GHz
dressed_state_assignments
mode_participation
transition_frequencies_GHz
anharmonicities_GHz
zz_metrics_GHz
overlap_summary
numerical_convergence
flux_scan
avoided_crossings
crossing_convergence
solver_backend_validation
solver_backend_validation_approval
stage3_solver_source_tree_sha256
environment_fingerprint_sha256
runtime
stage_gate
warnings
checks
```

## 验收标准

阶段 3 完成条件：

```text
1. VSCode 中运行 scripts/run_stage_03_static_spectrum.py 可以成功。
2. output/stage_03_static_spectrum/ 下生成 static_spectrum_artifacts.json 和 verification.ipynb。
3. 能谱、dressed labels、mode participation、频率、anharmonicity 和 ZZ 指标在 notebook 中可检查。
4. Stage 2.1 manifest 和所有内容 SHA-256 验证通过，idle gaps 一致性达到 1e-9 GHz。
5. 关键静态指标通过配置定义的 charge-cutoff 收敛门。
6. q1-c / c-q2 两个 crossing 都满足 interior、continuity、character exchange、
   participation、三模式 cutoff convergence、显著性和 refinement 终止条件，status=resolved。
7. acceptance 使用已批准的 validated eigsh 并在预算内完成；任何 pilot / validation / smoke 结果不作为物理验收证据。
8. StageGateDecision.status=ready_for_stage4，report.ok=true，stage4_ready=true。
9. 阶段 3 测试通过，CLI 成功 / 失败退出码正确。
10. 独立测试 / 审查 AI 按本计划和详细设计给出通过记录。
11. 开发日志记录实现结果。
12. 用户检查并认可 verification.ipynb 后，再进入阶段 4。
```

## 开放问题

阶段 3 第一版暂不解决但必须记录：

```text
1. 若实际结果为 geometry_too_weak，阶段 3 保留完整诊断 artifact 但验收失败并阻塞阶段 4；
   用户必须决定调整器件几何，或正式修改项目物理目标和路线图。
2. 第一版用全局 overlap 和相邻点 eigenvector overlap 跟踪；多参数闭环中的 Berry phase、
   真简并和复杂分支拓扑不在本阶段处理。
3. 本征态缓存默认不落盘；若重复扫描性能不足，再增加带 provenance 的可选 npz。
4. 是否把阶段 3 指标作为后续校准数据库的一部分，留到实验运行阶段决定。
5. 阶段 3 v0.1 使用 0.50 MHz frequency、1.00 MHz anharmonicity、0.01 MHz ZZ/crossing、
   crossing 5% relative 和 participation 0.02；阶段 4 正式误差预算形成后必须复核。
```
