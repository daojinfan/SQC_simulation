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
