# 校准候选决策与显式覆盖更新详设

## 1. 文档状态

- 阶段编号：`07_1_14`
- 设计对象：通用校准候选的推荐判断、人工决策和配置写回
- 依赖：`calibration_candidate_v1`、PlatformConfiguration v0.2、配置事务、校准 Web 控制台
- 兼容目标：不修改已经发布的实验目录和候选证据格式
- 实现范围：Python API、Web API、Web 交互、配置事务、审计、自动化测试

本文冻结“实验建议”和“最终更新决策”的分层。`recommendation_eligible` 只表达实验分析系统是否推荐，
不能再作为用户更新配置的绝对权限。自动化默认遵循推荐，用户或显式上层决策可以覆盖推荐，但不能绕过
证据完整性、配置合法性、并发保护和候选过期检查。

## 2. 背景与问题

当前 `calibration_candidate_v1` 同时保存：

1. 实验计算出的参数候选；
2. 实验质量策略给出的 `recommendation_eligible`；
3. 候选是否允许写回配置的实际权限。

Python API、配置存储层和 Web 都会拒绝 `recommendation_eligible=false` 的候选。这使“系统不推荐”错误地
等价于“用户禁止更新”，也无法支持用户检查、专家判断或 AI 辅助分析后的显式采用。

本阶段必须将三类事实拆开：

```text
实验计算事实：候选参数是什么
实验建议事实：质量策略是否推荐
应用决策事实：调用方最终是否采用
```

## 3. 设计原则

1. 候选值和质量指标继续由具体校准实验定义；通用配置层不解释 Rabi、频谱或 CZ 指标。
2. `recommendation_eligible` 保持不可变，是实验运行时绑定策略产生的建议结论。
3. 自动化脚本默认只应用推荐候选，保持 fail-closed 和向后兼容。
4. 显式覆盖必须选择具体 candidate ID、声明决策来源并提供非空原因。
5. Web 操作属于用户决策；不推荐候选必须警告，但不能隐藏更新入口。
6. 覆盖只绕过推荐结论，不能降低证据、并发、数据类型、Schema 或物理配置检查。
7. 所有覆盖更新必须进入配置审计和 source-candidate provenance。
8. 一个候选组的多参数修改继续原子提交，不允许部分成功。

## 4. 分层模型

### 4.1 候选层

继续使用 `calibration_candidate_v1`，不增加破坏性必填字段：

```text
candidate_id
candidate_type
calibration_subjects
configuration_resources
changes[]
source_dataset_sha256s[]
quality_metrics
recommendation_eligible
reason
```

`recommendation_eligible=false` 的候选仍是完整、可显示、可导出、可选择的候选。`reason` 表达实验系统不推荐
的原因，不表达配置层拒绝原因。

### 4.2 推荐层

具体实验负责：

- 计算候选值；
- 计算质量指标；
- 执行版本化质量策略；
- 写入 `recommendation_eligible`、`reason` 和 `quality_metrics`；
- 将 policy ID、policy SHA 和输入 dataset SHA 绑定到不可变 workflow。

本阶段不允许配置层重新拟合数据，也不允许配置层重新解释实验门限。

### 4.3 应用决策层

新增规范化决策对象：

```text
CandidateApplicationDecision
  mode: recommended_only | override_recommendation
  source: automation | notebook_user | web_user | ai_assisted
  reason: string | null
```

规则：

| mode | 允许候选 | candidate_ids | reason |
| --- | --- | --- | --- |
| `recommended_only` | 仅 `recommendation_eligible=true` | 可省略，省略时选择所有推荐候选 | 可选 |
| `override_recommendation` | 推荐和不推荐候选 | 必须显式提供且非空 | 必须为去空白后的非空字符串 |

`reason` 最长 2048 个 Unicode 字符，不允许控制字符。`source=automation` 不允许与
`override_recommendation` 组合，防止自动化静默扩大权限。AI 辅助分析若决定覆盖，必须使用
`source=ai_assisted` 并记录理由；调用方仍对该决策负责。

## 5. Python 公开接口

保留现有函数并兼容已有调用：

```python
def apply_calibration_candidates_to_current_configuration(
    run: Any,
    *,
    confirmation_phrase: str,
    candidate_ids: Sequence[str] | None = None,
    targets: Sequence[str] | None = None,
    decision_mode: Literal[
        "recommended_only",
        "override_recommendation",
    ] = "recommended_only",
    decision_source: Literal[
        "automation",
        "notebook_user",
        "web_user",
        "ai_assisted",
    ] = "automation",
    decision_reason: str | None = None,
    device_id: str = "demo_2q1c2r",
    actor_id: str = "notebook.user",
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    expected_current_content_sha256: str | None = None,
    operation_id: str | None = None,
) -> CalibrationCandidateUpdate:
    ...
```

### 5.1 自动化示例

```python
apply_calibration_candidates_to_current_configuration(
    run,
    confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
)
```

行为与当前接口一致：只选择推荐候选，没有推荐候选时拒绝。

### 5.2 Notebook 人工覆盖示例

```python
apply_calibration_candidates_to_current_configuration(
    run,
    confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
    candidate_ids=[candidate_id],
    decision_mode="override_recommendation",
    decision_source="notebook_user",
    decision_reason="检查曲线、泄漏和拟合残差后确认采用该值",
)
```

### 5.3 AI 辅助决策示例

```python
apply_calibration_candidates_to_current_configuration(
    run,
    confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
    candidate_ids=[candidate_id],
    decision_mode="override_recommendation",
    decision_source="ai_assisted",
    decision_reason="AI 审查完整曲线与同批次对照实验后建议采用，用户工作流授权执行",
)
```

## 6. Web API

现有端点保持不变：

```text
POST /api/v1/experiments/<run_id>/apply-current
POST /api/v1/experiments/<run_id>/draft
```

请求增加：

```json
{
  "candidate_ids": ["candidate-id"],
  "decision_mode": "override_recommendation",
  "decision_source": "web_user",
  "decision_reason": "用户检查实验数据后确认采用",
  "confirmation_phrase": "APPLY CALIBRATION CANDIDATES <run_id>",
  "actor_id": "project.manager",
  "expected_content_sha256": "...",
  "operation_id": "uuid4"
}
```

服务端不能根据按钮来源隐式信任请求。它必须重新验证 decision、workflow、候选和当前配置。

## 7. Web 用户体验

### 7.1 实验详情

- 推荐候选显示“推荐更新”；
- 不推荐候选显示“不推荐”，保留候选值、当前值、质量指标和原因；
- 有结构合法候选时均显示“更新当前配置”入口；
- “候选不可更新”只用于证据无效、无配置适配器、synthetic demo 或候选结构无效；
- `recommendation_eligible=false` 的列表状态改为“不推荐，可人工确认”。

### 7.2 更新对话框

推荐候选：

- 保留 candidate ID 多选；
- 保留目标配置选择；
- 保留普通确认复选框；
- 以 `recommended_only` 提交。

选择任一不推荐候选时：

- 显示醒目但非阻断的警告；
- 展示该候选的 `reason` 和已有失败质量门；
- 提交按钮文案变为“仍然更新”；
- 必须勾选“我理解系统不推荐该候选”；
- 必须填写覆盖原因；
- 以 `override_recommendation`、`web_user` 提交。

推荐和不推荐候选混合选择时，整个原子操作采用 `override_recommendation`。

## 8. 不可绕过边界

无论 decision mode 为何，以下情况必须拒绝：

1. 实验 workflow、dataset、receipt、manifest 或哈希验证失败；
2. workflow 没有注册的候选 verifier；
3. synthetic demo；
4. candidate schema、candidate ID、parameter path、resource 或数据类型无效；
5. candidate `current_value` 与当前配置不一致；
6. 当前配置 content SHA 已变化；
7. candidate 之间参数路径冲突；
8. 更新后 PlatformConfiguration Schema 或配置验证失败；
9. 多参数候选无法全部应用；
10. confirmation phrase、actor、operation ID 或 decision 无效。

覆盖更新不得自动修改候选中的 `recommendation_eligible`，不得修改已发布实验目录，也不得伪造新的质量结论。

## 9. 配置事务与审计

配置存储层不解释实验质量，只消费规范化 decision。现有事务幂等键继续绑定完整请求，新增 decision 后，
相同 operation ID 使用不同 decision、reason 或 candidate IDs 必须产生幂等冲突。

`source_candidate` 和审计事件增加：

```text
decision
  mode
  source
  reason
  overrode_recommendation

recommendation_snapshot
  candidate_id
  recommendation_eligible
  reason

experiment_run_id
recommendation_id
candidate_ids
targets
old_content_sha256
new_content_sha256
```

`overrode_recommendation=true` 当且仅当所选候选中存在 `recommendation_eligible=false`。该值由服务端计算，
不能由请求指定。

应用成功后继续：

1. 更新 mutable current；
2. 增加 revision；
3. 写入 source-candidate provenance；
4. 写入审计事件；
5. 自动保存 snapshot；
6. 自动激活 snapshot；
7. 按现有规则设置 `requires_requalification`。

## 10. 错误合同

新增或稳定化以下错误语义：

```text
candidate_decision_invalid
candidate_override_selection_required
candidate_override_reason_required
candidate_override_source_invalid
candidate_not_recommended
candidate_stale
candidate_configuration_conflict
candidate_update_invalid
```

现阶段内部异常仍可使用现有异常类，但 Python API 和 Web HTTP payload 必须提供稳定 machine-readable code，
不能只依赖英文 message。

## 11. 向后兼容

- 已发布 `calibration_candidate_v1` 不迁移；
- 旧 workflow 继续由原 verifier 验证；
- 未传 decision 字段的 Python 调用等价于 `recommended_only + automation`；
- 旧 Web 客户端不传 decision 时只能应用推荐候选；
- `recommendation_eligible` 保持必填，不改变实验 artifact hash；
- 频谱兼容 wrapper 继续默认只应用推荐候选；
- 不允许通过重新加载新 policy 将旧候选追认成推荐候选；需要新实验或独立的未来重评估 artifact。

## 12. 实现边界

### 12.1 通用后端

- 新增 decision 模型、规范化和验证；
- Python API 选择逻辑支持显式覆盖；
- 配置存储层只在 `recommended_only` 拒绝不推荐候选；
- 当前配置和 draft 两条写回路径采用相同 decision；
- 事务请求、source-candidate 和 audit 保存 decision；
- 保留全部不可绕过边界。

### 12.2 Web

- Web read model 区分“无效/不可应用”与“不推荐”；
- 不推荐候选仍提供更新入口；
- 更新对话框收集并提交 decision reason；
- Web server 验证 decision payload；
- 显示实验已有质量门和候选原因。

### 12.3 独立测试与审查

- 建立候选决策验收矩阵；
- 独立覆盖 Python、Web、事务、幂等、审计和回归；
- 至少执行一次真实已发布实验候选的推荐模式与覆盖模式验证；
- 检查覆盖更新后的 current、snapshot、active pointer 和 audit 一致性。

## 13. 必需测试

### 13.1 Python API

1. 默认模式应用推荐候选；
2. 默认模式拒绝不推荐候选；
3. 覆盖模式显式应用不推荐候选；
4. 覆盖模式缺少 candidate IDs、reason 或合法 source 时拒绝；
5. `automation + override` 拒绝；
6. AI-assisted 覆盖保存来源和原因；
7. 推荐与不推荐混合候选原子应用；
8. targets 和 candidate IDs 的互斥规则保持；
9. 频谱兼容 wrapper 不发生行为回归。

### 13.2 不可绕过边界

1. synthetic demo 覆盖仍拒绝；
2. artifact 篡改仍拒绝；
3. stale candidate 仍返回冲突；
4. current hash 冲突仍拒绝；
5. 重复 path 和资源不匹配仍拒绝；
6. Schema 无效仍拒绝；
7. 多参数候选中一个失败时零参数写入；
8. 相同 operation ID、不同 decision 产生幂等冲突。

### 13.3 Web

1. 不推荐候选显示原因和更新按钮；
2. 不填写覆盖原因不能提交；
3. 推荐候选仍走默认模式；
4. 混合选择走覆盖模式；
5. server 拒绝伪造或不完整 decision；
6. 成功后 current、snapshot 和 active 状态刷新；
7. synthetic demo 不显示更新入口。

### 13.4 回归

- candidate protocol；
- calibration API；
- PlatformConfiguration v0.2；
- configuration transaction；
- spectroscopy；
- Rabi；
- calibration Web API/UI；
- storage lifecycle；
- notebook import 和公开接口。

## 14. 验收标准

本阶段完成必须同时满足：

1. 自动化默认行为没有放宽；
2. Python 能显式覆盖不推荐候选；
3. Web 用户能在看到警告后显式覆盖；
4. `recommendation_eligible` 保持实验建议语义且不被改写；
5. 覆盖不能绕过任何硬安全边界；
6. 决策来源、原因、候选建议状态和配置前后哈希可审计；
7. current、snapshot、active pointer 和事务 receipt 一致；
8. 独立测试 AI 完成验收矩阵并给出明确结论；
9. 相关快速测试、Web 测试和配置事务测试全部通过；
10. 至少一条端到端候选更新路径通过；
11. 开发日志记录实际修改、测试和已知限制。

## 15. 团队分工

### PM / 设计与集成

- 冻结本设计和公共接口；
- 控制任务文件边界；
- 解决实现偏差；
- 集成后执行最终验收；
- 更新开发日志和结果说明。

### 后端与事务 AI

- 实现 decision、Python API、配置存储、事务和审计；
- 编写后端单元与集成测试；
- 不修改 Web UI。

### Web 与交互 AI

- 实现 read model、server payload 和前端交互；
- 编写 Web API/UI 测试；
- 不修改后端候选决策核心。

### 独立测试与审查 AI

- 从本设计建立独立验收矩阵；
- 审查两条实现；
- 补充遗漏的边界测试；
- 运行分层回归并记录失败和残余风险。

## 16. 已知限制

- 首版不提供跨用户权限和审批流；
- 首版不支持在原实验上重新运行新 policy 并生成追认建议；
- AI-assisted 表达决策来源，不代表平台验证了 AI 分析质量；
- 强制更新后的参数仍可能物理质量较差，系统只保证证据和配置一致性，不保证用户决策正确；
- Web 用户身份继续使用当前单用户 actor 模型。

## 17. PM 冻结的契约细节

以下规则用于消除实现与验收之间的歧义，优先于对错误 message 的推断：

1. `override_recommendation` 只接受显式、非空的 `candidate_ids`；不能用 `targets` 代替。缺少或空列表返回
   `candidate_override_selection_required`。重复 ID、未知 ID、同时提交 `candidate_ids` 与 `targets` 返回
   `candidate_update_invalid`。
2. `reason` 先去除首尾空白，再按 Unicode code point 计数，最大 2048；Unicode General Category 为
   `Cc` 的字符不允许。Web 只能做同语义的预检查，服务端是最终权威。
3. `recommended_only` 可以携带非空 reason；规范化后写入 decision 和审计，但不改变
   `overrode_recommendation` 的计算。
4. current 更新继续进入现有配置事务，以完整的规范化 decision 参与 request SHA 和幂等判断。draft
   端点沿用现有生命周期：从实验绑定的父配置创建新草稿，再在单次 Store 调用内原子应用候选组；首版不为
   draft 增加 operation ID 或跨请求幂等语义。候选组应用失败时不能留下部分参数或 candidate provenance。
5. Python 使用 `CalibrationExperimentError.code`，Store 使用 `ConfigurationManagementError.code`，Web 使用
   JSON `code`。decision/selection/schema 错误为 HTTP 422，stale/current conflict/idempotency conflict 为
   HTTP 409。
6. `source_candidate` 仍保持旧记录可读。新记录必须同时含 `decision` 与
   `recommendation_snapshot`；引用扫描器按“旧严格键集合”或“新严格键集合”验证，不能接受任意附加键。
   `overrode_recommendation` 只由服务端根据实际选择计算；请求中的同名字段不参与持久化。
7. 非 synthetic 的不推荐候选验收夹具应由生产 artifact writer 和已注册 verifier 生成。允许使用确定性的
   模型仿真运行并绑定未通过的版本化质量策略；不允许直接编辑 workflow 或候选 JSON 制造 `N`。
