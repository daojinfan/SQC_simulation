# 阶段 3 详细设计：静态能谱与 dressed-state 分析

## 1. 设计目标

阶段 3 的目标是把阶段 2 的 `2q1c` Hamiltonian 变成可解释的静态物理表征。

阶段 2 已经回答：

```text
如何构建 Hamiltonian
Hamiltonian 是否 Hermitian
最低 eigenvalues 是否可求
```

阶段 3 要回答：

```text
每个低能本征态主要对应哪个 bare product state
q1/q2/c 的 dressed 频率是多少
非谐性是多少
可获得的 residual ZZ 类指标是多少
当前 demo 小耦合是否在结果中显式可见
```

本阶段仍然是静态分析，不进入脉冲控制和时间演化。

## 2. 阶段边界

本阶段依赖阶段 2 的公开接口：

```text
load_hamiltonian_config
load_device_artifacts
build_mode_transform
build_mode_capacitance_matrix
build_ec_matrix
resolve_effective_junctions
build_hamiltonian
```

本阶段允许重建 Hamiltonian 并求 eigenvectors，因为阶段 2 artifact 默认不保存完整矩阵或本征态。

本阶段不直接解析 `device.yaml`。`source_hamiltonian_config` 只作为阶段 2 API 的输入，由阶段 2 的 `load_hamiltonian_config` 和构建接口处理；阶段 3 不读取 Hamiltonian YAML 的内部字段，不绕过阶段 2 自己构建 Hamiltonian。

阶段 3 实现前必须通过 `docs/stages/02_1_hamiltonian_rebaseline_plan.md`。阶段 3 只接受：

```text
stage_02_hamiltonian schema_version = 0.2 且 artifact_version = 0.2
stage_02_1_hamiltonian_rebaseline manifest
decision=approved 且绑定 manifest / artifact SHA-256 的独立 rebaseline approval
manifest 中与当前文件 bytes 完全一致的 SHA-256
```

只比较 path、basis 或 solver 字段不足以建立 provenance。阶段 3 必须重新计算 Hamiltonian config、
device artifact、阶段 2 model source tree 和阶段 2 artifact 的 SHA-256，并与 manifest 完全比较。

阶段 3 需要扫描 coupler flux。为避免修改阶段 1 artifact 或复制阶段 2 的 SQUID 公式，
阶段 2 的公开接口做一个向后兼容扩展：

```python
resolve_effective_junctions(
    device_artifacts,
    flux_bias_overrides_phi0: Mapping[str, float] | None = None,
) -> tuple[EffectiveJunction, ...]
```

不传 override 时行为与阶段 2 完全相同。override 只在当前函数调用中生效，不修改
`DeviceArtifacts.payload`、配置对象或磁盘文件。阶段 3 只允许 target=`c`。

## 3. 输入配置

第一版配置文件：

```text
configs/spectra/2q1c_static.yaml
```

推荐结构：

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

### 3.1 num_states

`num_states` 表示阶段 3 求解并保存的低能本征态数量。

第一版默认：

```text
num_states = 48
```

原因：

```text
需要覆盖 |000>, |100>, |010>, |001>, |110>, |101>, |011>, |200>, |020>, |002> 等态。
Stage 2.1 第一候选 cutoff 为 q1=7、c=7、q2=7，demo Hilbert dimension 为
`15 * 15 * 15 = 3375`。三个 N=5 -> N=7 已知漂移都超过暂定 0.50 MHz frequency budget。
dense eigh 仅用于基准和 solver validation；完整扫描必须受第 3.6 节运行预算约束。
```

### 3.2 max_excitations

`max_excitations` 和 `max_total_excitations` 共同控制需要分配的低能 bare labels。

第一版默认：

```text
q1: 2
c : 2
q2: 2
max_total_excitations: 2
```

目标 label 必须同时满足每个模式不超过自己的上限，且总激发数不超过 2。第一版恰好覆盖：

```text
|000>
|100>, |010>, |001>
|200>, |020>, |002>, |110>, |101>, |011>
```

不把 `|222>` 等高总激发态强行分配给前 48 个 eigenstates。`num_states` 至少大于目标 label 数量，
但是否可靠覆盖仍由实际 overlap 检查决定。mode participation 使用完整有限维单模本征基，不受该目标 label 截断影响。

但 artifact 可以只突出低能和指标所需的子集。

### 3.3 min_overlap

`min_overlap` 是 dressed-state assignment 的最低可信 overlap。

第一版默认：

```text
min_overlap = 0.50
```

若某个目标 bare label 找到的最大 overlap 低于该阈值：

```text
assignment 仍记录
check 给 warning
notebook 标红或标注 low overlap
```

`continuity_min_overlap` 用于 flux scan 相邻点的 adiabatic branch tracking。低于该值时分支仍记录，
但必须产生 warning，避免把跨大步长或近简并下的不可靠连接解释为连续物理分支。

### 3.4 convergence

阶段 3 输出会被控制与校准阶段直接使用，因此关键静态指标必须通过数值收敛门。

```text
cutoff_increment = 2
```

单点频率、anharmonicity 和 ZZ 在 idle flux 上分别提高 q1、c、q2 cutoff，记录每个输出指标的漂移。

每个 avoided crossing 也必须分别提高 q1、c、q2，不能把未直接参与 pair label 的模式预设为 spectator 并跳过。

cutoff 改变后 Hilbert 空间维度不同，禁止用 baseline / refined eigenvector 直接做跨维 overlap。
每个 refined model 都在固定 baseline character evidence 区间
`[character_evidence_left_key, character_evidence_right_key]` 上独立重建：

```text
1. 精确求解 baseline character evidence left / right keys。
2. 在 left key 用局部 bare participation 把两条 branch 语义锚定为 A_like / B_like；
   主 character 必须达到 endpoint_character_min_fraction。
3. 只在同一个 refined Hilbert 空间内，用相邻 eigenvector overlap 从 left 跟踪到 right。
4. refined 初始 grid 是 evidence 区间 17 点均匀网格与以下固定 keys 的 union：
   evidence left / right、baseline final bracket left / minimum / right；去重后排序。
   因此 baseline crossing key 必定被 refined model 精确求解。随后再做最多 4 级局部 refinement。
5. 比较 semantic A_like / B_like branches 的 splitting、crossing flux 和 participation；
   不比较跨维 eigenvector 本身。
```

refined minimum 必须仍位于 baseline final bracket 内。若移出 final bracket 但仍在 evidence 区间，
状态为 `cutoff_shift_outside_baseline_bracket` 并失败；若移出 evidence 区间，状态为
`refined_crossing_outside_evidence_domain` 并失败。

对每个 `(candidate, refined_mode)` 记录：

```text
splitting drift
crossing flux drift
固定 character evidence left / crossing / right participation fraction drift
character exchange 是否保持
semantic branch anchor participation
minimum 是否仍位于 baseline final bracket
```

保守 splitting cutoff 不确定度定义为三个模式绝对漂移之和：

```text
U_cutoff = |delta_q1| + |delta_c| + |delta_q2|
```

crossing flux drift 转换为能量误差：

```text
delta_phi_i = |phi_refined_i - phi_baseline|
对 baseline 和 refined 各自 final refinement level，minimum index 必须为内部 j：
  slope_left  = |gap[j]   - gap[j-1]| / |phi[j]   - phi[j-1]|
  slope_right = |gap[j+1] - gap[j]  | / |phi[j+1] - phi[j]|
  S_model = max(slope_left, slope_right)
S_i = max(S_baseline, S_refined)，单位 MHz / Phi0
U_flux_i = S_i * delta_phi_i
U_flux = |U_flux_q1| + |U_flux_c| + |U_flux_q2|
```

`gap` 固定为该 candidate 两条 semantic branches 的能量间隔（MHz），stencil 固定使用各自 final level grid
中 minimum 左右紧邻 key，不能使用 merged grid 或拟合导数。minimum 非内部、相邻 key 重复或分母为零时
`avoided_crossing_flux_converged=false`。

总 splitting 不确定度为：

```text
U_total = max(U_cutoff, U_flux, level_to_level_drift, validated_solver_error)
```

resolved 必须同时满足：

```text
U_total <= avoided_crossing_absolute_tolerance_MHz
U_total / splitting_MHz <= avoided_crossing_relative_tolerance
splitting_MHz / max(U_total, 1e-12) >= splitting_significance_min_ratio
U_flux <= avoided_crossing_absolute_tolerance_MHz
participation fraction 最大漂移 <= participation_fraction_tolerance
所有 refinement 下 character exchange 结论一致
```

任一必需指标超过容差时 `verify_static_spectrum.ok = false`。这与阶段 2 的 warn-only 探索性检查不同：
阶段 3 已经要向下游交付物理指标。

暂定误差预算依据：

```text
frequency 0.50 MHz：32 ns 控制窗口内相位误差约 2*pi*0.50 MHz*32 ns = 0.10 rad。
anharmonicity 1.00 MHz：相对典型约 200 MHz 非谐性为 0.5%，用于控制 / leakage 初始模型。
ZZ 0.01 MHz：200 ns 窗口内额外条件相位约 0.013 rad。
crossing 0.01 MHz 且 5% relative：同时限制绝对相位误差和小 splitting 的相对误差。
```

这些是阶段 4 尚未设计完成前的保守代理预算，不是来源于已有门保真度规格。
设计负责人已将其采纳为阶段 3 v0.1 基线；阶段 4 形成正式 pulse duration / phase error /
gate error budget 后必须复核，若新预算更严格则重新触发阶段 3 数值验收。

### 3.5 flux_scan

第一版强制扫描 coupler flux，默认范围覆盖 coupler 从高于 q1/q2 到低于 q1/q2 的区域。

```text
target = c
start_phi0 < stop_phi0
coarse_points >= 9 且为奇数
min_refinement_levels >= 1
max_refinement_levels >= min_refinement_levels
refinement_points >= 7 且为奇数
flux_key_decimal_places = 12
```

flux 不直接使用 binary float 作为 cache key。固定规则：

```text
flux_key = Decimal(str(phi)).quantize(Decimal("1e-12"), rounding=ROUND_HALF_EVEN)
```

cache key 必须包含：

```text
(q1_cutoff, c_cutoff, q2_cutoff, flux_key, num_states, solver_backend)
```

coarse grid 在十进制有理数域等分，首尾 key 必须精确等于 start / stop。两个 candidate 按固定顺序
`q1-c`, `c-q2` 序列化，但每一级同时生成新网格、取 union、求解缺失 key，再统一重做 branch tracking，
避免处理顺序改变结果。

对每个 candidate，level 0 是 coarse grid。level `L+1` 的 bracket 只使用该 candidate 在 level `L`
自己的 grid 中 minimum key 左右相邻点，不能使用全局 merged grid 的任意最近点。新 grid 包含 bracket 两端，
两端直接复用缓存；中间点按十进制规则生成。每一级必须保存：

```text
level
input_bracket_keys
grid_keys
minimum_key
minimum_splitting_MHz
level_to_level_drift_MHz
local_energy_resolution_MHz
termination_reason
```

refinement 至少执行 `min_refinement_levels`。之后只有同时满足下列条件才能以 `converged` 终止：

```text
minimum 不是当前 level grid 的端点
连续两级 splitting drift <= splitting_level_tolerance_MHz
bracket 半宽 * 两 branch gap 的最大局部斜率 <= flux_energy_resolution_MHz
没有产生重复 key 导致的分辨率耗尽
```

到达 `max_refinement_levels` 仍不满足则 `resolution_limit`；minimum 在 level grid 端点则 `boundary`；
量化后少于 3 个唯一 key 则 `key_resolution_exhausted`。三者均不能 resolved。

### 3.6 execution profile 与运行预算

Stage 2.1 cutoff 下 dimension 为 3375。默认 base scan 最多新增：

```text
25 + 2 candidates * 8 levels * (11 - 2 cached endpoints) = 169 eigensystem solves
```

For each (candidate, refined_mode), the initial key set is upper-bounded by 17 uniform evidence-grid keys
plus 3 forced baseline final-bracket left/minimum/right keys, followed by at most 9 new keys at each of 4 levels.
Thus refined solves are bounded by 2 * 3 * ((17 + 3) + 4 * 9) = 336; with base scan, three idle refinements,
and one acceptance baseline idle solve, the conservative ceiling is 169 + 336 + 3 + 1 = 509 solves.
acceptance profile 预算为 1800 秒。

不能用 3375 维 baseline p95 估算 4275 维 refined solve。运行计划按 cache signature / dimension 分组，
分别使用 solver validation artifact 中对应维度和近简并区域的 p95。adaptive 后续 key 取决于前一级 minimum，
不能提前枚举。`ExecutionPlan` 使用按 signature 的保守 remaining-job upper bound：

```text
base candidate remaining = active_candidates * remaining_levels * (refinement_points - 2)
refined candidate remaining = active_refinements * remaining_levels * (refinement_points - 2)
refined forced-union remaining = active_refinements * up_to_3_uncached_baseline_bracket_keys
再加尚未执行的固定 coarse / evidence / idle jobs，并扣除 cache 中已完成 keys。
```

在 baseline coarse 完成、每个 adaptive level 完成、每个 refined signature 完成后都必须重新计算：

```text
elapsed_seconds
completed_jobs_by_cutoff_signature
remaining_job_upper_bound_by_signature
p95_seconds_by_signature
projected_remaining_seconds
projected_total_seconds
budget_seconds
```

`projected_total_seconds > 1800` 时直接 `runtime_budget_exceeded`；不得减少精度或临时切换 backend。

提供两个明确 profile：

```text
acceptance:
  使用已批准的 validated_eigsh、正式 cutoff、全部 convergence 和完整 scan。
  只有该 profile 可以生成可验收 artifact。

smoke:
  configs/spectra/2q1c_static_smoke.yaml
  预算 90 秒，只验证配置、重建、缓存、assignment、artifact / notebook plumbing。
  acceptance_eligible = false，不得作为物理验收证据。

dense_pilot:
  使用 dense eigh、num_states=12 和非验收 scan，定位两个 candidate 的 approximate bracket / character domain。
  只为 solver validation 选点，acceptance_eligible = false。

solver_validation:
  在 dense_pilot 给出的 idle / start / stop、两个 crossing 的 evidence left / minimum / right，
  对 baseline 3375 和 q1/c/q2 三个 refined 4275 模型同时运行 dense eigh 与 eigsh。
  生成 solver validation artifact，acceptance_eligible = false。
```

acceptance backend 固定为 `validated_eigsh`：

```text
validated_eigsh:
  显式 which="SA"、tol、maxiter；不得依赖 scipy 默认。
  ncv 固定为 97，必须满足 ncv > 2 * num_states。
  v0_rule 固定为 sha256_counter_v1。
  validation 覆盖 baseline / 三个 refined cutoff，以及 idle、scan endpoints、crossing evidence / minimum。
  最低 48 gaps 最大差 <= 1e-6 GHz。
  近简并块使用子空间 projector error <= 1e-6，不逐向量比较任意旋转。
  dressed labels、participation 和静态指标结论一致。
```

Near-degenerate projector validation is byte- and index-deterministic:

```text
1. For a requested num_states=N, dense reference returns N+1 sorted eigenpairs E[0..N], V[0..N].
2. For i=0..N-2, dense indices i and i+1 belong to the same block iff
   E[i+1]-E[i] <= near_degenerate_gap_threshold_GHz.
   Blocks are the maximal contiguous components of that rule, including singleton blocks.
3. If any dense adjacent gap used for partitioning differs from the threshold by
   <= partition_boundary_margin_GHz, fail near_degenerate_partition_ambiguous.
4. If E[N]-E[N-1] <= threshold + margin, fail validation_block_truncated; no accepted block may
   cross the N-state output boundary.
5. Sort each eigsh run by ascending eigenvalue. After all first-N energy gaps relative to state 0 pass
   gap_dense_tolerance_GHz, dense block [a,b] corresponds exactly to eigsh columns [a,b].
6. For each block, P_dense=V_dense[:,a:b+1] @ V_dense[:,a:b+1]^H and likewise P_eigsh.
   projector error is ||P_dense-P_eigsh||_2, the largest singular value (spectral matrix 2-norm).
7. Dense/eigsh error must be <= projector_dense_tolerance. Repeatability uses the same dense-defined
   [a,b] blocks and requires ||P_eigsh_run1-P_eigsh_run2||_2 <= projector_repeat_tolerance.
```

`sha256_counter_v1` 确定性初始向量：

```text
cutoff_text = "q1=<q1_decimal>,c=<c_decimal>,q2=<q2_decimal>" in that exact order;
each cutoff is unsigned base-10 ASCII with no sign or leading zero except the value 0.
flux_text is the Decimal flux key after ROUND_HALF_EVEN quantization to 12 places, rendered with
exactly 12 fractional ASCII digits; negative zero is canonicalized to 0.000000000000.
num_states_text is unsigned base-10 ASCII with no sign or leading zero.
stage2_artifacts_sha256 is exactly 64 uppercase hexadecimal ASCII characters.

seed_text is the exact concatenation, with LF (0x0A) after every line including the last:
sqvm-eigsh-v0-v1
stage2_artifacts_sha256=<stage2_artifacts_sha256>
cutoff=<cutoff_text>
flux_phi0=<flux_text>
num_states=<num_states_text>

seed = UTF-8(seed_text), ASCII subset, no BOM and no CR.
For block_index=0,1,... compute SHA256(seed || uint64_be(block_index)); uint64_be is exactly 8 bytes.
Consume each 32-byte digest from byte 0 in non-overlapping 8-byte big-endian uint64 words.
For each u use x = 2 * ((u >> 11) / 2^53) - 1, append float64 values in digest/block order until
dimension values exist, discard surplus words, then normalize once with float64 L2 norm.

Norm zero/non-finite is v0_generation_failed. No JSON, tuple repr, locale formatting, platform newline,
hash-hex bytes, or pre-hashing of seed is permitted.

Normative test vector:
stage2_artifacts_sha256=222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C
cutoffs=(7,7,7), flux=0.2, num_states=48
seed_text UTF-8 length=166
SHA256(seed)=ED780C6FA5C04949197BEE6D43156B10391B1E6E3596F9CFA41D80D455FBF1A7
SHA256(seed || uint64_be(0))=2601A3794F3CD9A73B4B6B2D4DDDB15E3FC890D8593B2752672D366E83EA2950
```

validation 每个 case 必须连续运行 eigsh 两次，使用同一 v0 rule，并满足：

```text
gaps repeat max difference <= 1e-10 GHz
近简并块 projector repeat error <= 1e-8
assignment / character / participation status 完全一致
```

solver validation 使用机器可读 artifact 和独立 approval：

```text
eigsh_validation.json:
  schema_version = 0.1
  artifact_type = stage_03_solver_backend_validation
  artifact_version = 0.1
  spectrum_config_sha256 / Stage 2.1 provenance hashes / dense and eigsh configs
  stage3_solver_source_tree_sha256
  environment_fingerprint
  v0_canonical_encoding_version / normative_test_vector_passed
  near_degenerate_gap_threshold_GHz / partition_boundary_margin_GHz / projector_error_norm
  dense_pilot_sha256 / validation_cases / per-case gaps and projector errors
  per-case dense_block_index_ranges / boundary_gaps / truncation_status / projector spectral norms
  assignment / participation / metric comparisons
  p50 / p95 by cutoff signature and dimension
  validation_passed

eigsh_validation_approval.json:
  schema_version = 0.1
  artifact_type = stage_03_solver_backend_validation_approval
  artifact_version = 0.1
  decision = approved | rejected
  validation_artifact_sha256
  reviewer_role = independent_test_review_ai
  blocking_findings
```

acceptance 启动前必须加载并验证 artifact / approval 的 schema、hash、decision 和 Stage 2.1 provenance。
还必须重新计算并严格匹配：

```text
stage3_solver_source_tree_sha256：src/sqvm/spectrum/**/*.py 按 Stage 2.1 tree-hash 算法计算。
environment_fingerprint：Python / NumPy / SciPy versions、BLAS vendor/config digest、
                         platform system/release/machine 的 canonical JSON SHA-256。
```

validation artifact 内的 eigsh spec 必须与 SpectrumConfig 完全相等，并由 `ValidatedSolverSpec` 强类型承载。
缺失、过期、环境/source hash 不匹配或未批准时 `solver_backend_unvalidated`；不允许自动退回 dense 完整扫描。
所有 profile 记录 wall time、每次 solve 时间、p50 / p95、solver evaluations、cache hits 和 backend 来源。

## 4. Bare Basis 与 Dressed Basis

完整 eigensystem 由阶段 3 自己求解，不改变阶段 2 的 `solve_lowest_eigenvalues`。dense reference 路径为：

```python
values, vectors = scipy.linalg.eigh(
    model.matrix.toarray(),
    subset_by_index=[0, num_states - 1],
    eigvals_only=False,
)
```

`vectors[:, k]` 对应 `values[k]`。`num_states` 必须不少于目标 bare labels 数量，且小于 Hilbert dimension。
本征向量整体符号不具有物理意义，所有标记和连续性只使用 overlap probability。
acceptance 路径使用已批准的 `eigsh(which="SA")`，返回相同的 `EigenstateTable`；
若 solver validation 无效，不能进入 acceptance。

阶段 2 的 Hamiltonian 使用 charge basis：

```text
|n_q1, n_c, n_q2>
```

但静态表征需要以每个模式的单模本征态作为 bare basis：

```text
|l_q1, l_c, l_q2>
```

其中 `l_i` 是单个 transmon 模式的本征态编号，不是 charge number。

### 4.1 单模 bare states

对每个模式 `i`，使用阶段 2 的：

```text
E_C,ii
EJ_eff,i
charge_cutoff_i
```

构建单模 Hamiltonian：

```text
H_i = 4 E_C,ii n_i^2 - EJ_eff,i cos(phi_i)
```

求解单模本征态：

```text
|0_i>, |1_i>, |2_i>, ...
```

这些单模本征态张量积形成 bare product basis：

```text
|l_q1, l_c, l_q2> = |l_q1> ⊗ |l_c> ⊗ |l_q2>
```

### 4.2 Dressed assignment

对完整 Hamiltonian 的每个 eigenstate：

```text
|psi_k>
```

计算 overlap：

```text
P(label, k) = |<bare_label | psi_k>|^2
```

目标 labels 按“总激发数、再按 `(q1, c, q2)` 字典序”稳定排序，形成 overlap 矩阵 `P`。
使用 `scipy.optimize.linear_sum_assignment(-P)` 求全局最大总 overlap 的一一分配：

```text
每个 target label 最多分配一个 eigenstate。
每个 eigenstate 最多被一个 target label 占用。
overlap < min_overlap 时保留 assignment，但 status = low_overlap 并给 warning。
目标数大于已求 eigenstates 数量时配置校验直接失败，不进入 assignment。
```

不采用逐 label 贪心。贪心在近简并和 avoided crossing 附近会因处理顺序产生不稳定结果，
不满足阶段 3 的唯一标记要求。

### 4.3 Mode participation

单模 Hamiltonian 的全部有限维本征向量组成 `U_q1`、`U_c`、`U_q2`。它们的 Kronecker product
把完整 eigenstate 从 charge basis 变换到 bare energy basis：

```text
beta[l_q1, l_c, l_q2; k] = <l_q1, l_c, l_q2 | psi_k>
```

对每个 dressed eigenstate 定义各模式平均激发数：

```text
nbar_i(k) = sum_l l_i * |beta[l; k]|^2
```

并定义归一化 participation fraction：

```text
fraction_i(k) = nbar_i(k) / sum_j nbar_j(k)
```

当总平均激发数小于 `1e-12`（通常为 ground state）时，fraction 记为 `null`，不做除法。
实现可以用 reshape / tensor contraction，不能为每个 label 构造并长期保存完整 Kronecker 向量矩阵。

## 5. 频率与指标定义

所有能量单位为 GHz。

基态 label：

```text
|000>
```

### 5.1 Dressed transition frequencies

```text
f_q1 = E_100 - E_000
f_c  = E_010 - E_000
f_q2 = E_001 - E_000
```

### 5.2 Anharmonicity

```text
alpha_q1 = (E_200 - E_100) - (E_100 - E_000)
alpha_c  = (E_020 - E_010) - (E_010 - E_000)
alpha_q2 = (E_002 - E_001) - (E_001 - E_000)
```

若 `|200>`、`|020>` 或 `|002>` assignment overlap 低于阈值，则对应 alpha 给 warning；
任一必需 label 缺失时 `required_metric_labels_assigned` 失败。

### 5.3 Residual ZZ

第一版定义 q1-q2 dressed ZZ：

```text
ZZ_q1q2 = E_101 - E_100 - E_001 + E_000
```

这里 label 顺序为：

```text
|q1, c, q2>
```

注意：

```text
当前 demo 的 q1-c / c-q2 耦合很小，ZZ 可能接近零。
这不是计算失败，而是阶段 2 已确认的几何结果。
```

同一阶段同时输出 coupler 相关 ZZ 指标：

```text
ZZ_q1c = E_110 - E_100 - E_010 + E_000
ZZ_cq2 = E_011 - E_010 - E_001 + E_000
```

### 5.4 Avoided crossing

阶段 3 第一版必须实现 coupler flux scan。每个 flux 点都通过内存 override 重建 Hamiltonian，
求 `num_states` 个 eigenpairs，并完成单点 bare assignment 和 mode participation。

相邻 flux 点使用 eigenvector overlap 保持 adiabatic branch 连续：

```text
Q[a, b] = |<psi_a(phi_prev) | psi_b(phi_next)>|^2
```

对 `-Q` 使用全局线性分配，得到一一 branch mapping。第一点的 branch id 由其 bare assignment 初始化；
未获得目标 bare label 的分支使用稳定 id `eigen_<index>`。每一级新点求解后，将缓存点合并、按 flux key 排序，
再从头执行 branch tracking，避免插点顺序影响结果。

第一版报告两个候选：

```text
q1-c: branches seeded by |100> and |010>
c-q2: branches seeded by |010> and |001>
```

每个 flux key 同时计算单模 bare transition frequencies：

```text
f_q1_bare(phi) = E1_q1 - E0_q1
f_c_bare(phi)  = E1_c(phi) - E0_c(phi)
f_q2_bare(phi) = E1_q2 - E0_q2

Delta_q1_c_bare(phi) = f_c_bare(phi) - f_q1_bare(phi)
Delta_c_q2_bare(phi) = f_c_bare(phi) - f_q2_bare(phi)
```

bare detuning sign change 不是简单看浮点正负。令
`detuning_sign_tolerance_MHz = frequency_tolerance_MHz`，只有 candidate evidence 区间内同时存在
`Delta <= -tolerance` 和 `Delta >= +tolerance` 才记为 `bare_detuning_sign_change=true`。
每个 flux 点和 candidate summary 都必须保存 bare frequencies、detuning、极值和 sign-change evidence keys。

候选 splitting 定义为扫描范围内两个 adiabatic branch gap 的最小值：

```text
splitting_GHz = min_phi |E_branch_a(phi) - E_branch_b(phi)|
```

每个候选必须记录：

```text
branch_a / branch_b
flux_bias_phi0_at_min
splitting_GHz / splitting_MHz
local_flux_step_phi0
refinement_levels_completed
minimum_is_interior
minimum_branch_overlap
final_bracket
character_evidence_keys
per_flux_mode_participation
character_exchange
crossing_convergence
status
```

`per_flux_mode_participation` 至少对 tracked `|100>`、`|010>`、`|001>` 三条 branch，在每个 evaluated flux key 保存：

```text
mean_excitations: {q1, c, q2}
fractions: {q1, c, q2}
target_pair_fraction
spectator_fraction
```

final bracket 只用于最小 splitting 数值细化，不直接作为 character exchange 窗口。对每个 candidate，
从 minimum 向左 / 右沿 merged flux grid 确定性搜索最近的 evidence key，使两条 branch 的 target-pair fraction
和主 character fraction 达到配置阈值；若任一侧找不到，则 `character_exchange_failed`。

对 candidate modes `(A, B)`、两条 adiabatic branches `(u, v)` 和固定的
`character_evidence_left / minimum / character_evidence_right`，character exchange 必须满足：

```text
1. 两个 evidence key 都存在 participation，且 left < minimum < right。
2. evidence left 每条 branch 的主要 character fraction >= endpoint_character_min_fraction。
3. 到 evidence right 时两条 branch 的 A/B character 对调。
4. 对每条 branch，(fraction_A - fraction_B) 在 evidence left / right 异号，且变化绝对值 >= character_exchange_min_delta。
5. minimum 点两条 branch 的 A+B fraction 都 >= target_pair_min_fraction_at_crossing。
6. 上述结论在 q1/c/q2 三个 cutoff refinement 下保持。
```

candidate 只有同时满足以下全部条件才能 `status=resolved`：

```text
minimum 在 scan 和最终 level grid 内部
refinement termination_reason = converged
final bracket 与 character evidence 区间内所有 branch continuity overlap >= continuity_min_overlap
character exchange 全部通过
逐 flux 点 participation 完整
q1/c/q2 三个 cutoff refinement 全部完成
splitting / flux / participation 满足第 3.4 节收敛门
splitting significance ratio 达标
runtime 和 solver backend 有效
```

其他状态：

```text
geometry_too_weak:
  bare_detuning_sign_change=true，provenance / solver / cutoff / refinement 都有效，
  continuity 和 target-pair participation 有效，但 splitting significance 不达标，且 character 变化幅度
  同时低于阈值，证据一致指向器件耦合过弱。

boundary:
  minimum 位于 scan 或 level grid 边界。

low_continuity:
  最终 bracket branch overlap 低于阈值。

character_exchange_failed:
  splitting 显著或其他弱耦合前提不成立，但 participation 仍不支持 A/B character 对调；
  不能把该状态归因于 geometry_too_weak。

multimode_overlap:
  target pair participation 不足，第三模式参与使 pairwise crossing 解释不成立。

numerically_unconverged:
  任一 q1/c/q2 cutoff refinement 超容差。

cutoff_shift_outside_baseline_bracket:
  refined crossing minimum 仍在 character evidence 区间，但移出 baseline final bracket。

refined_crossing_outside_evidence_domain:
  refined model 在固定 character evidence 区间内找不到内部 minimum。

resolution_limit / key_resolution_exhausted / runtime_budget_exceeded:
  对应第 3.5 / 3.6 节终止原因。
```

状态判定按固定优先级执行：runtime -> numerical convergence / cutoff-domain failure -> boundary / resolution -> continuity ->
multimode overlap -> resolved 全条件 -> geometry_too_weak 谓词 -> character_exchange_failed。
artifact 同时保存所有 failed predicates，最终 status 只取最高优先级，保证相同数据得到相同分类。

`geometry_too_weak` 是有效诊断，不是阶段成功。只要任一 q1-c / c-q2 candidate 不是 resolved：

```text
StageGateDecision.stage4_ready = false
StaticSpectrumVerificationReport.ok = false
CLI exit code = 1
```

用户随后只能选择调整器件几何并重新走 Stage 1/2/2.1，或正式修改项目物理目标；不得直接进入阶段 4。

## 6. 正确性检查

### 6.1 结构性 checks

失败则 `verify_static_spectrum.ok = false`：

```text
source_hamiltonian_artifacts_type
source_hamiltonian_schema_version_0_2
source_hamiltonian_artifact_version_0_2
stage2_rebaseline_approval_valid
hamiltonian_config_sha256_matches
device_artifacts_sha256_matches
stage2_model_source_tree_sha256_matches
stage2_artifacts_sha256_matches
hamiltonian_rebuild_success
stage2_gap_consistency_error
eigenvalues_finite
eigenvalues_sorted
eigenvectors_normalized
eigenvectors_orthogonal
bare_catalog_contains_required_labels
dressed_assignment_unique
dressed_ground_assigned
required_metric_labels_assigned
mode_participation_finite_nonnegative
mode_participation_single_excitation_normalized
flux_override_input_immutable
flux_scan_range_and_order
flux_key_deterministic
refinement_brackets_deterministic
flux_branch_assignment_unique
avoided_crossing_payload_complete
per_flux_mode_participation_complete
runtime_profile_valid
```

`stage2_gap_consistency_error` 明确调用 Stage 2.1 的 dense 12-state rebuild 路径，容差 `1e-9 GHz`；
它不读取 acceptance eigsh 的 48-state baseline。artifact 分开保存：

```text
provenance.stage2_dense_gap_consistency
acceptance_baseline.eigsh_eigenvalue_gaps_GHz
```

eigsh 只需满足 solver validation 的 `1e-6 GHz` dense 对照容差；两种容差服务不同契约，不互相替代。

### 6.2 物理 sanity checks

失败只 warning，不阻塞：

```text
dressed_overlap_above_threshold
transition_frequency_range
anharmonicity_range
zz_metric_available
demo_coupling_small_warning
```

建议宽松范围：

```text
transition frequency: 0.1–20 GHz
anharmonicity: -2–0 GHz for q1/q2/c transmon-like modes
ZZ: 不设硬范围，只展示 MHz 和 GHz
```

### 6.3 数值收敛 checks

以下检查失败会导致 `verify_static_spectrum.ok = false`：

```text
transition_frequency_converged
anharmonicity_converged
zz_metric_converged
avoided_crossing_splitting_converged
avoided_crossing_flux_converged
avoided_crossing_participation_converged
avoided_crossing_all_modes_refined
```

基准配置当前 coupler `N=5 -> N=7` 的单模 gap 漂移约 31.7 MHz，已超过阶段 3 暂定频率容差。
因此开发 AI 不得通过放宽容差让默认示例通过；基准 Hamiltonian cutoff 的第一候选为
`q1=7, c=7, q2=7`（Hilbert dimension 3375）。应重新生成阶段 2 artifact，
若仍未收敛，再根据逐模漂移提高对应 cutoff，
以 `N -> N+2` 证明新基准收敛。任何 cutoff 调整必须同时记录运行时间和 Hilbert dimension。

### 6.4 Stage gate checks

以下条件全部成立才允许 `ready_for_stage4`：

```text
provenance_passed
stage2_gap_consistency_error passed
static_metrics_converged
q1_c_crossing_status = resolved
c_q2_crossing_status = resolved
acceptance_profile_completed_within_budget
```

`geometry_too_weak`、`boundary`、`low_continuity`、`character_exchange_failed`、`multimode_overlap`、
`numerically_unconverged`、`resolution_limit` 和 `runtime_budget_exceeded` 都使 stage gate 失败。
其中 geometry_too_weak 可以保留 `analysis_completed=true`，但不得让 `ok` 或 `stage4_ready` 为 true。

## 7. 输出 artifact

`static_spectrum_artifacts.json` 示例结构：

```json
{
  "schema_version": "0.1",
  "artifact_type": "stage_03_static_spectrum",
  "artifact_version": "0.1",
  "source_hamiltonian_config": "configs/hamiltonians/2q1c_charge_basis.yaml",
  "source_hamiltonian_artifacts": "output/stage_02_hamiltonian/hamiltonian_artifacts.json",
  "source_rebaseline_manifest": "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json",
  "source_rebaseline_approval": "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json",
  "provenance": {},
  "spectrum_config": {},
  "mode_order": ["q1", "c", "q2"],
  "basis": {},
  "hamiltonian_summary": {},
  "eigenvalues_GHz": [],
  "eigenvalue_gaps_GHz": [],
  "dressed_state_assignments": [],
  "mode_participation": [],
  "transition_frequencies_GHz": {},
  "anharmonicities_GHz": {},
  "zz_metrics_GHz": {},
  "overlap_summary": {},
  "numerical_convergence": {},
  "flux_scan": {},
  "avoided_crossings": [],
  "crossing_convergence": {},
  "runtime": {},
  "stage_gate": {},
  "warnings": [],
  "checks": []
}
```

### 7.1 dressed_state_assignments

每条 assignment：

```text
label: "100"
target_bare_state: [1, 0, 0]
eigen_index: int
energy_GHz: float
gap_from_ground_GHz: float
overlap: float
status: assigned | low_overlap | missing
```

### 7.2 transition_frequencies_GHz

```text
q1_01
c_01
q2_01
```

### 7.3 anharmonicities_GHz

```text
q1_alpha
c_alpha
q2_alpha
```

### 7.4 zz_metrics_GHz

```text
zz_q1_q2
zz_q1_c
zz_c_q2
```

Notebook 可同时用 MHz 展示：

```text
value_MHz = value_GHz * 1000
```

### 7.5 mode_participation

每个已分配 dressed state 保存：

```text
label
eigen_index
mean_excitations: {q1, c, q2}
fractions: {q1, c, q2} | null
total_mean_excitation
```

### 7.6 numerical_convergence

```text
baseline_cutoffs
refinement_increment
tolerances_MHz
per_mode_refinement_rows
max_frequency_drift_MHz
max_anharmonicity_drift_MHz
max_zz_drift_MHz
passed
```

### 7.7 flux_scan

```text
target
start_phi0 / stop_phi0
coarse_points / refinement settings
evaluated_flux_points_phi0
points:
  flux_key
  flux_bias_phi0
  eigenvalue_gaps_GHz
  bare_transition_frequencies_GHz
  bare_detunings_MHz
  branch_gaps_GHz
  branch_continuity_overlaps
  mode_participation_by_branch
  assignment_summary
candidate_levels
solver_evaluations
cache_hits
wall_time_seconds
```

不保存 scan eigenvectors。`evaluated_flux_points_phi0` 和 `points` 必须按 flux 严格升序排列。

### 7.8 avoided_crossings

每个候选按第 5.4 节保存 branch、final bracket、character evidence keys、最小 splitting、位置、逐 flux participation、
bare detuning sign-change evidence、character exchange、三模式收敛、显著性和最终状态。

### 7.9 provenance

```text
rebaseline_manifest_path / sha256
rebaseline_approval_path / sha256 / decision
hamiltonian_config_sha256
device_artifacts_sha256
stage2_model_source_tree_sha256
stage2_artifacts_sha256
stage2_artifact_version
stage3_solver_source_tree_sha256
environment_fingerprint_sha256
stage2_gap_max_abs_difference_GHz
all_matches
```

### 7.10 crossing_convergence

按 q1-c / c-q2 分组，每个候选必须包含 q1、c、q2 三行 refinement：

```text
refined_mode / baseline_cutoffs / refined_cutoffs
baseline_splitting_MHz / refined_splitting_MHz / absolute_drift_MHz
baseline_flux_phi0 / refined_flux_phi0 / flux_drift_phi0
local_gap_slope_MHz_per_phi0 / flux_drift_equivalent_MHz
flux_slope_stencil_keys / flux_slope_stencil_gaps_MHz
character_evidence_left_key / character_evidence_right_key
semantic_branch_anchors: A_like / B_like
participation_fraction_max_drift
character_exchange_preserved
minimum_within_baseline_final_bracket
runtime_seconds
```

候选汇总保存 `U_cutoff`、`U_total`、relative uncertainty 和 significance ratio。

### 7.11 runtime

```text
profile
acceptance_eligible
budget_seconds
projected_total_seconds
wall_time_seconds
solver_backend
solver_validation_artifact / sha256
solver_validation_approval / sha256 / decision
execution_plan
solver_evaluations / cache_hits
solve_time_p50_seconds / solve_time_p95_seconds
budget_status
```

### 7.12 stage_gate

```text
analysis_completed
status = ready_for_stage4 | geometry_too_weak | unresolved_crossing |
         numerical_failure | provenance_failure | runtime_budget_exceeded
stage4_ready
blocking_reasons
candidate_statuses
```

`StaticSpectrumResult` 是上述全部数据的内存聚合对象，也是 artifact writer 的唯一输入。
不得让 writer 从松散参数或全局状态重新推导 provenance、branch identity、收敛或 stage gate。

## 8. verification notebook

Notebook 只读取：

```text
output/stage_03_static_spectrum/static_spectrum_artifacts.json
```

展示内容：

```text
1. 输入来源、Stage 2.1 SHA-256 provenance 和 Hamiltonian 摘要。
2. eigenvalue gaps 表和折线图。
3. dressed-state assignment 表。
4. overlap heatmap。
5. mode participation 表和热图。
6. q1/q2/c 频率表。
7. anharmonicity 表。
8. ZZ metrics 表，GHz 和 MHz 双单位。
9. charge-cutoff convergence 表，并突出超容差项。
10. coupler flux scan 能级图和 branch continuity 摘要。
11. 每个 evaluated flux 点的 q1/c/q2 branch participation 曲线。
12. crossing 左 / 中 / 右 character exchange 表和图。
13. q1-c / c-q2 的 q1/c/q2 三模式 convergence 表。
14. runtime、solver backend validation、ExecutionPlan 和预算摘要。
15. stage gate status、stage4_ready、blocking reasons。
16. warnings 和 checks。
```

Notebook 生成继续使用：

```text
matplotlib Agg backend
UTF-8
已执行 notebook
```

## 9. 公开接口

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

关键类型契约：

```text
SpectrumBuildContext:
  已验证的 Stage 2.1 provenance、ValidatedSolverSpec、Hamiltonian config、device artifact、
  C_mode、E_C 和基准 junction 数据。
  scan / convergence 不得重复从未验证路径读取输入。

StaticSpectrumPointResult:
  一个明确 flux / cutoff / solver key 下的 eigenpairs、assignment、participation 和 metrics。

FluxScanResult:
  evaluated points、candidate-specific level grids、final brackets、character evidence keys、固定 branch identities、
  逐点 participation、character evidence、cache 和 runtime 数据。

CrossingConvergenceReport:
  以 FluxScanResult 中 final bracket / branch identity 为输入，对 q1/c/q2 三种 refinement 输出不确定度。
  不得重新选择 crossing 或 branch。

SolverBackendReport:
  绑定 validation artifact / approval / Stage 2.1 provenance、solver source hash、environment fingerprint，
  并提供不可变 ValidatedSolverSpec 和 3375 / 4275 两类 signature 的误差 / p95。

ExecutionPlan:
  Adaptive future keys are not pre-enumerated. Use conservative remaining-job upper bounds per cutoff signature,
  and recompute after baseline coarse scan, every adaptive level, and every refined signature; enforce the 1800-second hard gate.

StaticSpectrumResult:
  provenance、baseline、metric convergence、flux scan、crossing convergence、runtime 和 stage gate 的唯一聚合对象。
```

`write_static_spectrum_artifacts` 只做确定性序列化，不重新计算物理结果或 stage gate。

## 10. VSCode runner

主入口：

```text
scripts/run_stage_03_static_spectrum.py
```

脚本示意：

```python
from sqvm.spectrum import verify_static_spectrum


if __name__ == "__main__":
    report = verify_static_spectrum(
        config_path="configs/spectra/2q1c_static.yaml",
        output_dir="output/stage_03_static_spectrum",
    )
    print(report)
```

## 11. 测试设计

### 11.1 test_spectrum_config.py

```text
test_load_valid_spectrum_config
test_reject_missing_source_hamiltonian_config
test_reject_missing_rebaseline_manifest
test_reject_missing_rebaseline_approval
test_reject_rejected_rebaseline_approval
test_reject_approval_manifest_hash_mismatch
test_reject_sha256_mismatch_for_each_bound_input
test_reject_invalid_num_states
test_reject_invalid_min_overlap
test_reject_invalid_max_total_excitations
test_reject_num_states_smaller_than_target_catalog
test_reject_invalid_convergence_tolerance
test_reject_invalid_flux_scan_range
test_reject_even_or_too_small_scan_points
test_reject_stage2_artifact_config_mismatch
test_reject_stage2_artifact_version_before_0_2
```

### 11.2 test_spectrum_eigensystem.py

```text
test_rebuild_hamiltonian_for_spectrum
test_eigenvalues_are_sorted
test_eigenvectors_are_normalized
test_eigenvectors_are_orthogonal
test_eigenvector_columns_match_eigenvalues
test_stage2_gap_consistency_is_error_at_1e_9_GHz
```

### 11.3 test_dressed_labeling.py

```text
test_bare_catalog_contains_required_labels
test_assign_ground_state
test_assign_single_excitation_states
test_low_overlap_produces_warning
test_global_assignment_is_one_to_one
test_global_assignment_beats_greedy_counterexample
test_assignment_is_deterministic_for_ties
```

### 11.4 test_mode_participation.py

```text
test_product_state_mean_excitations
test_single_excitation_fractions_sum_to_one
test_ground_state_fractions_are_null
test_participation_is_finite_and_nonnegative
test_scan_points_store_three_single_excitation_branch_participations
test_character_exchange_detected_from_participation
test_character_evidence_points_are_outside_final_bracket_when_needed
```

### 11.5 test_static_metrics.py

```text
test_transition_frequency_formula
test_anharmonicity_formula
test_zz_formula
test_missing_state_metric_warning
```

### 11.6 test_spectrum_convergence.py

```text
test_per_mode_cutoff_refinement_changes_only_target_mode
test_each_crossing_refines_q1_c_and_q2
test_spectator_mode_drift_contributes_to_conservative_uncertainty
test_refined_branches_anchor_by_bare_character_not_cross_dimension_vector_overlap
test_refinement_domain_includes_character_evidence_keys
test_cutoff_shift_outside_baseline_bracket_fails
test_refined_crossing_outside_evidence_domain_fails
test_flux_drift_converts_to_energy_with_local_slope
test_participation_convergence_required
test_convergence_passes_within_tolerance
test_convergence_failure_blocks_verify
test_default_demo_coupler_cutoff_is_not_accepted_by_relaxing_tolerance
```

### 11.7 test_flux_scan.py

```text
test_flux_override_does_not_mutate_inputs
test_decimal_flux_key_uses_12_places_half_even
test_cache_key_includes_cutoffs_flux_states_and_backend
test_coarse_grid_endpoints_are_exact
test_level_bracket_uses_previous_candidate_grid_neighbors
test_refinement_endpoints_are_reused
test_candidates_refine_level_by_level_independent_of_serialization_order
test_refinement_termination_reasons
test_flux_points_are_sorted_and_cached
test_branch_tracking_is_one_to_one
test_low_continuity_blocks_resolved
test_synthetic_avoided_crossing_location_and_splitting
test_boundary_minimum_blocks_resolved
test_character_exchange_failure_blocks_resolved
test_geometry_too_weak_classification_requires_valid_numerics
test_bare_detuning_sign_change_uses_frequency_tolerance
test_geometry_too_weak_requires_bare_detuning_sign_change_evidence
test_multimode_overlap_blocks_pairwise_resolution
```

### 11.8 test_static_spectrum_verify.py

```text
test_verify_static_spectrum_writes_artifacts
test_verification_notebook_reads_static_spectrum_artifacts
test_vscode_runner_smoke
test_cli_verify_spectrum_success
test_cli_verify_spectrum_failure_exit_code
test_geometry_too_weak_returns_ok_false_and_exit_1
test_stage4_ready_requires_both_crossings_resolved
test_smoke_profile_is_not_acceptance_eligible
test_acceptance_rejects_missing_or_stale_solver_validation
test_solver_validation_approval_binds_artifact_hash
test_solver_validation_binds_stage3_solver_source_tree_hash
test_solver_validation_binds_environment_fingerprint
test_validated_solver_spec_exactly_matches_spectrum_config
test_sha256_counter_v1_v0_is_deterministic
test_sha256_counter_v1_normative_seed_and_block0_vector
test_near_degenerate_blocks_use_dense_maximal_contiguous_partition
test_near_degenerate_partition_boundary_margin_is_rejected
test_trailing_near_degenerate_block_is_rejected
test_projector_error_uses_dense_index_blocks_and_spectral_norm
test_eigsh_repeatability_for_gaps_subspaces_and_participation
test_validated_eigsh_matches_dense_for_baseline_and_refined_crossing_cases
test_execution_plan_uses_dimension_specific_p95
test_runtime_budget_exceeded_blocks_acceptance
test_static_spectrum_result_is_only_artifact_writer_input
```

## 12. 已知限制

阶段 3 第一版限制：

```text
只支持 2q1c。
flux scan 只支持 coupler 的一维扫描。
单点标记只使用 bare overlap；跨点连续性只使用相邻 eigenvector overlap。
不处理多参数闭环、Berry phase 或真简并处的规范选择。
不保存本征态矩阵。
不自动调整 device 几何。
不把 geometry_too_weak 当作自动改参授权；必须由用户决定是否改变器件物理目标。
不计算真实门保真度。
不做时间演化。
```

## 13. 与阶段 4 的接口边界

阶段 4 控制信号链可以依赖阶段 3 输出：

```text
transition_frequencies_GHz
anharmonicities_GHz
zz_metrics_GHz
dressed_state_assignments
mode_participation
flux_scan
avoided_crossings
numerical_convergence
crossing_convergence
runtime
stage_gate
mode_order
```

阶段 4 不应重新实现 dressed-state 标记逻辑。只有同时满足以下条件才能消费阶段 3 数据：

```text
StaticSpectrumVerificationReport.ok = true
StaticSpectrumResult.stage_gate.status = ready_for_stage4
StaticSpectrumResult.stage_gate.stage4_ready = true
```

`geometry_too_weak` 即使 `analysis_completed=true` 也不能进入阶段 4。
`stage4_ready=true` 只表示 artifact 通过计算与物理证据门；独立测试 / 审查 AI批准和用户验收仍是外部流程硬门。

## 14. 阶段 3 验收清单

阶段 3 完成时必须满足：

```text
1. configs/spectra/2q1c_static.yaml 存在。
2. Stage 2.1 rebaseline gate 已批准，artifact / manifest / SHA-256 和 gap consistency 全部通过。
3. acceptance / smoke configs 和 runner 可以运行，smoke 明确不可验收。
4. output/stage_03_static_spectrum/ 下生成 static_spectrum_artifacts.json 和 verification.ipynb。
5. notebook 展示逐 flux participation、character exchange、三模式 crossing convergence、runtime 和 stage gate。
6. 关键静态指标通过配置定义的 cutoff 收敛门。
7. q1-c / c-q2 都满足第 5.4 节全部 resolved 条件。
8. 默认 demo 不通过放宽容差掩盖任何已知 cutoff 漂移。
9. acceptance 使用已独立批准的 eigsh validation，并在预算内完成。
10. StageGateDecision.status=ready_for_stage4，report.ok=true，stage4_ready=true。
11. tests/ 中阶段 3 测试通过，CLI 成功 / 失败退出码正确。
12. 独立测试 / 审查 AI 输出验收矩阵和审查记录。
13. docs/logs/DEVELOPMENT_LOG.md 记录实现结果与运行时间。
14. 用户检查并认可 verification.ipynb 后，再进入阶段 4。
```

## 15. 后续用户阶段门

```text
U1. 若实际 acceptance 结果为 geometry_too_weak：
    选择调整器件电容 / 几何并重新走 Stage 1 -> 2 -> 2.1，
    或正式修改项目目标，不再要求当前 demo 支持可解析的 q1-c / c-q2 avoided crossing。
    在用户作出选择前，阶段 4 保持阻塞。
```
