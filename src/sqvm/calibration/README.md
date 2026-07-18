# Calibration package

所有校准实验的实现统一放在本包中。

```text
calibration/
├─ api.py                    绑定 Active 配置的用户级入口
├─ spectroscopy.py           频谱线路生成、执行与分析
└─ spectroscopy_workflow.py  粗扫、细扫、确认、门限与候选工件
```

后续新增实验时使用独立模块，例如 `rabi.py`、`ramsey.py`、`drag.py`、
`coupler.py` 和 `cz.py`。校准模块只能通过公共 `run_circuits` 接口执行线路，
不能直接调用 QuTiP worker。

`sqvm.experiments` 和 `sqvm.calibration_api` 仅作为旧调用路径的兼容层，
新代码统一从 `sqvm.calibration` 导入。
