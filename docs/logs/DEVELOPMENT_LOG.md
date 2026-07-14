# 开发日志

## 2026-07-08：项目重启与规划骨架

阶段：

```text
规划
```

目标：

```text
围绕一台明确的 2q1c2r 虚拟超导量子计算机重新确定项目方向，并建立支持分阶段可靠开发的文档流程。
```

完成的修改：

```text
创建项目 README。
创建项目目标文档。
创建开发流程文档。
创建开发路线图。
创建阶段 1 器件模型计划。
创建开发日志。
```

设计决定：

```text
第一版实现聚焦一台 2q1c2r 虚拟机器，而不是通用平台。

开发按阶段推进。每个阶段都需要计划、实现、测试、输出产物和日志，再进入下一阶段。

Web 界面应作为人工操作虚拟实验的 lab，而不是单独的平台抽象。
```

运行的测试：

```text
无。本条日志只创建规划文档。
```

下一步：

```text
实现阶段 1：器件配置与参数模型。
```

## 2026-07-08：接口与正确性检查点要求

阶段：

```text
规划
```

目标：

```text
让每个开发阶段都可检查，并在后续阶段依赖它之前提供清晰公开接口和明确正确性证据。
```

完成的修改：

```text
在开发流程中加入接口规则和正确性规则。
在阶段模板中加入公开接口和正确性检查章节。
在阶段 1 计划中加入具体的器件验证接口。
```

设计决定：

```text
每个阶段都必须暴露小而稳定的接口。
每个阶段都必须提供自动测试、确定性产物和人可读 verification report。
每个阶段进入下一阶段前，用户都应有明确检查命令或检查产物。
```

运行的测试：

```text
无。本条日志只更新规划文档。
```

下一步：

```text
围绕 verify-device 检查点实现阶段 1。
```

## 2026-07-08：规划文档中文化

阶段：

```text
规划
```

目标：

```text
将规划相关文档统一改为中文，保留接口名、命令和路径等实现标识，避免中英文混杂影响后续阅读。
```

完成的修改：

```text
将 README 和 docs 下的规划、流程、路线图、阶段计划、模板、日志说明改为中文。
```

运行的测试：

```text
已用 UTF-8 读取关键规划文档。
已搜索常见乱码字符，未发现乱码。
已搜索旧英文模板标题，未发现残留。
```

下一步：

```text
进入阶段 1 实现：器件配置与参数模型。
```

## 2026-07-08：加入 PDF 参考资料规则

阶段：

```text
规划
```

目标：

```text
将参考文件夹中的两篇博士论文纳入项目设计依据，并规定后续阶段如何引用参考资料。
```

完成的修改：

```text
创建 docs/references/README.md。
在开发流程中加入参考资料规则。
在阶段模板中加入参考来源章节。
在阶段 1 计划中说明两篇 PDF 和旧 V1 项目的参考作用。
```

设计决定：

```text
PDF 作为物理模型、实验流程、控制与标定背景参考。
每个阶段必须明确采用了参考资料中的哪些原则，也要说明暂不采用的内容。
不确定的论文理解不能直接写成已验证实现要求。
```

运行的测试：

```text
使用 pypdf 读取两篇 PDF 元数据和页数。
中文全文关键词抽取不稳定，因此现阶段只记录参考用途，不做细节推断。
```

下一步：

```text
阶段 1 详细实现设计中继续细化 device.yaml 字段和物理参数来源。
```

## 2026-07-08：明确阶段 1 主参考资料

阶段：

```text
规划
```

目标：

```text
明确阶段 1 主要参考李少炜博士论文和旧 V1 项目。
```

完成的修改：

```text
更新阶段 1 计划中的参考来源说明。
更新参考资料目录中的阶段使用说明。
```

设计决定：

```text
阶段 1 主要参考李少炜博士论文中关于超导量子比特、高精度控制、双比特门、耦合器和实验校准背景的内容。
阶段 1 主要工程参考旧 V1 项目的 device.yaml、NodeGraph、电容矩阵、结参数解析和参数管理思想。
范道金博士论文在阶段 1 只作为次要背景，后续控制链路、实验运行框架、标定流程和 Web lab 阶段再重点使用。
```

运行的测试：

```text
未运行代码测试。本条日志只更新规划文档。
```

下一步：

```text
基于阶段 1 主参考资料编写详细实现设计文档。
```

## 2026-07-08：阶段 1 详细设计文档

阶段：

```text
阶段 1 设计
```

目标：

```text
给出器件配置与参数模型阶段的具体设计，作为后续实现依据。
```

完成的修改：

```text
新增 docs/designs/01_device_model_design.md。
在阶段 1 计划中加入详细设计文档链接。
```

设计决定：

```text
阶段 1 只支持 topology: 2q1c2r。
第一版配置文件要求字段名带单位。
默认节点顺序为 q1_p, q1_m, c, q2_p, q2_m, r1, r2。
电容矩阵只做节点电容矩阵，不在阶段 1 做坐标变换或逆矩阵。
Rn 到 EJ 的估算沿用 V1 的 Ambegaokar-Baratoff 工程公式。
阶段 2 只能依赖阶段 1 公开接口输出，不直接解析 YAML 内部结构。
```

运行的测试：

```text
未运行代码测试。本条日志只新增设计文档。
```

下一步：

```text
开始实现阶段 1：创建配置文件、sqvm.device 模块、verify-device 命令和测试。
```

## 2026-07-08：阶段 1 与李少炜论文对照

阶段：

```text
阶段 1 设计审查
```

目标：

```text
检查阶段 1 具体设计与李少炜博士论文中关于超导量子比特、高精度控制、双比特门、耦合器和实验校准背景是否存在明显出入。
```

完成的修改：

```text
在 docs/designs/01_device_model_design.md 中加入“与李少炜论文的阶段 1 对照”小节。
```

观察结果：

```text
阶段 1 中 q1/q2/c、电容网络、qubit-coupler 电容、qubit-qubit 直接电容、coupler flux、truncation、控制通道和读出通道的保留，与论文中 LC 网络、多 Transmon 仿真、耦合器调节和校准背景方向一致。
```

需要后续确认：

```text
论文具体实验器件的接地/浮置约定是否与 V1 的 floating qubit + grounded coupler 完全一致。
Rn 到 EJ 的工艺换算是否需要替换为更贴近实验记录的参数来源。
阶段 2 中从节点电容矩阵到 EC 矩阵和哈密顿量的坐标选择。
```

运行的测试：

```text
未运行代码测试。使用 pypdf 抽取论文目录和相关章节文本，用于设计对照。
```

下一步：

```text
如用户认可该对照结论，开始阶段 1 实现。
```

## 2026-07-08：阶段 1 输出改为 JSON + Notebook

阶段：

```text
阶段 1 设计
```

目标：

```text
简化阶段 1 输出产物，并让人工检查入口更适合展示表格、图和可执行检查过程。
```

完成的修改：

```text
将阶段 1 默认输出从多个 summary/csv/md 文件改为 device_artifacts.json 和 verification.ipynb。
更新阶段 1 详细设计、阶段计划、开发流程和阶段模板。
```

设计决定：

```text
device_artifacts.json 是唯一机器可读数据源。
verification.ipynb 只读取 device_artifacts.json，不重复计算核心结果。
后续阶段依赖 Python API 或 device_artifacts.json，不依赖 notebook。
CSV 和 Markdown report 不作为阶段 1 默认产物。
```

运行的测试：

```text
未运行代码测试。本条日志只更新设计文档。
```

下一步：

```text
继续确认剩余阶段 1 设计问题。
```

## 2026-07-08：阶段 1 设计文档复查

阶段：

```text
阶段 1 设计审查
```

目标：

```text
总体复查阶段 1 计划和详细设计，清理已经确认但仍残留为开放问题的内容。
```

完成的修改：

```text
将阶段 1 计划中的开放问题改为已确认关键设计。
移除显式 EJ 输入作为阶段 1 主路径的残留描述。
将输出描述统一为 device_artifacts.json 和已执行的 verification.ipynb。
同步测试清单，移除 explicit_ej_sum 测试，改为 Rn -> EJ 主路径。
```

设计决定：

```text
阶段 1 设计已经足够进入实现。
剩余问题主要属于阶段 2 哈密顿量构建或后续读出模型，不阻塞阶段 1。
```

运行的测试：

```text
未运行代码测试。本条日志只清理和复查设计文档。
```

下一步：

```text
提交设计文档更新，然后开始阶段 1 实现。
```

## 2026-07-08：verification notebook 生成规则

阶段：

```text
阶段 1 设计
```

目标：

```text
确认 verification.ipynb 的生成方式。
```

设计决定：

```text
阶段 1 只生成已执行的 verification.ipynb。
不额外生成未执行 notebook。
用户打开 verification.ipynb 时应能直接看到表格、图和检查结果。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计决策。
```

下一步：

```text
继续确认 device_artifacts.json 的版本字段和 schema 规则。
```

## 2026-07-08：device_artifacts 版本字段

阶段：

```text
阶段 1 设计
```

目标：

```text
确认 device_artifacts.json 的版本标识，保证后续阶段读取格式可追踪。
```

设计决定：

```text
device_artifacts.json 必须包含 schema_version、artifact_type 和 artifact_version。
schema_version 表示输入 device.yaml 的格式版本。
artifact_type 阶段 1 固定为 stage_01_device_model。
artifact_version 阶段 1 固定为 0.1。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计决策。
```

下一步：

```text
继续确认 priors 字段范围。
```

## 2026-07-08：priors 字段范围

阶段：

```text
阶段 1 设计
```

目标：

```text
确认 device.yaml 中 priors 的第一版字段范围和使用规则。
```

设计决定：

```text
priors 第一版只允许少量白名单字段。
允许 estimated_f01_GHz、estimated_anharmonicity_GHz、estimated_idle_frequency_GHz、estimated_flux_sweet_spot_phi0、r1_frequency_GHz、r2_frequency_GHz 和 note。
priors 只作为参考信息，不参与阶段 1 电容矩阵，不参与 Rn -> EJ，不覆盖物理参数。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计决策。
```

下一步：

```text
继续确认 sanity check warning 阈值。
```

## 2026-07-08：sanity check warning 阈值

阶段：

```text
阶段 1 设计
```

目标：

```text
确认阶段 1 的宽松 warning 阈值。
```

设计决定：

```text
warning 只提示，不阻止通过。
q1/q2/c capacitance_fF 超出 20-200 fF 给 warning。
r1/r2 frequency_GHz 超出 4-10 GHz 给 warning。
r1/r2 kappa_MHz 超出 0.01-50 MHz 给 warning。
rn_ohm 超出 1000-100000 ohm 给 warning。
flux_bias_phi0 超出 -1 到 1 给 warning。
q1/q2/c truncation 小于 3 给 warning。
缺少 XY 或 readout channel 给 warning。
这些范围只是宽松 sanity check，不作为论文参数的严格约束。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计决策。
```

下一步：

```text
确认是否初始化 git，然后进入阶段 1 实现。
```

## 2026-07-08：初始化 Git 仓库

阶段：

```text
项目基础设施
```

目标：

```text
让后续每一步设计、实现和验证都可以通过版本控制回溯。
```

完成的修改：

```text
在 D:\codex\SQC_simulation 初始化 git 仓库。
新增 .gitignore，排除 Python 缓存、虚拟环境、Jupyter checkpoint、output 和 tmp 等生成物。
```

运行的测试：

```text
git init 成功。
```

下一步：

```text
检查 git status，然后开始阶段 1 实现。
```

## 2026-07-08：关联远程仓库

阶段：

```text
项目基础设施
```

目标：

```text
将本地仓库关联到远程 GitHub 仓库。
```

完成的修改：

```text
添加 git remote origin。
远程地址为 https://github.com/daojinfan/SQC_simulation.git。
```

运行的测试：

```text
git remote -v 已显示 origin fetch/push 地址。
当前本地分支为 master。
```

下一步：

```text
确认是否将默认分支改为 main，以及是否进行首次 commit 和 push。
```

## 2026-07-08：阶段 1 器件模型实现

阶段：

```text
阶段 1 实现
```

目标：

```text
按阶段 1 详细设计实现 2q1c2r 器件配置、参数解析、基础正确性检查和可检查产物生成。
```

完成的修改：

```text
新增 configs/devices/2q1c2r.yaml 示例配置。
新增 sqvm.device 模块，包含 device.yaml 解析、2q1c 节点电容矩阵构建、Rn -> Ic -> EJ 参数换算、宽松 sanity validation、device_artifacts.json 写出和 verification.ipynb 生成。
新增 python -m sqvm verify-device CONFIG --output OUTPUT 命令。
新增 pytest 测试，覆盖配置解析、结构校验、电容矩阵、结参数换算和 CLI/产物生成。
```

设计决定：

```text
阶段 1 计算默认只包含 2q1c 节点 q1_p, q1_m, c, q2_p, q2_m。
r1/r2 在阶段 1 作为读出元数据和通道信息保留，不进入默认电容矩阵。
verification.ipynb 只读取 device_artifacts.json，不重新计算核心结果。
warning 不阻塞通过；只有 validation errors 或核心 checks 失败才导致 verify-device 失败。
```

运行的测试：

```text
C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
结果：20 passed。

C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m sqvm verify-device configs/devices/2q1c2r.yaml --output output/stage_01_device_model
结果：ok=true，生成 output/stage_01_device_model/device_artifacts.json 和 output/stage_01_device_model/verification.ipynb。
```

下一步：

```text
人工检查阶段 1 产物；若确认通过，再进入阶段 2：从电容/结参数构建 2q1c 哈密顿量接口。
```

## 2026-07-08：修正 verification notebook 电容矩阵 heatmap

阶段：

```text
阶段 1 可视化修正
```

目标：

```text
让电容矩阵 heatmap 更直观地区分节点电容矩阵符号和物理耦合电容大小，并修正 notebook 中文标题乱码。
```

完成的修改：

```text
将原来的字符 heatmap 改为嵌入式 matplotlib PNG 图。
heatmap 左图显示带符号节点电容矩阵 C_ij，右图显示非对角耦合电容 -C_ij。
每个格子直接标注 fF 数值，避免小耦合项被对角元视觉上淹没。
修正 verification.ipynb 的中文标题和章节名编码。
新增测试，要求 verification.ipynb 包含正常中文标题和 image/png 输出。
```

运行的测试：

```text
C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
结果：21 passed。

C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m sqvm verify-device configs/devices/2q1c2r.yaml --output output/stage_01_device_model
结果：ok=true，重新生成 verification.ipynb。
```

下一步：

```text
人工检查新的 verification.ipynb heatmap；若显示符合预期，再确认阶段 1 产物。
```

## 2026-07-08：移除 device.yaml 中未使用的 truncation 字段

阶段：

```text
阶段 1 设计清理
```

目标：

```text
避免 device.yaml 中出现当前阶段未使用、且容易与阶段 2 数值基底截断混淆的字段。
```

设计决定：

```text
device.yaml 只描述器件物理结构和物理参数。
数值计算用的 charge basis 截断属于阶段 2 Hamiltonian 构建配置，不写入 device.yaml。
阶段 1 不再解析、验证或输出 truncation。
```

完成的修改：

```text
从 configs/devices/2q1c2r.yaml 删除 truncation。
从 ComponentSpec、配置解析、validation、device_artifacts 输出和测试中删除 truncation。
更新阶段 1 详细设计文档，移除 truncation 相关配置、单位、验证规则和测试条目。
重新生成 output/stage_01_device_model/device_artifacts.json 和 verification.ipynb。
```

运行的测试：

```text
C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
结果：20 passed。

C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m sqvm verify-device configs/devices/2q1c2r.yaml --output output/stage_01_device_model
结果：ok=true。
```

下一步：

```text
继续讨论阶段 2 详细设计；阶段 2 只在 Hamiltonian 构建配置中引入 charge_cutoff。
```

## 2026-07-08：阶段 2 哈密顿量设计文档

阶段：

```text
阶段 2 设计
```

目标：

```text
把 2q1c charge-basis 静态哈密顿量构建方案写成正式阶段计划和详细设计文档。
```

设计决定：

```text
阶段 2 使用 charge-basis multi-transmon Hamiltonian。
模式顺序固定为 q1, c, q2。
从节点坐标到模式坐标使用固定线性变换 theta_node = A * theta_mode。
C_mode = A^T C_node A。
E_C,ij = e^2 / (2h) * C_mode^-1，单位为 GHz。
SQUID 有效 EJ 使用非对称 SQUID 公式。
charge_cutoff 只写在 Hamiltonian 构建配置中，不写入 device.yaml。
Hamiltonian 默认用 sparse 矩阵构建，并用小 cutoff dense 对照验证。
默认求最低 12 个本征值。
默认不保存完整 Hamiltonian 矩阵，只保存可重建摘要和验证结果。
后续阶段主要通过 VSCode runner 脚本运行，CLI 只作为备用自动化入口。
```

完成的修改：

```text
新增 docs/stages/02_hamiltonian_plan.md。
新增 docs/designs/02_hamiltonian_design.md。
文档中明确阶段 2 的输入、输出、公开接口、VSCode 工作流、verification notebook 内容、测试设计和验收标准。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计文档新增。
已用 rg 检查阶段 2 文档中的关键术语和不再使用的截断字段。
```

下一步：

```text
复查阶段 2 设计文档；确认无问题后开始实现 configs/hamiltonians、scripts/run_stage_02_hamiltonian.py 和 sqvm.hamiltonian 模块。
```

## 2026-07-08：阶段 2 三个关键设计点修正

阶段：

```text
阶段 2 设计复查
```

目标：

```text
在实现前先收敛 hamiltonian artifact 数据完整性、C_mode 正定性规则和数值依赖声明。
```

设计决定：

```text
hamiltonian_artifacts.json 必须包含 node_capacitance_matrix_fF，保证 verification.ipynb 只读取 hamiltonian_artifacts.json 也能展示 C_node heatmap。
C_mode 必须对称正定；非正定或不可逆都算 error，不再接受“至少可逆”作为通过条件。
阶段 2 明确依赖 numpy 和 scipy；sparse matrix、Kronecker product 和 eigsh 来自 scipy。
```

完成的修改：

```text
更新 docs/stages/02_hamiltonian_plan.md。
更新 docs/designs/02_hamiltonian_design.md。
新增 node_capacitance_matrix_fF 输出字段说明。
将 C_mode 检查统一为 positive definite。
补充 numpy/scipy 依赖说明。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计文档修正。
已用 rg 检查没有残留“至少可逆”或 mode_capacitance_invertible 的旧说法。
```

下一步：

```text
继续复查阶段 2 设计中剩余细节；若无新问题，再进入阶段 2 实现。
```

## 2026-07-09：阶段 2 哈密顿量实现

阶段：

```text
阶段 2 实现
```

目标：

```text
实现从阶段 1 device_artifacts.json 到 2q1c charge-basis 静态 Hamiltonian 的完整可验证链路。
```

完成的修改：

```text
新增 configs/hamiltonians/2q1c_charge_basis.yaml。
新增 scripts/run_stage_02_hamiltonian.py，作为 VSCode 主运行入口。
新增 sqvm.hamiltonian 模块，包含配置解析、阶段 1 artifact 读取、固定 A 坐标变换、C_mode、E_C、EJ_eff、charge basis、sparse Hamiltonian、dense eigh 求解、artifact 写出、verification notebook 生成和 verify_hamiltonian 编排。
新增阶段 2 测试，覆盖配置、电容矩阵、E_C、有效 EJ、Hamiltonian 形状/厄米性、物理 sanity、CLI、runner 和 notebook。
更新 CLI，新增 python -m sqvm verify-hamiltonian。
更新 pyproject.toml，显式加入 numpy 和 scipy。
同步修正阶段 2 文档中 solver 默认值和 code fence。
```

设计决定：

```text
阶段 2 第一版默认 solver 为 scipy.linalg.eigh，eigsh 暂不实现。
Hamiltonian 内部构建仍使用 sparse matrix，求解时在当前 1331 维规模下转 dense。
结构性 checks 失败会导致 verify_hamiltonian 失败。
single_transmon_analytic_limit、charge_basis_convergence、coupling_magnitude_displayed 和 ec_diagonal_range 属于 warn-only physical sanity。
notebook 只读取 hamiltonian_artifacts.json。
```

运行的测试：

```text
C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
结果：38 passed。

C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts/run_stage_02_hamiltonian.py
结果：ok=true，生成 output/stage_02_hamiltonian/hamiltonian_artifacts.json 和 output/stage_02_hamiltonian/verification.ipynb。
```

观察到的结果：

```text
Hilbert dimension = 1331。
Hamiltonian summary: sparse shape = 1331 x 1331, nnz = 8590, hermiticity_error = 0。
mode coupling fF: q1-c = -0.100, q1-q2 = -0.075, c-q2 = -0.100。
最低 gap 前几项约为 0, 5.195, 5.334, 7.601 GHz。
single_transmon_analytic_limit 最大误差约 0.0133 GHz。
charge_cutoff N -> N+2 最大 gap 漂移约 0.0317 GHz。
```

下一步：

```text
人工检查 output/stage_02_hamiltonian/verification.ipynb。
若认可阶段 2 输出，再决定是否提交并进入阶段 3 静态能谱与 dressed-state 分析设计。
```

## 2026-07-10：阶段 3 静态能谱详细设计

阶段：

```text
阶段 3 设计
```

目标：

```text
设计从阶段 2 Hamiltonian 进入静态能谱、dressed-state 标记、频率、非谐性和 residual ZZ 类指标的分析流程。
```

完成的修改：

```text
新增 docs/stages/03_static_spectrum_plan.md。
新增 docs/designs/03_static_spectrum_design.md。
明确阶段 3 输入、输出、公开接口、VSCode runner、verification notebook、测试设计和验收标准。
```

设计决定：

```text
阶段 3 通过阶段 2 API 重建 Hamiltonian 并求 eigenvectors，不依赖阶段 2 artifact 保存完整矩阵。
阶段 3 不直接解析 device.yaml，也不直接读取 Hamiltonian YAML 内部字段；source_hamiltonian_config 只作为阶段 2 API 输入。
bare basis 使用各模式单模 Hamiltonian 的本征态，而不是 charge number basis。
dressed-state assignment 第一版使用 bare product state overlap，低 overlap 或冲突给 warning。
频率、anharmonicity 和 ZZ 指标都基于 dressed labels。
Notebook 只读取 static_spectrum_artifacts.json。
```

运行的测试：

```text
未运行代码测试。本条日志只记录阶段 3 设计文档新增。
```

下一步：

```text
复查阶段 3 设计；确认后开始实现 configs/spectra、scripts/run_stage_03_static_spectrum.py 和 sqvm.spectrum 模块。
```

## 2026-07-09：阶段 2 设计审查与修订

阶段：

```text
阶段 2 设计审查
```

目标：

```text
在实现前审查阶段 2 设计，找出物理与工程问题，并把结论落回 plan 和 design 文档。
```

完成的修改：

```text
新建 docs/decisions/2026-07-09-stage2-hamiltonian-review.md，记录两轮审查（H1–D4、N1–N10）与决策。
更新 docs/designs/02_hamiltonian_design.md：默认 solver 改稠密 eigh（eigsh 可选且须显式 which / sigma）；
  新增结构性 / 物理两类 check（node_order 硬契约、junction / flux 配对、解析极限、逐模收敛、基态 gap sanity、耦合量级展示）；
  物理类只 warn；记录忽略 SQUID 相位偏置 delta、offset_charge_ng=0、禁止用 asymmetry、物理常数共享、matplotlib Agg；
  artifact 增加 offset_charge_ng / reference_priors / mode_coupling_fF / solver_method；补充测试与已知限制。
更新 docs/stages/02_hamiltonian_plan.md：正确性检查、verification notebook、输出产物、测试、开放问题同步。
```

设计决定：

```text
H1：保持固定 A 差分模投影。物理复核确认投影合理——共模无 Josephson 恢复力、不承载 qubit-coupler 耦合，
    black-box 对该近对称器件会给相同量级耦合；小耦合（g ~ 0.4 MHz）源自示例器件近对称几何，非投影 bug。
    阶段 2 接受小耦合；真实几何留到阶段 3 前决策。（更正了此前“投影丢耦合、需上 black-box”的过头说法。）
H2/M2：默认 solver 改稠密 eigh；eigsh 仅大维度可选，且必须显式 which / sigma。
H3/H4：解析极限与收敛性等物理 sanity 全部只 warn；只有结构性检查和 error 才让 verify 失败。
M3：asymmetry 死字段延后清理；阶段 2 禁用。
N1：node_order == [q1_p, q1_m, c, q2_p, q2_m] 作为阶段 1 -> 阶段 2 硬契约。
```

运行的测试：

```text
未运行代码测试。本条日志只记录设计审查与文档修订。
关键数值（C_mode、E_C、f01）已在审查文档中手算验证：q1 / q2 / c 频率均与阶段 1 priors 吻合。
```

下一步：

```text
进入阶段 2 实现：configs/hamiltonians、scripts/run_stage_02_hamiltonian.py 和 src/sqvm/hamiltonian 模块。
实现时落实本次新增的 check 与契约；阶段 3 前再评估 demo 耦合几何。
```

## 2026-07-09：阶段 2 实现审查

阶段：

```text
阶段 2 实现审查
```

目标：

```text
审查 Codex 的阶段 2 哈密顿量实现是否满足设计文档与验收清单，给出验收结论。
```

完成的修改：

```text
新建 docs/decisions/2026-07-09-stage2-implementation-review.md（实现审查记录）。
```

观察结果：

```text
实现正确、物理对。
python -m pytest -q：38 passed。
python -m sqvm verify-hamiltonian ...：ok=true，退出码 0，生成 artifact + verification.ipynb。
关键数值与审查时手算预期全部吻合：
  C_mode 对角 77.025/75.8/80.075、非对角 -0.1/-0.075；
  E_C 对角 0.2515/0.2555/0.2419；EJ_eff 14.81/30.02/16.12；
  单模 f01 5.194/7.569/5.332 vs 解析 5.207/7.578/5.343（差 ~0.01 GHz）；
  第一组 gaps 5.19/5.33/7.60；Hilbert 维度 1331；hermiticity 0。
逐模收敛 check 实测印证 N6：coupler 漂移 0.032 GHz 显著大于 q1/q2 的 0.001–0.002。
验收清单 A–J 全通过，红线项无一命中。
```

设计决定：

```text
准予通过阶段 2。发现 7 条 nit（均不阻塞）：
  np_sin cosmetic、契约失败走异常而非 report、junction_flux_pairing 名实不符、
  解析极限容差偏松、两个接口名/导出小出入、offset_charge_ng 仅记录未接入、缺 warn-only 端到端测试。
可选打磨优先级：契约失败收进 report > 代码清晰度 > 其余。
```

运行的测试：

```text
C:\...\codex-primary-runtime\dependencies\python\python.exe -m pytest -q：38 passed。
同解释器 -m sqvm verify-hamiltonian：ok=true，退出码 0。
```

下一步：

```text
人工确认 output/stage_02_hamiltonian/verification.ipynb 后正式收尾阶段 2，
进入阶段 3（静态能谱与 dressed-state 分析）。阶段 3 前再评估 demo 耦合几何。
```

## 2026-07-10：阶段 3 设计收敛与 AI 团队职责

阶段：

```text
阶段 3 设计
```

目标：

```text
把阶段 3 草案收敛成开发 AI 可直接实现、测试 AI 可独立验收的设计，并固化 AI 团队职责分离。
```

完成的修改：

```text
更新 docs/10_development_process.md，增加设计 AI、开发 AI、测试 / 审查 AI和用户的职责边界，
并把旧五阶段文档目录修正为当前十阶段路线图。
更新 docs/stages/03_static_spectrum_plan.md 和 docs/designs/03_static_spectrum_design.md。
新增 docs/decisions/2026-07-10-stage3-design-review.md，作为开发和独立测试交接清单。
```

设计决定：

```text
阶段 3 强制包含 coupler flux scan 与 q1-c / c-q2 avoided-crossing 指标。
dressed-state assignment 使用全局最大 overlap 一一匹配，不使用贪心。
flux scan 使用相邻 eigenvector overlap 的全局匹配跟踪 adiabatic branch。
mode participation 定义为 bare-energy basis 下的模式平均激发数和归一化 fraction。
关键频率、anharmonicity、ZZ 和 crossing splitting 必须通过逐模 cutoff refinement；超容差阻塞 verify。
默认 cutoff 第一候选为 q1=7、c=7、q2=7，不允许通过放宽容差隐藏三个模式的已知漂移。
flux override 通过阶段 2 接口的向后兼容参数在内存中传入，不修改 artifact 或 YAML。
```

运行的测试：

```text
未运行代码测试。本次只修改设计、流程和交接文档。
已用 rg 检查阶段 3 文档中不存在“flux scan 可选”“单点 only”“贪心 assignment”等旧约定。
```

下一步：

```text
由独立审查 AI 对阶段 3 计划、详细设计和交接清单做一致性审查。
设计问题关闭后冻结 v0.1 接口，再交给开发 AI实现。
```

## 2026-07-11：阶段 3 首次独立设计审查处置

阶段：

```text
阶段 3 设计修订
```

目标：

```text
处理独立审查提出的 4 个阻塞项和 2 个中等项，消除未解析 crossing 仍可能通过、
spectator 收敛遗漏、Stage 2 provenance 不足、接口表达不足、运行预算过期和扫描不确定性。
```

完成的修改：

```text
新增 docs/stages/02_1_hamiltonian_rebaseline_plan.md，定义 Stage 2 API / cutoff / artifact 变更后的正式重基线门。
更新 docs/10_development_process.md 和 docs/20_roadmap.md，加入已验收阶段 rebaseline 规则。
更新阶段 3 plan / detailed design / AI handoff checklist。
```

设计决定：

```text
q1-c 和 c-q2 必须都 resolved，StageGateDecision 才允许 ready_for_stage4。
geometry_too_weak 是可记录的物理诊断，但 report.ok=false、CLI exit 1、stage4_ready=false。
每个 crossing 分别提高 q1、c、q2 cutoff，三个 splitting 绝对漂移之和形成保守 cutoff 不确定度。
每个 flux 点保存三条单激发 branch 的 q1/c/q2 participation，并用 character exchange 作为 crossing 必要证据。
Stage 2.1 使用 config、device artifact、Stage 2 model source tree 和 Stage 2 artifact SHA-256；
确定性 manifest 与独立 rebaseline approval 分离，approval 绑定 manifest / artifact SHA-256；
阶段 3 重建最低 12 gaps 与新 artifact 差异 <= 1e-9 GHz，失败为 error。
新增 StaticSpectrumResult 统一聚合对象，crossing convergence 显式消费 FluxScanResult 的 final bracket 和 branch identity。
flux key 固定 Decimal 12 位 ROUND_HALF_EVEN；level bracket、端点复用和终止规则全部确定化。
dimension 更新为 3375；acceptance 预算 1800 秒，smoke 预算 90 秒且不可验收。
超预算只允许已独立验证的 eigsh fallback，否则失败。
```

设计预算与后续用户阶段门：

```text
设计负责人采纳阶段 3 v0.1 暂定误差预算：frequency 0.50 MHz、anharmonicity 1.00 MHz、
ZZ / crossing 0.01 MHz、crossing relative 5%、participation fraction 0.02。
若实际结果为 geometry_too_weak，用户决定调整器件几何，或正式修改项目物理目标。
```

运行的测试：

```text
未运行代码测试。本次只修改设计和流程文档。
完成后运行 git diff --check、Markdown fence 平衡检查和旧约定搜索。
```

下一步：

```text
由独立审查 AI进行第二轮设计审查。
第二轮批准前不冻结设计、不交开发 AI。
```

## 2026-07-11：阶段 3 第二轮独立审查处置

阶段：

```text
阶段 3 设计再修订
```

审查结论：

```text
第二轮仍不批准冻结。新增 4 个阻塞项、2 个高优先级项和 2 个中等项。
```

完成的设计修订：

```text
解除 Stage 2.1 循环 gate：由 Stage 2.1 自包含函数重建最低 12 gaps，阶段 3 后续再重复。
增加 legacy_baseline_anchor.json 和 previous_stage2_artifacts_sha256；当前旧 artifact hash 等待用户确认。
refined cutoff 改为在固定 character evidence domain 独立求解，以 bare participation 锚定 A_like/B_like，
禁止跨不同 Hilbert dimension 做 eigenvector overlap。
minimum 移出 baseline bracket / evidence domain 都定义为 crossing convergence 失败。
crossing flux drift 用 local gap slope 转为 U_flux(MHz)，并纳入 U_total。
逐 flux 保存 bare transition frequencies / detunings，以容差化 sign-change 证据定义 geometry_too_weak。
baseline / refined dimensions 更新为 3375 / 4275；保守 acceptance 上限更新为 509 solves。
增加 dense_pilot、solver_validation artifact + approval、validated eigsh acceptance 和 dimension-specific ExecutionPlan。
Stage 3 同时检查 Stage 2 schema_version=0.2 / artifact_version=0.2。
加强 roadmap 的 Stage 3 验收门。
```

设计预算：

```text
设计负责人采纳 v0.1 预算：frequency 0.50 MHz、anharmonicity 1.00 MHz、
ZZ / crossing 0.01 MHz、crossing relative 5%、participation 0.02。
```

仍在等待：

```text
用户确认 legacy Stage 2 artifact SHA-256：
222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C。
第三轮独立设计审查结论。
```

运行的测试：

```text
未运行实现测试；只修改设计 / 流程文档。
git diff --check 通过。
```

## 2026-07-11: Stage 3 third-review disposition and external AI channels

Review result:

```text
The third independent review did not approve freeze. Four high-priority and two medium findings were dispositioned.
```

Design corrections:

```text
Bound exact eigsh spec, Stage 3 solver source-tree SHA-256, environment fingerprint, deterministic v0,
and repeatability evidence to ValidatedSolverSpec and every acceptance solve.
Separated the Stage 2.1 dense 12-state 1e-9 GHz provenance check from acceptance eigsh validation.
Changed ExecutionPlan to adaptive remaining-job upper bounds; conservative acceptance ceiling is 509 solves.
Defined deterministic U_flux slope stencils and forced baseline final-bracket keys into refined evidence grids.
Removed the Stage 2.1/Stage 3 circular wording from the roadmap.
```

External AI channels:

```text
Review: 019f4cba-6ef4-7160-ba5b-7f74ea5b31d8
Development: 019f4ec0-b12f-7b83-b3ff-c75dc7b93b52
Independent test/acceptance: 019f4ec2-530b-7ce0-98f1-9885e123c020
Development and test remain on hold until design freeze.
```

Remaining gate:

```text
User confirmation of legacy Stage 2 artifact SHA-256:
222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C.
No implementation, artifact overwrite, formal rebaseline, or gate approval is authorized before the relevant gate.
Fifth independent design review is pending.
```

## 2026-07-11: Stage 3 fourth-review disposition

Review result:

```text
NOT APPROVED: three high and one medium design-contract findings.
```

Corrections:

```text
Raised the conservative acceptance ceiling from 491 to 509 solves by counting up to three forced
baseline-bracket keys in every refined initial grid.
Defined dense-reference near-degenerate partitioning, ambiguity/truncation failures, fixed index mapping,
and spectral matrix 2-norm projector gates for dense/eigsh and repeat comparisons.
Defined sha256_counter_v1 canonical seed bytes and a normative seed/block-0 digest test vector.
Restricted pre-anchor work to the Stage 2.1 gate and non-mutating Stage 2.1 fixtures; Stage 3 implementation
cannot start until Stage 2.1 is approved.
```

Status:

```text
Development and independent test remain on hold.
Legacy Stage 2 artifact user confirmation remains a separate external gate.
Fifth independent design review is required before freeze.
```

## 2026-07-11: Stage 3 v0.1 design freeze approval

Independent review:

```text
Fifth review decision: APPROVED.
No blocking, high-priority, or medium-priority findings remain.
Reviewer task: 019f4f05-aaa2-7fe2-8769-8eece01ecc08.
```

Freeze result:

```text
Stage 3 v0.1 design is frozen.
Development may begin only with the Stage 2.1 gate implementation and non-mutating Stage 2.1 fixtures.
Formal rebaseline, old artifact overwrite, Stage 2.1 approval, and all Stage 3 implementation remain blocked
until the user confirms the legacy Stage 2 artifact trust anchor.
```

Remaining external gate:

```text
Candidate legacy SHA-256:
222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C.
Independent review confirmed the bytes hash but did not substitute for user acceptance.
```

## 2026-07-11: Stage 2.1 rebaseline approved

Result:

```text
Stage 2.1 gate decision: APPROVED.
Development full regression: 107 passed.
Independent regression: 105 passed, 2 production-runner tests deselected and reviewed against developer evidence.
Dense 12-gap rebuild max difference: 0.0 GHz.
N=7 to N=9 drifts: q1=0.000386739774 MHz, c=0.097365327832 MHz, q2=0.001101029277 MHz.
All three convergence drifts pass the 0.50 MHz gate.
```

Approved hash chain:

```text
previous artifact: 222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C
accepted anchor: FE489B2476FA3AE3121BEBB1FA06BF5EF1B74DEDAD4546458C7687EA7431C88D
candidate artifact: DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66
manifest: 4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D
approval: CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9
independent review record: 5776B27ECFFE6D1E2D59520F19288C6CB9835B6BFDA9706305E22B06E17D8A55
```

Next:

```text
Start Stage 3 implementation from the frozen v0.1 design.
Development must first produce the implementation, smoke evidence, dense pilot, and solver-validation candidate.
Independent test/review must approve the solver backend before the formal Stage 3 acceptance run.
```

## 2026-07-11: Stage 3 C2 blocked by non-finite diagnostic serialization

Result:

```text
C2 formal acceptance: BLOCKED (implementation_error).
The run exited after about 32.5 seconds with canonical JSON rejecting an Infinity value.
This was not a runtime-budget failure, and no numerical or geometry conclusion is approved.
No formal Stage 3 artifact or verification notebook was written; Stage 4 remains blocked.
```

Disposition:

```text
The C2.1 clarification preserves zero as 0.0 and encodes unavailable diagnostics as null plus explicit
availability/status/reason fields. Resolved and geometry_too_weak both require valid finite uncertainty inputs.
NaN/Infinity remain forbidden, and artifact serialization gains a non-mutating finite preflight and atomic write.
No physical model, tolerance, scan, cutoff, or frozen design contract changes are authorized.
```

Required sequence:

```text
Implement and test C2.1 without rerunning formal acceptance.
Regenerate solver validation because the Stage 3 source-tree hash changes.
Obtain a new independent solver approval.
Then run one formal C2 acceptance attempt and hand its artifact to independent test.
```

## 2026-07-11: Stage 3 formal diagnostic delivery completed; Stage 4 blocked

Independent disposition:

```text
ANALYSIS APPROVED / STAGE4 REJECTED.
Stage 3 implementation and formal diagnostic delivery are complete.
The Stage 3 gate did not pass: status=unresolved_crossing, stage4_ready=false.
No implementation or artifact-integrity finding remains.
```

Formal evidence:

```text
artifact: 7D6D3D8DAD7187217F0559D324653B3E5A87E3AE623FA94A0F90715D0A4F5DE2
notebook: 0A5CAC7779ABECBEF5DFF8A365AAEE74EEAF30AE240EAB1E7EFBC34FBE8F578E
independent review: D3B0DEAAB09065A8E043B6CA7E607465E8721C6A7E023A05AABB36A54BCECE21
runtime: 31.695474300009664 seconds, 81 solver evaluations, within the 1800-second budget
safe regression: 307 passed, 3 production-runner tests deselected
```

Gate blockers:

```text
q1-c: numerically_unconverged. The level-2 minimum reached a boundary, the right character-evidence
point was unavailable, and the required three-mode crossing refinement rows could not be formed.
c-q2: low_continuity. Crossing location, character exchange, target-pair participation, three-mode
uncertainty, and significance passed, but branch continuity was 0.7033494766689148 below the frozen 0.90 gate.
```

Project decision:

```text
Stage 4 must not start.
The formal Stage 3 artifact and notebook remain immutable evidence of the current model result.
Any new scan range/grid, tolerance, continuity rule, branch-tracking method, status rule, or device geometry
requires a versioned Stage 3 design iteration and independent review; it must not overwrite this result.
```

## 2026-07-11: Stage 3 objective corrected to q1-q2 coupling sweep

User requirement clarification:

```text
The required avoided crossing is q1-q2, not q1-c or c-q2.
At fixed coupler flux, tune a qubit through q1-q2 resonance and observe the avoided crossing.
Then change coupler flux, repeat the qubit-resonance scan, and verify that the q1-q2 splitting changes.
```

Disposition:

```text
The Stage 3 v0.1 implementation and formal diagnostic remain valid historical evidence but answer the
wrong acceptance question. They do not authorize Stage 4.
Stage 3.1 is a versioned redesign with q1 fixed at 0.10 Phi0, q2 as the inner resonance-scan axis, and
coupler flux as the outer control axis. It reports minimum q1-q2 splitting and magnitude-only
abs(g_eff)=splitting/2, with a hard coupler-participation gate.
```

Read-only feasibility pilot:

```text
q1-q2 resonance occurs near q2=0.099755 Phi0.
At c=0.270 Phi0, splitting was about 4.950 MHz.
At c=0.394 Phi0, splitting was about 4.431 MHz with about 1.23% maximum coupler participation.
At c=0.396 Phi0, maximum coupler participation rose to about 16.46%, confirming the need to reject
three-mode hybridized points from a pairwise q1-q2 coupling claim.
At c=0.400 Phi0, splitting was about 5.164 MHz.
These pilot values select the design range only and are not formal convergence evidence.
```

Next:

```text
Independently review and freeze the Stage 3.1 objective correction, design, and execution plan.
Do not start implementation or modify the Stage 3 v0.1 formal output before design approval.
```

## 2026-07-11: Stage 3.1 design review round 1 not approved

Independent result:

```text
The corrected fixed-coupler/q2-scan experiment, q2 range, acceptance anchors, full-flux v2 identity,
normative seed vector, and 1381 formal solve ceiling were accepted in principle.
Design freeze was rejected because the first candidate did not fully define numerical resolved
predicates, resonance alignment for splitting/2, the schema-0.2 config, evidence-domain continuity,
or the separation between computational gate and post-write verification.
```

Round 2 disposition:

```text
Added strict configs/spectra/2q1c_q1q2_coupling.yaml schema and provenance binding.
Added a local |100>/<100| + |001>/<001| bare-projector gate, total-excitation bound, full-domain coupler
participation checks, deterministic character endpoints, zero-detuning root bracket, and a 0.50 MHz
resonance-alignment gate. Invalid pairwise/alignment evidence leaves abs_g_eff null.
Defined U_cutoff as the sum of the three absolute splitting shifts, the exact immediate-neighbor
U_inner_flux stencil, U_level, U_solver, and the 0.01 MHz / 5% / 5x predicates. U_delta must be finite
and positive for modulation significance.
Expanded solver validation from 28 to 44 cases so all three acceptance anchors have left/minimum/right
coverage, plus idle and a three-mode diagnostic.
Separated the computational StageGateDecision from artifact/notebook writes and the final canonical
verification_report.json. The artifact is never rewritten.
```

## 2026-07-11: Stage 3.1 final acceptance and Stage 4 design start

Stage 3.1 result:

```text
Independent final acceptance: APPROVED.
Stage4ReadinessReport: ok=true, stage4_ready=true, blocking_reasons=[].
Final approval SHA-256: 5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D.
Required q1-q2 anchors at coupler flux 0.200, 0.270, and 0.385 Phi0 are resolved.
The corresponding |g_eff| magnitudes are about 2.476254, 2.475067, and 2.436837 MHz.
Coupler-flux modulation passed its independently recomputed uncertainty/significance gate.
```

Stage 4 design work:

```text
Added a proposed Stage 4 scope decision.
Added a Stage 4.0 control-channel compatibility plan.
Added the Stage 4 execution plan and detailed control-signal design.
No implementation, config, accepted artifact, solver result, or output was modified by this design step.
```

Design decisions:

```text
Stage 4 owns a strict seven-channel control registry. It preserves the five Stage 1 channel declarations
and adds q1_z/q2_z through an independently approved compatibility gate, without mutating the accepted
device/Hamiltonian/spectrum chain.
AWGs sample baseband I/Q/Z only; GHz carriers remain metadata.
The v0.1 electronics model includes signed 16-bit DAC quantization, 2 GS/s timing, integer-sample latency,
causal FIR response, and static XY/Z/readout mixing matrices.
Stage 4 produces signals only. QuTiP evolution, gate fidelity, calibration updates, noise, ADC, and readout
classification remain downstream.
```

Tests:

```text
Documentation checks are run after the draft is complete.
No implementation or numerical acceptance test is authorized before independent design review.
```

Next:

```text
Submit the Stage 4 scope, Stage 4.0 plan, Stage 4 plan, and detailed design for independent design-freeze
review. Keep development and the new independent test AI idle until the design is approved.
```

## 2026-07-14: Stage 6 platform runtime implemented and accepted

Stage:

```text
Stage 6 experiment runtime
```

Completed work:

```text
Implemented strict schema-0.1 request admission with program fixed to null.
Implemented deterministic scan expansion, immutable registries, fake backend, lifecycle, cancellation, budgets,
canonical event journal, raw dataset, provenance snapshots, no-replace publication, recovery, verification,
catalog rebuild, and eight CLI operations.
Added a versioned future Gate/Macro IR -> Pulse IR -> Stage 4 boundary without enabling instruction execution.
Closed all development, independent-test, physics-boundary, and release-review findings.
```

Verification:

```text
Independent Stage 6 tests: 35 passed.
Joint Stage 5/provenance/Stage 6 regression: 56 passed.
Two formal Stage 6 smoke roots completed, independently verified, and rebuilt their catalogs.
All deterministic request, point-table, dataset, response, device, calibration, environment, and source bytes
matched across both roots.
The repository-wide historical suite remains unavailable because ignored Stage 1-4 output fixtures are absent.
```

Decision:

```text
Stage 6 is accepted as a platform-only experiment runtime framework.
It makes no physical, calibration, observation, measurement, or Stage 7 readiness claim.
```

Next:

```text
Commit and push the accepted Stage 6 implementation and evidence records.
Begin Stage 7 detailed design only after user confirmation of its physical experiment and instruction contracts.
```

## 2026-07-14: Stage 7 calibration-experiment design drafted

Stage:

```text
Stage 7 design only
```

Completed work:

```text
Drafted the Stage 7 entry/phasing decision, stage plan, and detailed design.
Defined the Gate/Macro IR -> Pulse IR -> Stage 4.1 control pipeline and typed scan_ref binding.
Separated model execution, analysis, recommendation, and atomic human-decision/calibration publication.
Added explicit Stage 4.1 and Stage 5.1 rebaseline gates because the accepted APIs are fixed-profile.
Defined a bootstrap policy that cannot use X2P/Y2P/CZ before an accepted simulation calibration exists.
```

Design result:

```text
Recommended order is Stage 7.0 compiler/evidence foundation, Stage 7.1 single-qubit calibration,
Stage 7.2 coupler calibration, then Stage 7.3 CZ coarse scan.
Stage 5 formal qualification and Stage 5.1 remain mandatory before any model-derived simulation scan.
Stage 8 remains mandatory for IQ, shots, readout, assignment, and measurement claims.
```

Tests and execution:

```text
No implementation, numerical experiment, artifact, dependency, or test was created or run by this design step.
Four independent project roles supplied architecture, physics, test, and release constraints.
```

Next:

```text
Obtain user confirmation of phasing, approval identity, single-qubit-first scope, and q1-to-q2-only CZ.
Then submit the current design bytes for independent design-freeze review before implementation.
```

## 2026-07-14: Stage 7 QCIS architecture incorporated

Stage:

```text
Stage 7 design revision only
```

Completed work:

```text
Read the user-supplied QCIS说明.md source (14192 bytes,
SHA-256 C638A4EE10E0F2D98B20E7A6B0E7ADD6718CADFC3EA00AEFC15F87961FF15338).
Made QCIS the sole external control-program format for all Stage 7 and later calibration experiments.
Defined deterministic template binding, strict parsing, QAgent/configuration/waveform resolution, discrete
timing, logical waveform generation, Stage 4.1 compilation, and Stage 5.1/QuTiP consumption boundaries.
Mapped the earlier internal PLXY/PULSE/WAIT/FRAME_CHANGE/BARRIER concepts to QCIS PLSXY/PLS/I/RZ/B.
Recorded incomplete source opcodes and formulas as reserved and fail-closed rather than inventing semantics.
Recorded the QCIS-only external-program architecture as a user-confirmed product decision; detailed profile
bytes remain proposed and do not authorize implementation.
Added freeze-level QCIS byte-oracle vectors for template materialization, append/absolute timing, I/B/RZ trace,
logical waveform hashes, and stable unsupported-feature rejection reasons.
```

Design result:

```text
Every recommendation-bearing calibration point must be traceable from concrete QCIS bytes through exact
logical/effective waveform hashes to the Hamiltonian coefficients consumed by QuTiP.
The external temporary source was mirrored byte-identically at docs/references/QCIS说明.md and hash-bound by
docs/references/qcis_source_snapshot.json.
No Stage 7 implementation or physics execution was authorized by this revision.
```

## 2026-07-14: Stage 7 QCIS design frozen and implementation branch opened

Stage:

```text
Stage 7.0 design freeze
```

Completed work:

```text
Accepted the four-phase delivery, local approval identity, q1-before-q2 lineage, and q1-to-q2 CZ MVP choices.
Froze the QCIS-only compiler path, typed AST, timing/frame/barrier semantics, waveform formulas, authority chain,
Stage 4.1/5.1 boundaries, exact rejection reasons, and byte-oracle test vectors.
Bound seven design/reference authorities by SHA-256 in the Stage 7 QCIS design-freeze decision.
Created the dedicated codex/stage7-qcis-calibration implementation branch after the design was completed.
```

Authorization boundary:

```text
Stage 7.0 compiler, fake fixtures, and evidence-ledger work may begin.
Stage 7.1-7.3 model-derived QuTiP scans remain closed until every prerequisite gate is independently approved.
Formal Stage 7.0 release remains closed until independent test and release owners approve implementation evidence.
```

Next:

```text
Commit and push the frozen design baseline.
Assign non-overlapping implementation, physics review, independent test, and release-evidence responsibilities.
```
