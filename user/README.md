# 用户调用入口

这里仅放面向使用者的 Notebook，不放校准算法实现。

| Notebook | 用途 | 状态 |
| --- | --- | --- |
| `01_qubit_spectroscopy.ipynb` | 单比特或双比特并行频谱校准 | 可运行 |

Notebook 通过 `sqvm.calibration` 公共接口运行实验。实验结果写入
`output/experiments/`，随后可以在校准 Web 控制台中查看。

真实 QuTiP 扫描可能耗时较长。Notebook 默认将 `RUN_EXPERIMENT` 设为
`False`；确认参数后显式改为 `True` 才会开始运行。
