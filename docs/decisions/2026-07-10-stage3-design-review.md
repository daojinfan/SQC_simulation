# 阶段 3 设计决策与 AI 交接清单

状态：

```text
APPROVED / Stage 3 v0.1 design frozen / legacy anchor confirmation is the only remaining external gate
```

日期：

```text
2026-07-10
```

首次独立审查与设计修订：

```text
2026-07-11
```

依据：

```text
docs/00_project_vision.md
docs/10_development_process.md
docs/20_roadmap.md
docs/stages/03_static_spectrum_plan.md
docs/designs/03_static_spectrum_design.md
output/stage_02_hamiltonian/hamiltonian_artifacts.json
```

## 已确认设计决策

```text
D1. 阶段 3 不再只做单 flux 点；coupler flux scan 和 q1-c / c-q2 avoided crossing 是必需交付物。
D2. 单点 dressed-state assignment 使用全局最大 overlap 一一匹配，不使用逐 label 贪心。
D3. flux scan 使用相邻点 eigenvector overlap 的全局一一匹配跟踪 adiabatic branch。
D4. mode participation 定义为 bare-energy basis 下各模式平均激发数及其归一化 fraction。
D5. 频率、anharmonicity、ZZ 和 avoided-crossing splitting 必须通过逐模 cutoff refinement；
    每个 crossing 必须分别评估 q1、c、q2，不预设 spectator 可忽略。
D6. 收敛超容差会阻塞 verify；不得通过放宽容差掩盖默认 c cutoff 的 31.7 MHz 已知漂移。
D7. 默认 Hamiltonian cutoff 第一候选调整为 q1=7、c=7、q2=7，并重新生成阶段 2 artifact。
D8. flux override 通过阶段 2 effective-junction 公开接口的向后兼容参数传入，只在内存中生效。
D9. artifact 不保存 eigenvectors；保存可复查的 assignment、参与度、收敛、scan 和 crossing 摘要。
D10. q1-c / c-q2 必须同时 resolved 才能进入阶段 4。
D11. geometry_too_weak 是合法诊断，但 report.ok=false、CLI exit 1、stage4_ready=false。
D12. Stage 2.1 rebaseline 是阶段 3 实现前置 gate，使用 SHA-256 内容摘要和自包含 error 级 gap consistency；
     阶段 3 实现后再独立重复同一检查。
D13. StaticSpectrumResult 是 artifact writer 的唯一聚合输入；crossing convergence 显式消费 FluxScanResult。
D14. 每个 scan 点必须保存三条单激发 branch 的 q1/c/q2 participation，并证明 character exchange。
D15. acceptance 预算 1800 秒；backend 固定为已独立批准的 validated eigsh，超预算直接失败。
```

## 首次独立审查处置

### 1. 未解析 crossing 仍可能通过

处置：

```text
已修正。
resolved 现在强制要求 interior、refinement converged、continuity >= 0.90、逐点 participation、
character exchange、target-pair participation、q1/c/q2 收敛、splitting significance 和有效 runtime。
geometry_too_weak / unresolved 都使 report.ok=false 并阻塞阶段 4。
```

### 2. crossing 收敛遗漏 spectator mode

处置：

```text
已修正。
每个 q1-c / c-q2 crossing 都分别提高 q1、c、q2 cutoff。
三个绝对 splitting drift 求和形成保守 U_cutoff，并同时比较 flux 和 participation 漂移。
不再允许未测量 spectator 影响就跳过。
```

### 3. Stage 2 provenance / 重新基线不足

处置：

```text
已修正。
新增 docs/stages/02_1_hamiltonian_rebaseline_plan.md。
要求完整回归、两个 verify、artifact 确定性、config / device artifact / Stage 2 model source tree /
Stage 2 artifact SHA-256、artifact_version 0.2 和独立审查。
Stage 2.1 自包含重建最低 12 gaps 与 candidate artifact 差异 <= 1e-9 GHz；
Stage 3 实现后再重复该 error check，不构成循环依赖。
```

### 4. 接口不能表达 crossing 收敛

处置：

```text
已修正。
新增 SpectrumBuildContext、StaticSpectrumPointResult、FluxScanResult、CrossingConvergenceReport、
RuntimeReport、StageGateDecision 和 StaticSpectrumResult。
check_crossing_convergence 显式接收 FluxScanResult；writer 只接受 StaticSpectrumResult。
```

### 5. 数值规模与运行预算过期

处置：

```text
已修正。
baseline 维度 3375、单模 refined 维度 4275；base scan 最多 169 次，保守 acceptance 最多 509 次 solve。
acceptance 预算 1800 秒，smoke 预算 90 秒且不可验收。
新增 dense_pilot 和 solver_validation profiles；acceptance 固定使用已批准 validated eigsh，超预算直接失败。
```

### 6. 扫描确定性不足

处置：

```text
已修正。
flux key 固定为 Decimal 12 位 ROUND_HALF_EVEN；cache key 包含 cutoffs / flux / states / backend。
level L+1 bracket 只取 candidate 自己 level L grid 的相邻点；端点复用。
固定 candidate 同步逐级 refinement，并定义 converged / boundary / resolution_limit /
key_resolution_exhausted 终止规则。
```

## 第二轮独立审查处置

第二轮结论仍为不批准冻结。处置如下：

```text
R2-1 Stage 2.1 循环 gate：
  已关闭。Stage 2.1 新增只依赖阶段 2 API 的自包含 gap rebuild check；阶段 3 后续重复，不再反向阻塞。

R2-2 legacy artifact 缺 trust anchor：
  设计已补 previous_stage2_artifacts_sha256 和独立 legacy_baseline_anchor.json。
  当前 hash 为 222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C；
  等待用户确认，未确认不得覆盖旧 artifact 或执行正式 rebaseline。

R2-3 跨 cutoff branch / character 不可实现：
  已关闭。refined model 在固定 character evidence domain 独立求解，以 bare participation 锚定 A_like/B_like，
  只在相同 Hilbert 空间内跟踪；minimum 移出 baseline bracket / evidence domain 均有明确失败状态。

R2-4 runtime / eigsh validation 不自洽：
  已关闭。保守 acceptance 上限改为 509 solves；按 3375 / 4275 signature 分别估时，adaptive 每级重算 upper bound。
  新增 dense_pilot、solver_validation artifact + approval、ExecutionPlan；acceptance 固定 validated eigsh，超预算失败。

R2-5 crossing flux drift 无公式：
  已关闭。使用 local gap slope 将 delta Phi 转成 U_flux(MHz)，并纳入 U_total。

R2-6 geometry_too_weak bare detuning 未定义：
  已关闭。逐 flux 保存单模 bare frequencies / detunings；sign change 使用 frequency tolerance 双侧证据。

R2-7 schema_version 未检查：
  已关闭。Stage 3 同时硬检查 schema_version=0.2 和 artifact_version=0.2。

R2-8 roadmap 验收过弱：
  已关闭。路线图加入 Stage 2.1 provenance、两个 crossing resolved、收敛、runtime 和独立验收硬门。
```

## Third independent review disposition

The third review did not approve design freeze. All four high-priority and two medium findings are dispositioned:

```text
R3-1 Acceptance solver binding:
  Closed. The exact eigsh which/tolerance/maxiter/ncv/v0_rule, Stage 3 solver source-tree SHA-256,
  environment fingerprint, deterministic v0, and repeatability checks are bound by SolverBackendReport.
  An immutable ValidatedSolverSpec is carried through SpectrumBuildContext into every acceptance solve.

R3-2 1e-9 GHz gap gate versus eigsh validation:
  Closed. Stage 3 provenance repeats the Stage 2.1 dense 12-state rebuild. It is stored separately from
  the acceptance eigsh baseline; the eigsh-to-dense validation tolerance remains 1e-6 GHz.

R3-3 Adaptive ExecutionPlan:
  Closed. Future adaptive keys are not pre-enumerated. Conservative remaining-job upper bounds are
  recomputed per cutoff signature after every adaptive level. The acceptance ceiling is 509 solves.

R3-4 Deterministic U_flux slope:
  Closed. Baseline and refined final-level immediate-neighbor one-sided slopes define S_model;
  invalid interior/stencil conditions fail avoided_crossing_flux_converged.

R3-5 Refined grid includes baseline minimum:
  Closed. The 17-point evidence grid is unioned with evidence endpoints and baseline final-bracket
  left/minimum/right keys, so the baseline crossing key is always solved.

R3-6 Stage 2.1 roadmap circular wording:
  Closed. Stage 2.1 owns the self-contained dense rebuild; Stage 3 repeats it later.
```

## Fourth independent review disposition

The fourth review did not approve design freeze. Three high and one medium findings are dispositioned:

```text
R4-1 Conservative solve ceiling:
  Closed. Each refined initial set allows 17 uniform evidence keys plus up to 3 forced baseline-bracket
  keys. Refined maximum is 2*3*((17+3)+4*9)=336 and total maximum is 169+336+3+1=509 solves.
  ExecutionPlan, plan tests, decision assertions, and log references use 509.

R4-2 Near-degenerate projector gate:
  Closed. Dense N+1 eigenpairs define maximal contiguous blocks at a fixed 1e-5 GHz threshold;
  a 2e-6 GHz boundary margin and trailing-block rule reject ambiguous/truncated partitions.
  Dense/eigsh and repeat projectors use identical dense index ranges and spectral matrix 2-norm,
  with explicit 1e-6 and 1e-8 limits.

R4-3 Stage 3 implementation permission conflict:
  Closed. Before legacy-anchor confirmation only the Stage 2.1 gate and non-mutating Stage 2.1
  fixtures may be implemented. Stage 3 code/config/runner/artifact work waits for Stage 2.1 approval.

R4-4 sha256_counter_v1 canonical bytes:
  Closed. The detailed design fixes field order, ASCII decimal formats, UTF-8/LF/no-BOM bytes,
  uppercase artifact hash, uint64 big-endian counter/word order, float mapping, and a normative
  seed-length/seed-digest/block-0-digest test vector.
```

## Fifth independent review approval

```text
Decision: APPROVED.
No blocking, high-priority, or medium-priority findings remain.
R4-1 509-solve conservative ceiling: CLOSED.
R4-2 deterministic near-degenerate projector gate: CLOSED.
R4-3 Stage 2.1/Stage 3 implementation permission boundary: CLOSED.
R4-4 sha256_counter_v1 canonical byte serialization and normative vector: CLOSED.
Original crossing, convergence, provenance, runtime, deterministic scan, and Stage 4 blocking gates did not regress.
Stage 3 v0.1 design is frozen.
Legacy Stage 2 artifact user confirmation remains an independent external gate and is not a design finding.
Reviewer task: 019f4f05-aaa2-7fe2-8769-8eece01ecc08.
Review was read-only; no files or artifacts were modified by the reviewer.
```

## 设计预算与后续用户阶段门

```text
设计负责人采纳 v0.1 预算：frequency 0.50 MHz、anharmonicity 1.00 MHz、
ZZ / crossing absolute 0.01 MHz、crossing relative 5%、participation fraction 0.02。

当前仍需用户确认 legacy Stage 2 artifact trust anchor：
222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C。
Before legacy-anchor confirmation, development is limited to the Stage 2.1 gate implementation and
non-mutating Stage 2.1 fixtures. Stage 3 spectrum modules/configs/runners/artifacts must not begin.
No old artifact may be overwritten and no formal rebaseline or gate approval may run before confirmation.

若未来实际结果为 geometry_too_weak，用户需选择调整器件几何并重新走 Stage 1/2/2.1，
    或正式修改项目物理目标；在选择前阶段 4 阻塞。
```

## 开发 AI 必须交付

```text
[ ] configs/spectra/2q1c_static.yaml
[ ] configs/spectra/2q1c_static_smoke.yaml
[ ] scripts/run_stage_03_static_spectrum.py
[ ] src/sqvm/spectrum/ 完整模块
[ ] python -m sqvm verify-spectrum CLI
[ ] Stage 2.1 rebaseline gate 的 flux override、完整回归、SHA-256 manifest 和独立批准
[ ] artifact_version 0.2 的 Hamiltonian artifact
[ ] StaticSpectrumResult / CrossingConvergenceReport / StageGateDecision
[ ] static_spectrum_artifacts.json
[ ] 已执行 verification.ipynb
[ ] 单元、集成、CLI、runner 和物理数值测试
[ ] 开发日志
```

## 测试 / 审查 AI 验收矩阵

### A. 配置与接口

```text
[ ] 配置字段、默认值、范围和单位与详细设计一致。
[ ] num_states 不少于 max_total_excitations 导出的目标 bare labels 数量。
[ ] flux scan 只允许 target=c，区间和点数规则被验证。
[ ] source_hamiltonian_artifacts 的 basis / solver / source 与当前 Hamiltonian config 一致。
[ ] Stage 2.1 approval decision=approved，并将 manifest / Stage 2 artifact SHA-256 绑定到当前 bytes。
[ ] Stage 2 artifact schema_version 和 artifact_version 都严格等于 0.2。
[ ] Stage 3 重建最低 12 gaps 与 artifact 差异 <= 1e-9 GHz，否则 error。
[ ] 阶段 3 只通过阶段 2 公开接口重建 Hamiltonian。
[ ] flux override 不修改 artifact、配置对象或磁盘文件。
```

### B. Eigensystem 与 bare basis

```text
[ ] eigenvalues finite / sorted。
[ ] eigenvectors 与 eigenvalues 列对应、归一且正交。
[ ] 单模 bare states 是单模 Hamiltonian 本征态，不是 charge number states。
[ ] product basis Kronecker 顺序固定为 q1 ⊗ c ⊗ q2。
```

### C. Assignment 与 participation

```text
[ ] assignment 使用 scipy 全局线性分配。
[ ] 同一 eigenstate 不会重复分配给多个 label。
[ ] 人工构造的贪心反例由全局分配得到正确结果。
[ ] 低 overlap 保留 assignment 并给 warning。
[ ] mean excitation 非负；单激发 fraction 归一；ground fraction 为 null。
[ ] 每个 scan 点保存 |100> / |010> / |001> branch 的 q1/c/q2 participation。
[ ] final bracket 与 character evidence keys 分离；evidence 左右点 character 明确交换，minimum 点 target pair participation 达标。
```

### D. 静态指标

```text
[ ] transition frequency、anharmonicity 和 ZZ 公式与 label 顺序一致。
[ ] 缺少必需 label 时不伪造数值，必须标记 unavailable，并使 verify 失败。
[ ] 与阶段 2 baseline gaps 在数值容差内一致。
```

### E. 数值收敛

```text
[ ] idle metrics refinement 每次只提高一个模式 cutoff。
[ ] 每个 q1-c / c-q2 crossing 分别提高 q1、c、q2 cutoff，不跳过 spectator。
[ ] cutoff、Hilbert dimension、运行时间和每项指标漂移完整记录。
[ ] splitting 使用三模式绝对漂移和形成保守不确定度，并检查绝对 / 相对容差与显著性。
[ ] 默认 N=5 coupler 不得通过放宽容差蒙混过关。
[ ] 任一必需指标超容差时 verify 返回 ok=false，CLI 退出码为 1。
```

### F. Flux scan 与 avoided crossing

```text
[ ] coarse scan 覆盖完整区间，所有点严格升序且缓存去重。
[ ] flux key 是 Decimal 12 位 ROUND_HALF_EVEN，cache key 包含 cutoff / flux / states / backend。
[ ] 相邻点 branch mapping 是一一映射。
[ ] 每一级 bracket 只使用 candidate 上一级 grid 的相邻点，端点复用。
[ ] 两个 candidates 同步逐级插点，重新按 flux 排序并从头跟踪，结果不依赖序列化顺序。
[ ] 合成二能级案例能恢复已知 crossing 位置和 splitting。
[ ] q1-c / c-q2 候选都记录位置、splitting、分辨率、连续 overlap 和状态。
[ ] minimum 在边界、连续性不足、character 不交换、三模式未收敛或 refinement 未终止时不能 resolved。
[ ] geometry_too_weak / unresolved 都使 report.ok=false、stage4_ready=false、CLI exit 1。
```

### G. Artifact 与 notebook

```text
[ ] artifact 含设计要求的全部字段，不保存完整 eigenvectors。
[ ] artifact 明确包含 provenance、逐点 participation、crossing convergence、runtime 和 stage gate。
[ ] notebook 只读 static_spectrum_artifacts.json。
[ ] notebook 展示参与度、收敛、flux scan、avoided crossing、warning 和 checks。
[ ] notebook 使用 Agg + UTF-8，并在 headless 环境执行完成。
[ ] acceptance profile 使用 hash/approval 有效的 eigsh validation，并在 1800 秒预算内。
[ ] Solver validation binds exact config/source/environment and immutable ValidatedSolverSpec to acceptance.
[ ] Dense-defined near-degenerate blocks, ambiguity/truncation failures, and spectral-norm projector gates pass.
[ ] sha256_counter_v1 matches the normative 166-byte seed, seed digest, and block-0 digest.
[ ] ExecutionPlan counts forced refined-grid union keys and enforces the 509-solve conservative ceiling.
[ ] smoke profile acceptance_eligible=false。
```

### H. 红线

命中任一项即审查不通过：

```text
实现阶段重新解析 device.yaml 或复制 SQUID / Hamiltonian 公式。
修改输入 artifact / YAML 来执行 flux scan。
使用逐 label 贪心并允许 eigenstate 重复分配。
只检查矩阵结构，不检查阶段 3 指标数值收敛。
跳过任一 crossing 的 q1 / c / q2 cutoff refinement。
通过放宽容差隐藏已知 cutoff 漂移。
只画能级图，不输出可复查的 branch / crossing 数值表。
不保存逐 flux participation 或没有 character exchange 证据就标 resolved。
仅凭路径 / 字段比较 provenance，不验证 SHA-256。
stage2 gap consistency 只给 warning。
物理指标未收敛但 verify 仍返回 ok=true。
geometry_too_weak 仍允许 stage4_ready=true。
超预算时自动减少精度，或使用缺少 hash/approval 的 eigsh validation。
开发 AI 自己批准自己的最终实现，缺少独立测试 / 审查记录。
```

## 进入实现前的关口

```text
1. Independent review AI performs the fifth consistency review of Stage 2.1, the Stage 3 plan, detailed design, and this checklist.
2. Design AI freezes the Stage 3 v0.1 interfaces only after that review approves them.
3. User confirms the legacy Stage 2 artifact trust anchor; this external gate is independent of design freeze.
4. Development AI implements Stage 2.1, then independent review approves its manifest/artifact gate.
5. Stage 3 implementation starts only after Stage 2.1 approval.
```
