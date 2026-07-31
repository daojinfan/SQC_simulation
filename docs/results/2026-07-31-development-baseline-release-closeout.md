# 开发基线发布收尾

- 日期：2026-07-31
- 范围：Rabi/X2P 开发基线的 PR、远端门禁与本地计数修复收口
- 状态：`实施中 / pending`
- PR：[#9](https://github.com/daojinfan/SQC_simulation/pull/9)

## 候选基线

PR #9 当前从 `codex/rabi-x2p-calibration` 指向 `main`，仍为 open 且未合并。远端 PR head 为
`7d479845139e5aeda7d8254dec8729c6711fa742`（短 SHA `7d47984`）。本工作包启动时的本地 HEAD 为
`825b24dacc5c0d92f1115da446e0629d8a73d626`（短 SHA `825b24d`），比远端多一个 physics suite
计数锁修复提交；该提交尚未推送。

`825b24d` 只把 `tests/physics_suite_lock.json` 与 `tests/test_test_taxonomy.py` 中的预期 physics
testcase 数从 204 更新为 206，使计数锁与已收集的测试集合一致。本报告不修改测试或锁。

## 远端验证状态

远端 `7d47984` 的 PR qualification 全部通过：Windows/Linux contract、Windows/Linux integration
和 fixture integrity 均为 pass。hosted evidence 的 authority drift、historical fixture byte match、
historical output regression 与 Stage 4 v1 byte contract 均为 pass；历史 Stage 4 v1 Notebook
execution 按工作流合同跳过。

physics 的 Windows 与 Linux job 都完成了实际测试：两平台各为 `206 passed, 1430 deselected`。
随后计数验证仍读取旧锁 204，均以 `expected 204, executed 206` 失败。因此远端 physics job 和
下游 `release-gate-main` 当前显示 fail；这不是测试用例失败，但也不能记为全绿。

## 本地收尾验证

本地文档与计数修复基线验证通过：

```powershell
git diff --check                                                          passed
py -3.12 -m compileall -q src tests                                       passed
py -3.12 tools/verify_authority_drift.py                                  passed
py -3.12 -m pytest -q tests/test_user_notebooks.py tests/test_test_taxonomy.py
                                                                          6 passed
```

这些是文档工作包的定向本地检查，不替代尚未重跑的远端双平台 physics 与 release gate。

## 发布边界

最终候选状态：`pending`。

全绿状态：`pending`。必须先把包含 `825b24d` 与本次文档提交的候选推送到远端，并由新的远端运行
重新证明 qualification、hosted evidence、Windows/Linux physics 和 release gate 全部通过。

`main` 尚未合并，本项目尚未发布。本工作包不执行 push、PR 更新、merge 或 release，也不把当前
状态解释为 `GO`。
