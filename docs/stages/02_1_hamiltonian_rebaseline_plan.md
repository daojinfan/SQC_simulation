# 阶段 2.1 计划：Hamiltonian 重基线门

## 触发原因

阶段 2 已于 2026-07-09 技术验收通过，但阶段 3 设计要求：

```text
1. 将默认 charge cutoff 第一候选从 q1=5, c=5, q2=5 调整为 q1=7, c=7, q2=7。
2. 为 resolve_effective_junctions 增加向后兼容的内存 flux override。
3. 为阶段 2 artifact 增加可校验的内容 provenance。
```

这些修改改变了已验收阶段的配置、API 和 artifact，因此必须先完成正式重基线，不能把旧阶段 2 验收记录直接沿用到新基线。

## 阶段目的

建立一个可由阶段 3 严格验证的新 Hamiltonian 基线，证明：

```text
阶段 1/2 行为没有非预期回归
新 cutoff 的数值变化可解释
不传 flux override 时阶段 2 行为保持兼容
配置、器件 artifact、阶段 2 源码和输出 artifact 由内容摘要绑定
阶段 3 可以确定性重建与 artifact 一致的低能谱
```

## 范围

本 gate 包含：

```text
阶段 2 API 的向后兼容 flux override 扩展
Hamiltonian cutoff 调整
阶段 2 artifact schema_version / artifact_version 升级为 0.2
provenance 字段与外部 rebaseline manifest
阶段 1/2 全量回归
verify-device / verify-hamiltonian 端到端验证
旧基线 -> 新基线数值差异报告
确定性重建检查
独立测试 / 审查 AI批准
```

本 gate 不包含：

```text
阶段 3 eigensystem、dressed-state、flux scan 或静态指标实现
器件几何调整
放宽阶段 3 数值容差
```

## 输入

```text
configs/devices/2q1c2r.yaml
output/stage_01_device_model/device_artifacts.json
configs/hamiltonians/2q1c_charge_basis.yaml
output/stage_02_hamiltonian/hamiltonian_artifacts.json（旧基线）
src/sqvm/device/**/*.py
src/sqvm/hamiltonian/**/*.py
pyproject.toml
```

`src/sqvm/__main__.py` 作为共享 CLI dispatcher 单独记录 `stage2_cli_sha256_at_rebaseline`，但不纳入
下游必须保持不变的模型 source tree digest；阶段 3 会合法新增 `verify-spectrum` 子命令。
阶段 2 CLI 行为由完整回归测试保护。

## 输出

```text
output/stage_01_device_model/device_artifacts.json（重新验证）
output/stage_02_1_hamiltonian_rebaseline/previous_hamiltonian_artifacts.json（旧基线 bytes 原样归档）
output/stage_02_1_hamiltonian_rebaseline/legacy_baseline_anchor.json
output/stage_02_hamiltonian/hamiltonian_artifacts.json（artifact_version 0.2）
output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json
output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json
output/stage_02_1_hamiltonian_rebaseline/verification.ipynb
docs/decisions/<date>-stage2-1-rebaseline-review.md
```

## SHA-256 定义

普通文件使用原始 bytes 计算 SHA-256，不做换行或 JSON 规范化转换。

阶段 2 model source tree digest 使用固定算法：

```text
1. 收集本计划“输入”中列出的阶段 1/2 Python 源文件和 pyproject.toml。
2. 路径转为仓库相对 POSIX 路径，并按 UTF-8 字节序排序。
3. 对每个文件依次写入：path_utf8 + NUL + byte_length_ascii + NUL + raw_bytes。
4. 对完整字节流计算 SHA-256。
```

manifest 至少记录：

```text
device_config_sha256
device_artifacts_sha256
hamiltonian_config_sha256
stage2_model_source_tree_sha256
stage2_cli_sha256_at_rebaseline（信息字段）
stage2_artifacts_sha256
git_commit（信息字段）
git_dirty（信息字段；SHA-256 才是权威依据）
```

阶段 2 artifact `0.2` 自身增加 `provenance`：

```text
device_artifacts_sha256
hamiltonian_config_sha256
stage2_model_source_tree_sha256
```

artifact 自身摘要保存在外部 manifest，避免 self-hash 递归。
修改配置或重新生成前，runner 必须先把旧 Stage 2 artifact bytes 原样归档并记录 SHA-256；
现有历史审查记录没有保存 hash，因此本次 legacy anchor 必须由用户显式确认当前旧 artifact bytes。
未确认时 `legacy_baseline_anchor.json.decision=pending`，Stage 2.1 不得批准。

`legacy_baseline_anchor.json` 至少包含：

```text
schema_version = 0.1
artifact_type = stage_02_legacy_baseline_anchor
artifact_version = 0.1
decision = pending | accepted | rejected
previous_stage2_artifacts_sha256
expected_sha256 = 222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C
historical_review_path = docs/decisions/2026-07-09-stage2-implementation-review.md
approved_by = user
```

若用户提供另一份可信旧 bytes，则必须重新计算 expected SHA-256、说明来源并重新做独立审查；
不能用新结果反推“旧基线”。

## 公开接口变更

唯一允许的阶段 2 行为扩展：

```python
resolve_effective_junctions(
    device_artifacts,
    flux_bias_overrides_phi0: Mapping[str, float] | None = None,
) -> tuple[EffectiveJunction, ...]
```

约定：

```text
None 或空 mapping 与旧接口结果逐字段一致。
override key 只能是 q1 / c / q2，value 必须是有限实数，单位 Phi0。
override 不修改 DeviceArtifacts、配置对象或磁盘文件。
阶段 3 第一版只使用 c override。
```

Stage 2.1 自包含重建接口：

```text
rebuild_stage2_low_energy_spectrum(config_path, artifact_path) -> Stage2RebuildConsistencyReport
```

该接口只能调用阶段 2 已公开的 config / artifact / capacitance / junction / builder 接口和明确的 dense `eigh`，
不得依赖 `sqvm.spectrum` 或任何阶段 3 代码。它重建当前 candidate artifact 的最低 12 gaps，
并与 artifact 比较 `max_abs_difference_GHz <= 1e-9`。

## 回归与数值检查

必须运行：

```text
完整 pytest
verify-device
verify-hamiltonian
VSCode stage 1 / stage 2 runner smoke
阶段 2 artifact 连续生成两次的 byte-for-byte 确定性检查
```

旧基线到新基线必须报告：

```text
cutoff / Hilbert dimension / runtime
C_mode / E_C / EJ_eff 最大差异
最低 12 个 gaps 的逐项差异
single-transmon analytic check
逐模 N -> N+2 convergence
所有 checks 和 warnings
```

第一候选使用三个模式 N=7，是因为已知 N=5 -> N=7 漂移约为 q1=1.12 MHz、c=31.7 MHz、
q2=2.11 MHz，三者都超过阶段 3 暂定 0.50 MHz frequency budget。Stage 2.1 必须继续报告
N=7 -> N=9 漂移；任一模式仍超用户批准后的 budget 时，不能批准该重基线。

以下属于 error 级契约：

```text
不传 override 时 effective junction 结果与旧行为逐字段一致。
相同 flux override 连续构建两次结果一致。
Stage 2.1 自包含重建的最低 12 个 gaps 与 candidate Stage 2 artifact 差异 <= 1e-9 GHz。
阶段 3 实现后必须独立重复相同检查，但它不是 Stage 2.1 批准的前置依赖。
阶段 3 读取时重新计算的 config / device artifact / model source tree SHA-256 与 manifest 完全一致。
stage2_artifacts_sha256 与实际 artifact bytes 完全一致。
```

## rebaseline_manifest.json

至少包含：

```text
schema_version = 0.1
artifact_type = stage_02_1_hamiltonian_rebaseline
artifact_version = 0.1
created_from_stage2_artifact_version
candidate_stage2_artifact_version = 0.2
previous_stage2_artifacts_sha256
legacy_baseline_anchor_path
legacy_baseline_anchor_sha256
paths
sha256
old_to_new_numeric_deltas
test_summary
verify_device_summary
verify_hamiltonian_summary
determinism_checks
```

manifest 不得自行声明 approved。独立审查完成后另写 `rebaseline_approval.json`：

```text
schema_version = 0.1
artifact_type = stage_02_1_hamiltonian_rebaseline_approval
artifact_version = 0.1
decision = approved | rejected
manifest_sha256
stage2_artifacts_sha256
legacy_baseline_anchor_sha256
previous_stage2_artifacts_sha256
review_record_path
reviewer_role = independent_test_review_ai
blocking_findings
```

approval 的 `manifest_sha256` 必须匹配 manifest 原始 bytes；`stage2_artifacts_sha256` 必须同时匹配 manifest
和实际 Stage 2 artifact。阶段 3 不接受 manifest 内部的自声明状态。

## 验收标准

```text
1. 阶段 1/2 完整测试和两个 verify 命令通过。
2. legacy baseline anchor decision=accepted，且 previous artifact bytes SHA-256 完全匹配。
3. 新 artifact schema / version、provenance 和 manifest 完整。
4. SHA-256 可从当前文件重新计算并完全匹配。
5. approval decision=approved，且绑定的 manifest / Stage 2 artifact SHA-256 完全匹配。
6. 连续两次生成的阶段 2 artifact bytes 完全相同。
7. 旧到新数值差异有解释，未出现 cutoff 之外的隐藏物理变化。
8. Stage 2.1 自包含重建 gap 一致性达到 1e-9 GHz，并作为 error check。
9. 独立测试 / 审查 AI批准新基线。
10. 在 gate 通过前，不得开始阶段 3 实现。
```

## 红线

```text
只比较路径或文件名，不计算内容 SHA-256。
没有用户接受的 legacy anchor 就声称 old-to-new delta 可信。
由 manifest 自己声明 approved，没有独立 approval 文件绑定 manifest hash。
修改 cutoff 后继续引用旧阶段 2 artifact 或旧验收结论。
Stage 2.1 自包含 gap consistency 只给 warning，或把它推迟到尚不存在的阶段 3 实现。
用手工编辑 artifact 的方式制造新基线。
未运行完整回归就交阶段 3。
```
