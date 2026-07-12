# Stage 3 C2 非有限诊断值编码决策

日期：2026-07-11
状态：APPROVED FOR C2.1 IMPLEMENTATION
范围：Stage 3 结果聚合与 artifact 序列化实现澄清

## 背景

Stage 3 C2 正式 acceptance 在完成主要计算后、写出 artifact 前失败：

```text
ValueError: Out of range float values are not JSON compliant: inf
```

该运行耗时约 32.5 秒，未超过 1800 秒预算。当前失败分类为 `implementation_error`；
它不能证明或否定数值收敛、avoided crossing 几何强度或 Stage 4 readiness。

已定位到两类实现风险：

1. `float(candidate.get("level_to_level_drift_MHz") or float("inf"))` 会把合法的 `0.0`
   漂移错误转换为 `inf`，并且 candidate summary 当前没有转发 final-level drift。
2. baseline/refined minimum 不在内部 stencil 时，局部斜率路径使用 `inf` 表示不可用；该诊断值随后进入
   `StaticSpectrumResult` 并被 canonical JSON 拒绝。

## 决策

本修复不改变六份冻结设计、物理模型、cutoff、扫描网格、容差、solver 规格或状态优先级。

### 1. 数值编码

- 所有可用数值必须为有限 JSON number；合法的 `0.0` 必须保留为 `0.0`。
- 不可用的诊断数值必须写为 JSON `null`，并同时提供确定性的 availability/status/reason 字段。
- `NaN`、`Infinity`、`-Infinity` 禁止进入正式或失败诊断 artifact。
- 禁止通过 `allow_nan=true`、字符串 `"inf"`、任意大哨兵值或静默 sanitizer 绕过该规则。

### 2. Crossing 数据流

- `_build_candidate_summary` 必须原样转发 final refinement level 的
  `level_to_level_drift_MHz`；缺失或不可用时为 `null`。
- 任何 `None` 检查必须显式执行，不能使用 `x or fallback`，避免把零值当作缺失。
- baseline 或 refined local-slope stencil 无效时：
  - `local_gap_slope_MHz_per_phi0 = null`；
  - `flux_drift_equivalent_MHz = null`；
  - `flux_slope_valid = false`；
  - 保存固定 reason，例如 `minimum_not_interior`、`duplicate_flux_key` 或
    `zero_stencil_denominator`。
- 只有 baseline 和 refined slope 都有限时才计算 `S_i` 和 `U_flux_i`。

### 3. Uncertainty gate

- `crossing_uncertainty_summary` 必须验证 splitting、三模式 refinement 行、level drift、solver error、
  cutoff drift、flux equivalent drift 和 participation drift 是否完整且有限。
- 任一必需输入不可用时，受影响的 `U_flux_MHz`、`U_total_MHz`、
  `relative_uncertainty`、`significance_ratio` 等派生量写为 `null`，
  `uncertainty_inputs_valid=false`，并记录排序稳定、去重后的 `unavailable_reasons`。
- `passed` 必须为 false；所有阈值比较必须先判断对应值非 null，不能抛出 `TypeError`。
- `resolved` 与 `geometry_too_weak` 都必须要求 `uncertainty_inputs_valid=true`。
  无效数值证据不能被误分类为 `geometry_too_weak`。
- boundary、resolution 和 cutoff-domain 的既有优先级保持不变；意外缺失的 uncertainty 输入必须
  fail closed，不能进入 `resolved`。

### 4. Artifact 写出

- `static_spectrum_result_to_payload` 之后、落盘之前执行递归 finite preflight；错误信息必须包含首个
  非有限值的 JSON path。
- preflight 是断言，不是 sanitizer；它不得修改 payload。
- canonical bytes 必须在创建/替换正式 artifact 前完整生成。artifact 写入采用临时文件加原子 replace，
  避免序列化失败留下部分 JSON。
- 合法的 boundary、resolution、numerically-unconverged 或 geometry-too-weak 结果必须仍能生成完整
  诊断 artifact/notebook，并以 `ok=false`、`stage4_ready=false`、CLI exit 1 返回。

## C2.1 验收范围

开发 AI 必须补充至少以下自动测试：

```text
zero level-to-level drift remains 0.0 and serializes canonically
missing level drift becomes null with uncertainty_inputs_valid=false
invalid baseline/refined slope stencil emits null plus deterministic reason
unavailable uncertainty cannot resolve or classify geometry_too_weak
boundary/unresolved StaticSpectrumResult writes a canonical diagnostic artifact
unexpected nested non-finite value is rejected with its JSON path and no partial artifact
```

测试应使用 unit/synthetic fixture；solver validation 重新批准前不得再次运行正式 acceptance。

## 重新批准顺序

任何 `src/sqvm/spectrum` 修改都会改变 Stage 3 source-tree SHA-256，因此固定顺序为：

1. 实现 C2.1，并运行定向测试、Stage 3 suite、全仓库安全回归、compileall、`git diff --check`。
2. 重新生成 dense/eigsh solver-validation candidate，并绑定新的 source-tree hash。
3. 独立测试 AI 采用精简复验：完整自动测试、原 P0 代表攻击、一个 availability/null 代表案例，
   以及一次 28-case validation regeneration；不扩展 fuzz/stress 矩阵。
4. 独立测试 AI 重新签发 solver validation approval。
5. 只在新 approval 生效后运行一次 C2 正式 acceptance。

旧的 solver validation approval 在源代码修改后立即失效，不得沿用。
