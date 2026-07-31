# Calibration package

所有校准实验的实现统一放在本包中。

```text
calibration/
├─ api.py                    绑定 Active 配置的用户级入口
├─ rabi.py                   X2P + X2P 幅度扫描、分析、候选与 Runtime 0.3 适配
├─ rabi_phase.py             Rabi 双脉冲时序与相位审计
├─ rabi_reader.py            Rabi 发布证据验证
├─ spectroscopy.py           频谱线路生成、执行与分析
├─ spectroscopy_reader.py    频谱发布证据验证
├─ spectroscopy_run.py       频谱 Runtime 0.3 批执行适配
└─ spectroscopy_workflow.py  粗扫、细扫、确认、门限与候选工件
```

比特频谱与 Rabi/X2P 幅度校准已经实现。用户级 Rabi 入口为 `run_rabi`，跨进程协作取消
入口为 `cancel_rabi`，对应 Notebook 为 `user/02_x2p_rabi_calibration.ipynb`。Ramsey、DRAG、
Coupler 和 CZ 校准实验仍未实现；后续新增时使用独立模块。校准模块只能通过公共
`run_circuits` 接口执行线路，不能直接调用 QuTiP worker。

面向用户的校准 API 使用 `CircuitExecutionProfile.CALIBRATION_SCAN`。该档位每点运行一次
严格 Stage 5.1 worker，并把独立数值重放标记为延迟批后复验；底层 `run_circuits` 默认
仍保持 `BOUNDED_SMOKE`。频谱和 Rabi 均通过 Runtime 0.3 统一完成全批预编译、逐点提交、
恢复、deadline、取消和幂等重开。

Web 只读取和展示频谱/Rabi 结果、证据、绘图与候选，并通过通用候选事务执行显式人工决策；
它不在请求内启动 QuTiP。实验存储 v1 已支持 catalog、归档/恢复、可恢复回收站和受限归档读取，
但自动保留、cleanup preview/apply、永久 purge 和配额执行尚未实现。当前 readout 仍是 dressed
computational population，不是硬件 shot、IQ、assignment 或读出噪声模型。

`sqvm.experiments` 和 `sqvm.calibration_api` 仅作为旧调用路径的兼容层，
新代码统一从 `sqvm.calibration` 导入。
