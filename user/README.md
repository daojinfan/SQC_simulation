# 用户调用入口

这里仅放面向使用者的 Notebook，不放校准算法实现。

| Notebook | 用途 | 状态 |
| --- | --- | --- |
| `01_qubit_spectroscopy.ipynb` | 单比特或双比特并行频谱扫描 | 可运行 |

首次使用或更换 Python 环境后，先运行：

```powershell
.\user\setup_environment.cmd
```

该脚本把当前项目安装为 editable package，并验证
`from sqvm.calibration import run_spectroscopy`。Notebook 随后可以直接通过
`sqvm.calibration` 公共接口运行实验。实验结果写入
`output/experiments/`，随后可以在校准 Web 控制台中查看。

真实 QuTiP 扫描可能耗时较长。Notebook 的 `RUN_EXPERIMENT=True` 会开始运行；需要先
检查导入和参数时可改成 `False`。运行后逐点打印进度和 circuit ID，每个 worker 的
watchdog 上限为 600 秒。
