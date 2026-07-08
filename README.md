# SQC Simulation

本项目用于构建一台最小单元的虚拟超导量子计算机。

目标机器是一个聚焦的 `2q1c2r` 器件：

```text
q1, q2  : 两个量子比特
c       : 一个可调耦合器
r1, r2  : 两个读出谐振腔
```

项目目标是一步一步构建这台虚拟量子计算机：

```text
芯片物理参数
  -> 2q1c 哈密顿量
  -> 尽量贴近真实系统的控制与读出信号
  -> 虚拟实验
  -> 实验分析与校准参数
  -> 面向人工操作实验的 Web lab
```

开发过程采用分阶段推进。每个阶段都必须先有计划，再实现、测试、检查结果并写入开发日志，然后再进入下一阶段。

建议阅读顺序：

```text
docs/00_project_vision.md
docs/10_development_process.md
docs/20_roadmap.md
docs/stages/01_device_model_plan.md
docs/logs/DEVELOPMENT_LOG.md
docs/references/README.md
```
