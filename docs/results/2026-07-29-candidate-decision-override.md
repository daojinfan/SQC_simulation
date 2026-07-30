# 校准候选决策覆盖实现结果

- 日期：2026-07-29
- 范围：通用校准候选的推荐判断、人工决策与配置写回
- 状态：`completed / independent GO`
- 详设：`docs/designs/07_1_14_calibration_candidate_decision_override.md`
- 独立验收：`docs/reviews/07_1_14_candidate_decision_override_acceptance_matrix.md`

## 实现结果

本阶段将“实验是否推荐”和“用户是否决定采用”拆成两层。自动化与旧调用默认保持
`recommended_only`，只应用推荐候选；Notebook、Web 用户或 AI 辅助流程可以通过显式 candidate IDs、
决策来源和原因选择 `override_recommendation`。覆盖只绕过推荐结论，不能绕过 workflow/receipt/hash、
synthetic、stale、current SHA、资源归属、Schema、原子事务或审计边界。

Python API、Web API 和配置 Store 统一消费规范化 decision。current 与 draft 采用相同选择规则；current
事务把 decision 纳入 request SHA 和幂等判断。source-candidate 与 audit 保存推荐快照、决策、前后配置
SHA；references 同时严格兼容旧记录和新的完整键集合。Web 将“不推荐，可人工确认”与“候选不可更新”
分开，并要求风险确认、覆盖原因和写入确认三项齐全后才允许提交。

## 团队分工

```text
PM / 集成              详设、契约冻结、references、风险修复、实机验收和交付收口
后端与事务 AI          decision、Python API、Store、事务、provenance/audit 和后端测试
Web 与交互 AI          read model、HTTP payload、人工覆盖对话框和 Web 测试
独立测试与审查 AI      验收矩阵、代码审查、分层回归、R/N 真实 artifact 端到端和 GO/NO-GO
```

## 验证

独立测试结果：

```text
候选 API、PlatformConfiguration、事务     60 passed
Web API/UI                                  35 passed
references                                  22 passed
retention 专项                               1 passed
Rabi 校准/runtime/storage                    30 passed
频谱                                        22 passed
Notebook                                     3 passed
compileall / node --check / git diff --check passed
```

Rabi archive/restore 的真实 worker 单例耗时 115.64 秒并通过。该耗时属于测试资源规划风险，不是行为失败。

已发布 artifact 隔离端到端：

```text
推荐默认路径  3df4ccf6-6817-481c-82ee-80a02dca3e5c
不推荐覆盖    f447aa97-c484-4ccf-8e2f-212a8efc0a08
```

两条路径均在临时 configuration root 完成，未修改正式配置或实验目录。workflow/receipt SHA 保持不变，
current、snapshot sidecar、active pointer、audit、transaction request SHA 和 references 一致。

真实 Web 页面另行验证了不推荐候选的更新入口、原因和失败门限展示，以及三项确认门禁；桌面和窄屏均无
横向溢出或控件重叠。验收未点击最终提交，不影响正式 current。

## 审查结论

独立结论为 `GO`。审查中发现的 candidate provenance retention 问题已修复：后续人工编辑只保留候选
子集时，同步裁剪 recommendation snapshot，并按保留候选重算 `overrode_recommendation`；专项回归通过。
当前没有未解决的 blocker、事务一致性或不可绕过边界问题。
