# 开发基线发布收尾

- 日期：2026-07-31
- 范围：Rabi/X2P 开发基线的 PR、远端门禁与本地计数修复收口
- 状态：`实施中 / pending`
- PR：[#9](https://github.com/daojinfan/SQC_simulation/pull/9)

## 候选基线

PR #9 当前从 `codex/rabi-x2p-calibration` 指向 `main`，仍为 open 且未合并。远端 PR head 为
`0835842f96f9b3b8e08a67e38fcfad4b758da1cc`（短 SHA `0835842f`）。`825b24d` 的 physics suite
计数修复和首版发布收尾文档已经一同推送，形成首轮新候选 `0835842f`。

`825b24d` 只把 `tests/physics_suite_lock.json` 与 `tests/test_test_taxonomy.py` 中的预期 physics
testcase 数从 204 更新为 206，使计数锁与已收集的测试集合一致。本报告不修改测试或锁。

首轮候选发现的 Windows registrar 并发问题已在本地提交
`5d0f7a1c2c1e4526b265f6e27285fd41e74ad273`（短 SHA `5d0f7a1c`）中修复并通过独立 QA，
但该修复尚未推送，不属于 run `30601295041` 的远端候选字节。

## 远端验证状态

首轮新候选 `0835842f` 的 Release Gate run 为
[`30601295041`](https://github.com/daojinfan/SQC_simulation/actions/runs/30601295041)。Windows/Linux
physics 均通过，证明 `825b24d` 已把 206 testcase 计数锁关闭。

除 Windows integration 外，qualification 与 hosted evidence 的其余门均通过，包括
Windows/Linux contract、Linux integration、fixture integrity、authority drift、historical fixture
byte match、historical output regression 与 Stage 4 v1 byte contract；历史 Stage 4 v1 Notebook
execution 按工作流合同跳过。

Windows integration 为 `592 passed, 1 failed, 1043 deselected`。唯一失败是
`test_concurrent_enqueue_publishes_exactly_one_event`：registrar 在并发 `.lock` 已存在时执行
`os.open(O_EXCL)`，Windows 返回 `PermissionError`，旧协议只把 `FileExistsError` 识别为锁竞争。
因此 run `30601295041` 的 Windows integration 与 `release-gate-main` 未通过；首轮候选不是全绿。

## 本地收尾验证

首版文档与计数修复基线的定向本地验证通过：

```powershell
git diff --check                                                          passed
py -3.12 -m compileall -q src tests                                       passed
py -3.12 tools/verify_authority_drift.py                                  passed
py -3.12 -m pytest -q tests/test_user_notebooks.py tests/test_test_taxonomy.py
                                                                          6 passed
```

`5d0f7a1c` 的 registrar 修复已通过独立 QA：registrar 文件 `11 passed`，相关 Web integration
`83 passed`，原并发用例连续 30 次通过，taxonomy `3 passed`，同进程 monkeypatch 恢复检查为
`True`；定向 compileall、authority drift 和 diff check 均通过。该本地 QA 不替代新候选的远端
Windows integration 与 release gate。

## 发布边界

最终候选状态：`pending`。

全绿状态：`pending`。下一候选必须包含 `5d0f7a1c` 与本次文档提交，并由新的远端运行重新证明
Windows integration 以及全部同候选门禁通过；下一候选和 `release-gate-main` 当前均为 pending。

`main` 尚未合并，本项目尚未发布。本工作包不执行 push、PR 更新、merge 或 release，也不把当前
状态解释为 `GO`。
