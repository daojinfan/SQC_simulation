# SQC Simulation

SQC Simulation 是一个面向超导量子计算校准流程的虚拟实验平台。项目以最小
`2q1c2r` 器件为对象，从芯片物理参数、哈密顿量、控制电子学和 QuTiP 时间演化出发，
通过 QCIS 线路执行校准实验，并把配置、原始数据、分析结果和证据展示在本地 Web
控制台中。

```text
q1, q2  两个量子比特
c       一个可调耦合器
r1, r2  两个读出谐振腔
```

当前项目处于校准平台的 bounded pilot 阶段。器件、哈密顿量、静态能谱、控制链、
QuTiP 演化、QCIS 编译和通用 `run_circuits` 接口已经建立；比特频谱是目前第一项完整
打通的校准实验。

## 当前能力

- 从电容、Josephson 结和磁通参数构建 2Q1C 哈密顿量。
- 计算静态能谱、dressed state、非谐性、residual ZZ 和耦合扫描。
- 编译 QCIS 指令并展开为 XY、Z 和读出通道上的采样波形。
- 模拟 DAC 量化、延迟、FIR、静态混频、串扰和 clipping 等控制电子学链。
- 使用 QuTiP 执行时间演化并保存 population、leakage 和完整性证据。
- 通过 `run_circuits` 执行任意受支持的 QCIS 线路，并选择 singleton 或 joint readout
  probability。
- 运行单比特频谱或 Q1/Q2 并行频谱，自动完成粗扫、细扫、确认扫描、门限检查和候选
  频率生成。
- 在本地 Web 控制台编辑当前配置、保存或恢复快照，并查看实验数据和证据。
- 从实验详情导出 JSON 或 CSV 数据。

## 系统结构

```text
器件配置
  -> 2Q1C Hamiltonian
  -> 静态能谱与 dressed basis
  -> QCIS parser/compiler
  -> logical pulse 与控制电子学链
  -> QuTiP 时间演化
  -> run_circuits
  -> 校准实验与数据分析
  -> 不可变实验工件
  -> Web 配置管理与结果查看
```

核心目录如下：

```text
SQC_simulation/
├─ configs/                  器件、控制、演化、运行时和验收配置
├─ docs/                     阶段计划、详细设计、决策、结果和开发日志
├─ scripts/                  阶段验证脚本与 Web 服务入口
├─ src/sqvm/
│  ├─ device/               器件模型与参数验证
│  ├─ hamiltonian/          2Q1C 哈密顿量
│  ├─ spectrum/             静态能谱与耦合扫描
│  ├─ qcis/                 QCIS 解析、编译、波形与验证
│  ├─ control/              控制波形和电子学链
│  ├─ evolution/            QuTiP 时间演化
│  ├─ calibration/          校准实验、工作流和用户级 API
│  ├─ runtime/              Stage 6 运行、恢复和证据框架
│  ├─ runtime_v02/          Runtime v0.2 实现
│  ├─ web/                  本地配置与实验结果控制台
│  └─ circuits.py           run_circuits 统一线路接口
├─ tests/                    自动化测试
├─ user/                     用户可直接运行的 Jupyter Notebook
├─ output/                   本机配置、快照、实验结果和验证工件
├─ pyproject.toml            Python 包与依赖
└─ start_calibration_web.cmd Windows Web 启动程序
```

所有新的校准代码统一放在 `src/sqvm/calibration/`。`sqvm.experiments` 和
`sqvm.calibration_api` 仅保留旧调用路径兼容，新代码应从 `sqvm.calibration` 导入。

## 环境安装

要求 Python 3.11 或更高版本。已记录的 Stage 5 复现环境使用 CPython 3.12.10。

在 PowerShell 中执行：

```powershell
cd D:\Codex\SQC_simulation
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[dev]"
```

验证安装：

```powershell
.venv\Scripts\python -c "import sqvm; print(sqvm.__version__)"
.venv\Scripts\python -m pytest -q
```

## 用户 Notebook

用户调用入口统一放在 `user/`：

| Notebook | 用途 |
| --- | --- |
| [`01_qubit_spectroscopy.ipynb`](user/01_qubit_spectroscopy.ipynb) | 单比特或双比特并行频谱校准 |

Notebook 会自动定位项目根目录并从 `sqvm.calibration` 导入公共接口。真实 QuTiP 扫描
可能耗时较长，因此 `RUN_EXPERIMENT=False` 是默认值；检查扫描轴和波形参数后，显式改为
`True` 才会开始实验。

推荐从项目根目录启动 Jupyter：

```powershell
.venv\Scripts\python -m pip install jupyterlab
.venv\Scripts\python -m jupyter lab user
```

## Python 校准 API

Web 不负责启动实验。校准实验通过 Python API 运行，结果写入
`output/experiments/`，Web 会自动发现已经发布的结果。

下面的示例运行 Q1/Q2 并行频谱：

```python
from sqvm.calibration import (
    SpectroscopyAxis,
    SpectroscopyCalibrationPolicy,
    SpectroscopyCalibrationRequest,
    SpectroscopyMode,
    SpectroscopyPulsePolicy,
    SpectroscopyRequest,
    run_active_qubit_spectroscopy_calibration,
)

coarse_request = SpectroscopyRequest(
    execution_mode=SpectroscopyMode.PARALLEL_LOCKSTEP,
    run_phase="coarse",
    targets=("Q1", "Q2"),
    axes=(
        SpectroscopyAxis("Q1", (4.90, 5.00, 5.10)),
        SpectroscopyAxis("Q2", (5.10, 5.20, 5.30)),
    ),
    pulse_policies=(
        SpectroscopyPulsePolicy("Q1", 12, 0.02, 2.5),
        SpectroscopyPulsePolicy("Q2", 12, 0.02, 2.5),
    ),
)

request = SpectroscopyCalibrationRequest(
    coarse_request=coarse_request,
    policy=SpectroscopyCalibrationPolicy(
        fine_span_GHz=0.10,
        fine_points=5,
        confirmation_span_GHz=0.08,
        confirmation_points=5,
        min_contrast=0.01,
    ),
)

result = run_active_qubit_spectroscopy_calibration(
    request,
    device_id="demo_2q1c2r",
    timeout_s=600.0,  # 每个 QuTiP worker 的 watchdog
    progress_callback=lambda event: print(dict(event)),
)

print(result.run_id)
print(result.root)
print(result.recommendation_eligible)
print(dict(result.candidates))
```

单比特扫描使用 `SpectroscopyMode.SINGLE`，并只提供一个 target、axis 和 pulse policy。
粗扫、细扫和确认扫描仍由同一个 API 编排。

本地校准扫描使用已批准的 Stage 4.1 控制链和 Stage 5.1 solver，但每个扫描点只运行一次
隔离 worker，不在同步实验路径中重复执行资格审核所需的数值重放。每点证据都会明确记录
`numerical_replay=deferred`；这类结果只用于本模拟器配置，不代表硬件测量或正式规模物理
authority。当前严格 solver 的实测单点耗时约为两分钟，完整双比特频谱可能需要半小时以上。

频率、幅度等直接波形参数由实验请求写入 QCIS；需要扫描配置表字段时使用 QCIS
`SET` overlay。`SET` 只作用于当前 circuit，不会直接修改当前配置或 Active 快照。

## `run_circuits` 接口

所有校准实验最终都通过 `run_circuits` 执行 QCIS。实验模块不应直接调用控制链、
Hamiltonian 内部接口或 QuTiP worker。

`readout_qubit` 使用嵌套列表选择返回结果：

```python
readout_qubit=[[]]                  # 所有量子比特分别返回 P0/P1
readout_qubit=[["Q1"], ["Q2"]]    # 分别返回 Q1、Q2 的 P0/P1
readout_qubit=[["Q1", "Q2"]]      # 返回 P00/P01/P10/P11
```

当前结果是 dressed computational population，不是硬件 shot、IQ 或 assignment readout。

## Web 校准控制台

Windows 下可以直接双击 `start_calibration_web.cmd`，也可以在 PowerShell 中运行：

```powershell
.\start_calibration_web.cmd
```

默认地址：<http://127.0.0.1:8765>

更换端口或禁止自动打开浏览器：

```powershell
.\start_calibration_web.cmd --port 8877 --no-browser
```

脚本入口：

```powershell
.venv\Scripts\python scripts\run_calibration_web.py
```

Web 的职责边界：

- 可以查看和编辑当前配置中的控制及校准参数。
- 可以保存配置快照、长期保留快照或把快照恢复到当前配置。
- 可以查看实验列表、扫描曲线、门限、候选值和底层证据。
- 不启动 QuTiP 实验，不在浏览器中重新拟合数据。
- 不开放电容、电感等器件物理 authority 的修改。
- 实验候选不能直接覆盖当前配置，参数更新仍需要显式确认。

## 配置与实验数据

本机配置数据位于：

```text
output/platform-configurations/
├─ current/       Web 中直接编辑的当前工作配置
├─ snapshots/     不可修改的配置快照
├─ active/        当前实验入口使用的 Active 快照指针
├─ drafts/        旧版 Draft 兼容数据
├─ pins/          用户要求长期保留的快照
└─ audit/         配置操作审计
```

当前 Web 主流程使用“当前配置 + 快照”模型；当前
`run_active_qubit_spectroscopy_calibration` 仍要求设备恰好存在一个合法的 Active 快照。
修改当前配置不会静默改变正在使用的实验 authority。

实验工件位于：

```text
output/experiments/<experiment_run>/
├─ workflow.json
├─ receipt.json
├─ spectroscopy.png
└─ datasets/
   ├─ coarse.json
   ├─ refined.json
   └─ confirmation.<target>.json
```

请求、分析、门限和候选参数记录在 `workflow.json` 中。具体目录会随工作流版本增加其他
证据文件。验证器以工件内容和 SHA-256 绑定为准，Web 不是数据 authority。

`output/` 默认被 Git 忽略，其中的当前配置、快照和实验数据只保存在本机。需要迁移或
备份实验环境时，必须单独备份该目录。

## 测试

运行全部测试：

```powershell
.venv\Scripts\python -m pytest -q
```

只运行当前校准、配置和 Web 主链测试：

```powershell
.venv\Scripts\python -m pytest -q `
  tests/test_calibration_api.py `
  tests/test_calibration_web.py `
  tests/test_platform_configuration_v02.py `
  tests/test_spectroscopy_calibration_workflow.py `
  tests/test_qubit_spectroscopy.py `
  tests/test_user_notebooks.py
```

## Stage 5 v0.2 复现

需要严格复现已记录的 Stage 5 smoke 证据时，使用锁定依赖：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements-stage5-lock.txt
.venv\Scripts\python -m pip install -e . --no-deps
.venv\Scripts\python -m pytest -q tests/test_stage5_evolution.py
.venv\Scripts\python -m sqvm verify-evolution `
  configs/evolution/2q1c_qutip_smoke.yaml `
  --output output/stage_05_qutip_evolution_smoke_v02_reproduced
```

对应的范围、哈希和审核记录位于
[`docs/results/2026-07-14-stage5-v0-2-smoke.md`](docs/results/2026-07-14-stage5-v0-2-smoke.md)。
Smoke 完成不等于生产物理后端通过正式规模验收。

## 当前限制

- 目前只支持固定的 2Q1C2R 模型拓扑。
- `bounded_smoke` 仍用于入口资格复验；用户频谱默认使用单次 worker 的
  `local_calibration_scan_v1`，两者都不是硬件或生产物理 authority。
- 当前初态固定为 `lab_ground`，observable 主要是 dressed computational population。
- 尚未实现真实 shot、IQ、assignment matrix 和读出噪声模型。
- Rabi、Ramsey、DRAG、Coupler 和 CZ 等校准实验尚未接入完整工作流。
- 正式 QuTiP 扫描可能需要数分钟处理一个点，不适合用作即时 UI 操作。
- `runtime`、`runtime_v02` 以及部分 Stage 4/5 双版本仍待后续架构收敛。

## 文档入口

建议按以下顺序了解项目：

1. [`docs/00_project_vision.md`](docs/00_project_vision.md)
2. [`docs/10_development_process.md`](docs/10_development_process.md)
3. [`docs/20_roadmap.md`](docs/20_roadmap.md)
4. [`docs/designs/07_qcis_compiler_design.md`](docs/designs/07_qcis_compiler_design.md)
5. [`docs/designs/07_1_0_run_circuits_design.md`](docs/designs/07_1_0_run_circuits_design.md)
6. [`docs/designs/07_1_1_qubit_spectroscopy_design.md`](docs/designs/07_1_1_qubit_spectroscopy_design.md)
7. [`docs/designs/07_1_5_current_configuration_workbench_v2.md`](docs/designs/07_1_5_current_configuration_workbench_v2.md)
8. [`docs/designs/07_1_6_local_calibration_scan_execution.md`](docs/designs/07_1_6_local_calibration_scan_execution.md)
9. [`docs/logs/DEVELOPMENT_LOG.md`](docs/logs/DEVELOPMENT_LOG.md)
