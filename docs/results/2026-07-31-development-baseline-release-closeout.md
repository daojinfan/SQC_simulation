# 开发基线发布收尾

- 日期：2026-07-31
- 范围：Rabi/X2P 开发基线、PR #9、跨平台门禁与 v0.1.0 发布准备
- 状态：`开发基线已完成 / v0.1.0 release candidate`
- PR：[#9](https://github.com/daojinfan/SQC_simulation/pull/9)
- 产品版本：`0.1.0`，Git tag `v0.1.0`

## 最终基线

PR #9 的最终 head 为
`0fbf7181abc1e528e3ed56564ec20ec24b3138fd`，已于 2026-07-31 squash merge 到 `main`；合并提交为
`c267e84f6f8b0a932f1b385d2cf9497766a6b28b`。GitHub 返回的 PR 状态为 `MERGED`。

本次基线包括 Rabi/X2P 校准工作流、公开运行与取消操作、Notebook/Web 视图、波形展示、物理测试计数锁
更新，以及 Windows registrar 并发锁竞争修复。`pyproject.toml` 和 `sqvm.__version__` 均为 `0.1.0`。
文档中的 QCIS v0.3、Runtime 0.3 等名称属于子协议或工件版本，不是产品 Release 版本。

## 问题闭环

首轮候选 `0835842f96f9b3b8e08a67e38fcfad4b758da1cc` 的 Release Gate
[`30601295041`](https://github.com/daojinfan/SQC_simulation/actions/runs/30601295041) 中，Windows/Linux physics
均通过，证明 `825b24d` 已将 physics testcase 计数锁从 204 更新到实际的 206。该轮唯一失败为 Windows
integration 的 `test_concurrent_enqueue_publishes_exactly_one_event`：并发 `.lock` 已存在时，Windows
可能从 `os.open(O_EXCL)` 返回 `PermissionError`，旧实现只识别 `FileExistsError`。

提交 `5d0f7a1c2c1e4526b265f6e27285fd41e74ad273` 对 Windows 锁创建竞争进行了 fail-closed 修复：只有在
锁路径经验证为根目录内安全的普通单链接文件时，Windows 的创建期 `PermissionError` 才按锁竞争处理；
其他权限错误继续失败关闭。修复没有引入 stale-lock recovery，也没有放宽 write、flush、fsync、replace
或 release 阶段的权限错误。

## 远端验证

PR 最终候选 `0fbf7181` 的 Release Gate
[`30602658456`](https://github.com/daojinfan/SQC_simulation/actions/runs/30602658456) 完整通过：候选身份、
fixture integrity、Windows/Linux contract、Windows/Linux integration、Windows/Linux physics、authority
drift、historical fixture byte match、historical output regression、Stage 4 v1 byte contract 和最终
`release-gate-main` 均为 success。历史 Stage 4 v1 Notebook execution 按工作流合同跳过，不是发布依赖。

合并提交 `c267e84f` 随后通过手动精确候选 Release Gate
[`30608406905`](https://github.com/daojinfan/SQC_simulation/actions/runs/30608406905)。工作流验证该 SHA 可从
`main` 到达，并在合并后的主分支字节上重新通过全部同候选门禁和 `release-gate-main`。

## 本地与独立验证

收尾过程执行并通过以下定向验证：

```powershell
git diff --check                                                          passed
py -3.12 -m compileall -q src tests                                       passed
py -3.12 tools/verify_authority_drift.py                                  passed
py -3.12 -m pytest -q tests/test_user_notebooks.py tests/test_test_taxonomy.py
                                                                          6 passed
```

registrar 修复的独立 QA 包括 registrar 文件 `11 passed`、相关 Web integration `83 passed`、原并发用例
连续 30 次通过、taxonomy `3 passed`，以及 monkeypatch 恢复、compileall、authority drift 和 diff check。
最终 Windows integration 已在两个远端成功候选上复验。

## 发布边界

`v0.1.0` 是 bounded-pilot 产品版本。它不声明已交付 Ramsey、DRAG、coupler/CZ 校准，也不声明已有
硬件式 shot/IQ/readout 能力；这些仍属于后续路线。Git tag 必须指向 `main` 历史上的最终发布提交，
不得指向 squash 前的 PR head。正式发布状态、tag 目标和发布时间以 GitHub Release `v0.1.0` 为准。
