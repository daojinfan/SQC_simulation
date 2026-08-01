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
QuTiP 演化、QCIS 编译和通用 `run_circuits` 接口已经建立；比特频谱与 Rabi/X2P
幅度扫描是目前已经完整打通的两项校准实验。

## 当前能力

- 从电容、Josephson 结和磁通参数构建 2Q1C 哈密顿量。
- 计算静态能谱、dressed state、非谐性、residual ZZ 和耦合扫描。
- 编译 QCIS 指令并展开为 XY、Z 和读出通道上的采样波形。
- 模拟 DAC 量化、延迟、FIR、静态混频、串扰和 clipping 等控制电子学链。
- 使用 QuTiP 执行时间演化并保存 population、leakage 和完整性证据。
- 通过 `run_circuits` 执行任意受支持的 QCIS 线路，并选择 singleton 或 joint readout
  probability。
- 通过统一 `run_spectroscopy` 接口运行一次单比特或双比特并行频谱，并返回本次扫描的
  频率、P0、P1、leakage 和峰值分析。
- 通过 `run_rabi` 执行单比特首瓣 `X2P + X2P` 幅度扫描，并返回幅度、P0、P1、
  leakage、拟合分析和候选更新；`cancel_rabi` 提供跨进程协作取消。
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
.venv\Scripts\python -m pip install --require-hashes -r requirements-test-py312-windows-lock.txt
.venv\Scripts\python -m pip install -e . --no-deps
```

验证安装：

```powershell
.venv\Scripts\python -c "import sqvm; print(sqvm.__version__)"
.venv\Scripts\python -m pytest -m contract -q
.venv\Scripts\python -m pytest -m integration -q
.venv\Scripts\python -m pytest -m physics_slow -q
.venv\Scripts\python -m pytest -m "evidence and release and not legacy_environment" -q
```

`.[dev]` remains a historical minimal entry and does not install the full test or
Notebook-kernel environment. Windows uses `requirements-test-py312-windows-lock.txt`;
Linux uses `requirements-test-py312-linux-lock.txt`. Regenerate both only with
`py -3.12 tools/generate_test_locks.py`, then run `py -3.12 tools/verify_authority_drift.py`.
Historical Stage 4 v1 Notebook execution is an `evidence`, `notebook`,
`windows`, `legacy_environment` test and is intentionally excluded from hosted
runner commands: it fails closed unless the approved interpreter and kernelspec exist.

## CI 与 successor 基线

仓库提供四套 GitHub Actions 工作流：PR 资格、夜间物理、hosted evidence 和 main 发布汇总。
Windows/CPython 3.12.10 是资格平台，Linux job 是跨平台补充。`dev` PR 独立运行一次资格矩阵；
`main` PR 由 Release Gate 复用同一矩阵，不再额外触发一套重复资格任务。物理 Windows/Linux job
在独立 hosted runner 上并行，产物名和 pip cache 都按平台隔离。工作流使用 Node 24 或更新运行时的
官方 Actions major，required check 名称保持不变。

旧 Stage 2.1 至 4.0 evidence 的原始字节已经不可恢复。当前 successor fixture 只记录已知旧哈希、
不可恢复状态和 provisional source closure，不宣称重新完成物理执行。验证开发基线：

```powershell
py -3.12 tests/tools/verify_successor_rebaseline_fixture.py `
  tests/fixtures/successor_rebaseline_authority_v1 `
  --repository-root .
py -3.12 tools/verify_successor_development_baseline_approval.py `
  --repository-root .
py -3.12 -m pytest -q `
  tests/test_successor_rebaseline_fixture.py `
  tests/test_successor_rebaseline.py
```

生产 v2 只有在 Stage 2.1 至 4.0 新证据、独立 approval 和版本化 selector 全部完成后才能激活；
现有 v1 production validator 与冻结哈希保持不变。

## 用户 Notebook

用户调用入口统一放在 `user/`：

| Notebook | 用途 |
| --- | --- |
| [`01_qubit_spectroscopy.ipynb`](user/01_qubit_spectroscopy.ipynb) | 单比特或双比特并行频谱校准 |
| [`02_x2p_rabi_calibration.ipynb`](user/02_x2p_rabi_calibration.ipynb) | 单比特 `X2P + X2P` Rabi 幅度校准 |

Notebook 会自动定位项目根目录并从 `sqvm.calibration` 导入公共接口。真实 QuTiP 扫描
可能耗时较长；当前 Notebook 的 `RUN_EXPERIMENT=True`，执行扫描单元格前应先检查扫描轴、
波形参数和 `OPERATION_ID`，不准备运行时将其改为 `False`。

推荐从项目根目录启动 Jupyter：

```powershell
.\user\setup_environment.cmd
.venv\Scripts\python -m pip install jupyterlab
.venv\Scripts\python -m jupyter lab user
```

如果没有 `.venv`，环境脚本会使用系统的 `py -3` 或 `python`。安装完成后可以在项目
根目录或 `user/` 目录直接导入 `run_spectroscopy` 或 `run_rabi`。

## Python 校准 API

Web 不负责启动实验。校准实验通过 Python API 运行，结果写入
`output/experiments/`。原子发布后会写入幂等索引提示，Web 后台协调器同时通过周期浅对账恢复漏登记结果。

用户只需要给出扫描对象、频率范围和公共频率步进。一个对象自动执行单比特
扫谱，两个对象自动并行：

```python
from uuid import uuid4

from sqvm.calibration import (
    apply_calibration_candidates_to_current_configuration,
    cancel_spectroscopy,
    run_spectroscopy,
)

operation_id = str(uuid4())
result = run_spectroscopy(
    {
        "Q1": (5.00, 5.40),
        "Q2": (5.10, 5.50),
    },
    frequency_step_GHz=0.20,
    operation_id=operation_id,
)

print(result.run_id)
print(result.root)
print(list(result.data["Q1"]["frequency_GHz"]))
print(list(result.data["Q1"]["P1"]))
print(result.analysis.peaks["Q1"])
print(result.candidates["Q1"])

# 检查候选和门限后，显式写入当前配置。
update = apply_calibration_candidates_to_current_configuration(
    result,
    confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {result.run_id}",
)
```

`apply_calibration_candidates_to_current_configuration` 是所有校准实验共用的配置更新入口。
实验只负责生成不同的候选组；每个候选以 `candidate_id` 标识，并包含一个或多个
`changes`。`calibration_subjects` 表示实际被校准对象；每项 change 通过
`configuration_resource` 表示配置记录 owner，并绑定 `parameter_path`、`current_value`、
`proposed_value` 和 `unit`。因此可以单独校准 Q1 的 CZ 动力学相位，同时把结果正确写入
C 所属的 `c_cz` Setting。被选择的候选在同一事务中原子写入，任何参数已变化都会以
stale conflict 拒绝。
Web 和 Python API 均按 `candidate_ids` 选择候选，通用确认短语为
`APPLY CALIBRATION CANDIDATES <run_id>`。

`run_spectroscopy` 的参数如下：

| 参数 | 必填 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `frequency_ranges_GHz` | 是 | 无 | `{QAgent: (起始频率, 终止频率)}`；包含首尾点，支持 1 或 2 个对象 |
| `frequency_step_GHz` | 否 | `0.01` | 公共频率步进；范围必须能被步进整除 |
| `pulse_length_samples` | 否 | `32` | 扫谱脉冲长度 |
| `pulse_amplitude_GHz` | 否 | `0.02` | 扫谱脉冲幅度 |
| `pulse_r_sigma_samples` | 否 | `8.0` | 扫谱脉冲的 `r_sigma` |
| `device_id` | 否 | `demo_2q1c2r` | 使用 Active 配置的设备 |
| `timeout_s` | 否 | `600.0` | 每个隔离 QuTiP worker 的 watchdog，不是整次实验总时长 |
| `batch_deadline_s` | 否 | `3600.0` | 一次有效批次 attempt 的总时限；已提交点在下次 attempt 中复用 |
| `operation_id` | 否 | 自动 UUID4 | 幂等运行 ID；重试时传回原值即可续跑或零执行重开 |
| `cancellation_token` | 否 | `None` | 进程内协作取消；在两个 QCIS 点之间生效 |
| `output_root` | 否 | `output/experiments` | 实验结果集合目录 |
| `progress_callback` | 否 | `None` | 接收运行进度事件的回调 |

每次调用只执行参数指定的这一轮扫描，不会自动追加另一组范围或步进。本次扫描的有效峰
会形成候选校准值；候选通过峰质量、最大 leakage 和最大 norm error 门限后，可以由用户
显式确认并写入当前配置。需要换范围或步进时，再次调用同一个接口。

调用前保存 `operation_id`。进程中断后，以完全相同的参数和该 ID 再次调用，只执行尚未提交的
QCIS 点；结果已经发布时则只验证并重开，不重复运行 QuTiP。同一个 ID 如果绑定了不同范围、
波形、Active 配置或执行时限会被拒绝。跨进程取消可调用
`cancel_spectroscopy(operation_id, ...)`，取消为协作式，不会在单个 QuTiP worker 中途强杀线程。

Rabi/X2P 扫描使用 Runtime 0.3 的同一批执行、恢复、幂等和证据发布合同。首版固定为一个目标
比特、从零开始的幅度轴，以及每点 `SET + X2P + X2P`；扫描范围和步进必须显式给出：

```python
from uuid import uuid4

from sqvm.calibration import cancel_rabi, run_rabi

operation_id = str(uuid4())
result = run_rabi(
    target="Q1",
    amplitude_range_GHz=(0.0, 0.03),
    amplitude_step_GHz=0.002,
    operation_id=operation_id,
)

print(list(result.data["Q1"]["amplitude_GHz"]))
print(list(result.data["Q1"]["P1"]))
print(result.candidates.get("Q1"))

# 需要在下一个扫描点前协作取消时：
# cancel_rabi(operation_id)
```

Rabi 实验只生成 active XY2 setting 的幅度候选，不会自动修改当前配置。相同参数和
`operation_id` 可恢复未提交点或零执行重开；`cancel_rabi` 不会在单个 QuTiP worker 中途强杀进程。

本地校准扫描使用已批准的 Stage 4.1 控制链，并把验证后的有效 I/Q 数组交给独立的
QuTiP worker。默认从 `(7,7,7)` 电荷基重建 2Q1C 哈密顿量，再投影到
`5×3×5=75` 维低能空间；每点同时以 `(8,8,8)`、`6×4×6=144` 检查频率和驱动矩阵元收敛。
这两组 charge cutoff 与保留能级数保存在 Web 当前配置的“控制链 > 仿真截断”中，可以编辑；
总维度由 Q1、C、Q2 的保留能级数乘积计算，不单独保存。
模型 authority、solver、控制工件和结果数组分别做哈希绑定。每点证据会记录
`numerical_replay=deferred`；这类结果只用于本模拟器配置，不代表硬件测量或未投影的
3375 维全空间演化 authority。当前单点端到端实测约 19 秒，整次运行耗时由本次请求的
数据点数量决定。

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
- 可以编辑校准模型的基础/收敛截断；默认基础保留空间为 `5×3×5=75` 维。
- 可以保存配置快照、长期保留快照或把快照恢复到当前配置。
- 可以查看扫描曲线、峰值分析、候选校准值、门限和底层证据。
- 实验图使用统一 `plot_spec`，可筛选 Q1/Q2/C 等对象与 P0/P1/leakage 等指标，并支持选点坐标、折线、散点和 heatmap。
- 实验详情可按扫描点联动查看 QCIS 指令、编译逻辑波形、电子学链后的真实 AWG `delivered_voltage`/DAC 码，以及实际送入 QuTiP 的有效 I/Q 和磁通控制。
- 实验列表使用后端分页、搜索和状态筛选；大型一维曲线按需加载降采样数据，点击后回查精确原始点。
- 实验列表、总览、详情和绘图普通请求读取可重建的 SQLite 读模型，不遍历每个实验的 `execution/` 证据树。
- 不启动 QuTiP 实验，不在浏览器中重新拟合数据。
- 不开放电容、电感等器件物理 authority 的修改。
- 扫谱生成合格候选后，可以显式确认并更新当前配置。
- 当前配置只在点击“保存并生效”后写入；保存成功会自动生成不可变运行版本并切换 Active，下一次实验直接使用新值。
- Notebook 中显式确认候选更新、以及 Web 中恢复快照，同样会在成功后立即生效，不再要求手工发布或激活。

通道波形不会并入普通实验详情响应，而是按需读取受清单和 SHA-256 绑定的 Stage 4.1 证据：

```text
GET /api/v1/experiments/<run_id>/waveforms
GET /api/v1/experiments/<run_id>/waveforms/<circuit_id>?view=awg|effective|logical&max_points=2000
```

## 实验存储 v1

本轮已交付本地实验数据存储的 v1 基础能力。它用于管理已产生的实验载体，不执行实验，也不会通过 Web 删除数据到不可恢复状态。完整的目标合同见[实验存储生命周期设计](docs/designs/07_1_8_experiment_storage_lifecycle_design.md)，其中有些阶段尚未实现，见本节末尾的边界说明。

### 启动与目录

默认启动时，实验热数据、存储控制面和归档分别使用以下目录：

```text
output/
  experiments/                       # experiment hot root
  experiment-storage/                 # storage root
    lifecycle/
    trash/
    tombstones/
    archives/                         # default archive root
    catalog.sqlite                    # 可重建的实验载体目录
    web-read-model.sqlite             # 可重建的 Web 实验读模型
    index-inbox/                      # 发布后的幂等待投影提示
    index-quarantine/                 # 损坏提示隔离区
```

可使用受信的启动参数覆盖这些根目录。`--experiment-archive-root` 必须是本机绝对路径；它只改变归档载体的位置，`catalog.sqlite`、生命周期记录、回收站和 tombstone 仍位于 `--experiment-storage-root` 下。

```powershell
.\start_calibration_web.cmd --experiment-hot-root output\experiments --experiment-storage-root output\experiment-storage
.\start_calibration_web.cmd --experiment-archive-root D:\SQVM-archives
python scripts/start_calibration_web.py --no-browser --experiment-archive-root D:\SQVM-archives
```

首次启动会以 no-follow 方式创建缺失的热数据和存储 authority 目录，并启动 Web 读模型协调器；storage catalog 可由后台协调或首次存储请求构建。任何根目录、数据库 carrier 或祖先目录中的 symlink/junction/reparse 载体都会被拒绝，数据库 hardlink 也会被拒绝。读模型损坏时只隔离并重建派生数据库，不修改实验载体。

### Web 操作

校准控制台提供“实验存储”和“回收站”一级视图，用于查看容量摘要、实验状态、引用/保留阻断原因和可用操作。当前 Web v1 允许：

- 设置或取消 manual keep；
- 将 hot 实验归档为确定性 `.sqrun`，以及恢复为 hot；
- 将 hot 或 archived 实验移入可恢复的回收站，以及从回收站恢复；
- 透明、受限地读取归档中的实验详情、图表数据和资产，不会解压到磁盘。

操作请求会携带 actor、理由、预期 catalog revision 和 workflow hash；服务端会重新检查当前状态，前端展示的 `allowed_actions` 仅用于提示，不是授权依据。连续 GET 不会重建 catalog 或推进 revision。Web v1 没有永久 purge 路由，也没有 cleanup preview/apply，更不会运行实验。

### Python API 边界

存储服务面向受信的本地 Python 调用方。查询和重建使用 catalog 模块；状态变更必须经过 operations 模块，而不是由调用方移动或删除载体：

```python
from sqvm.storage.catalog import CatalogRoots, query_catalog, rebuild_catalog, storage_summary
from sqvm.storage.operations import ExperimentStorageOperations, StorageMutationRequest
```

`CatalogRoots` 和 `ExperimentStorageOperations` 接收的是由宿主进程配置的受信根目录。浏览器请求不提供目标路径、archive root 或删除权限；操作层会执行新鲜的 revision、workflow identity、引用、保留和载体完整性检查。catalog 是可重建的派生缓存，不是删除授权的唯一来源。

`.sqrun` 使用确定性 ZIP_STORED/ZIP64 格式，并通过 v0.3 evidence verifier 验证归档前后的证据闭包。归档读取是有界、流式的，拒绝路径逃逸、链接/reparse、hardlink 和不受支持的 ZIP 结构。回收站是可恢复的，不等同于永久清除。

### 尚未交付的阶段

以下内容仍属于后续阶段，不能视为当前 v1 已具备：自动保留策略执行、cleanup plan 的 preview/apply、永久 purge、配额与准入/预留执行，以及旧版实验载体迁移。

## 配置与实验数据

本机配置数据位于：

```text
output/platform-configurations/
├─ transactions/
│  ├─ heads/      每个设备唯一的事务提交点
│  ├─ bundles/    不可变的同代 current/Active/snapshot/pin/audit 状态
│  └─ locks/      Web 与 Notebook/Python 共用的跨进程设备锁
├─ current/       Web 当前配置的兼容投影
├─ snapshots/     不可修改快照的兼容投影
├─ active/        Active 指针的兼容投影
├─ drafts/        旧版 Draft 兼容数据
├─ pins/          长期保留标记的兼容投影
└─ audit/         配置审计的兼容投影
```

正式 Store、实验 resolver、引用图、Web 和 Notebook/Python API 都先验证 transaction Head、Manifest、
完整 Bundle 与 parent chain。平面目录只用于旧脚本兼容，可能在崩溃恢复期间短暂滞后，不应被新代码
直接读取。事务协议与稳定错误码见
[`docs/designs/07_1_11_configuration_transaction_v0_1.md`](docs/designs/07_1_11_configuration_transaction_v0_1.md)。

当前 Web 主流程使用“当前配置 + 快照”模型；`run_spectroscopy` 要求设备恰好存在一个
合法的 Active 运行版本。浏览器内尚未保存的修改不会影响实验；点击“保存并生效”后，
服务端自动生成不可变运行版本并切换 Active，使下一次实验使用新配置，同时保留内容哈希用于复现。

扫谱 Notebook 负责设置参数、运行一次实验、查看数据和候选；只有用户打开更新开关后才
会显式更新当前配置，更新成功后立即供下一次实验使用。

实验工件位于：

```text
output/experiments/<experiment_run>/
├─ workflow.json
├─ receipt.json
├─ dataset.json
└─ execution/       每个 QCIS circuit 的模型与执行证据
```

本次请求、峰值分析、候选校准值和门限记录在 `workflow.json` 中，所有扫描点记录在唯一
的 `dataset.json`。旧版多阶段校准工件仍可读取，但不再由 `run_spectroscopy` 生成。
验证器以工件内容和 SHA-256 绑定为准，Web 不是数据 authority。

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
  tests/test_rabi_public_api_acceptance.py `
  tests/test_rabi_calibration.py `
  tests/test_rabi_runtime_adapter.py `
  tests/test_rabi_storage_lifecycle.py `
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
- 当前 75 维投影电荷基校准模型只接收 idle-flux XY 线路；DTN、CZ 和 FSIM 仍需增加磁通响应模型。
- 当前初态固定为 `lab_ground`，observable 主要是 dressed computational population。
- 尚未实现真实 shot、IQ、assignment matrix 和读出噪声模型。
- 比特频谱与 Rabi/X2P 幅度校准已接入完整工作流；Ramsey、DRAG、Coupler 和 CZ 校准实验
  仍未实现。
- Runtime 0.3 批执行底座已用于频谱和 Rabi 的预编译、逐点提交、恢复、取消与幂等重开。
- 校准扫描仍是后台 Python 工作流，不作为 Web 请求内的同步操作。
- Web 已支持配置、实验查看、Rabi/频谱绘图、存储 v1 生命周期操作和显式候选决策，
  但不负责启动实验；自动保留、cleanup preview/apply、永久 purge 和配额执行仍未交付。
- `runtime`、`runtime_v02`、Runtime 0.3 批执行以及部分 Stage 4/5 双版本仍待后续架构收敛。

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
9. [`docs/designs/07_1_7_unified_web_plotting.md`](docs/designs/07_1_7_unified_web_plotting.md)
10. [`docs/designs/07_1_13_rabi_x2p_amplitude_calibration.md`](docs/designs/07_1_13_rabi_x2p_amplitude_calibration.md)
11. [`docs/logs/DEVELOPMENT_LOG.md`](docs/logs/DEVELOPMENT_LOG.md)
