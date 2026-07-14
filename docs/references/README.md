# 参考资料

本目录记录项目设计中使用的论文、旧代码和外部资料。

原则：

```text
1. 参考资料只作为设计依据，不直接替代本项目的接口和验收标准。
2. 每个阶段如果采用某篇资料中的物理模型、实验流程或参数约定，必须在阶段设计文档中明确说明。
3. 对论文内容的引用应尽量落到具体主题，例如电容网络、结参数、哈密顿量、控制脉冲、读出或校准流程。
4. 不确定的论文理解必须标为待确认，不能写成已验证事实。
```

## 本项目主要参考 PDF

### 范道金博士论文

文件：

```text
D:\claude\superconducting_simulation\superconducting-qc-sim-lab\基于超导量子计算的大规模传输与量子门标定方案的研究.pdf
```

元数据：

```text
题名：基于超导量子计算的大规模传输与量子门标定方案的研究
作者：范道金
页数：158
完成时间：2025-03-18
```

本项目重点参考方向：

```text
大规模超导量子计算系统中的传输与控制链路
量子门标定流程
实验配置、数据处理和人工判断流程
读出与实验结果组织方式
```

阶段使用说明：

```text
阶段 1 只作为次要背景。
后续控制信号链、实验运行框架、校准流程和 Web lab 阶段重点参考。
```

### 李少炜博士论文

文件：

```text
D:\claude\superconducting_simulation\superconducting-qc-sim-lab\李 - 2022 - 超导量子比特高精度调控与高保真度双比特门实现.pdf
```

元数据：

```text
题名：超导量子比特中的高精度调控与高保真度双比特门实现
作者：李少炜
页数：132
完成时间：2021-05-28
```

本项目重点参考方向：

```text
超导量子比特高精度控制
双比特门实现和调控策略
耦合器相关模型
控制脉冲与实验校准方法
读出相关实验背景
```

阶段使用说明：

```text
阶段 1 主要参考此论文和旧 V1 项目。
阶段 1 重点关注器件组成、耦合器角色、双比特门背景，以及后续控制和校准需要提前保留的基础参数。
```

## 阶段引用规则

每个阶段的详细设计文档应包含“参考来源”小节，格式如下：

```text
参考来源：
1. 范道金博士论文：用于参考 ...
2. 李少炜博士论文：用于参考 ...
3. 旧 V1 代码：用于参考 ...

本阶段不直接采用或暂不处理：
1. ...
```

## Stage 7 QCIS source

The user-supplied `QCIS说明.md` has a byte-identical repository mirror at `docs/references/QCIS说明.md`; metadata,
source path, byte count, limitations, and raw SHA-256 are bound in `docs/references/qcis_source_snapshot.json`.
Because it was extracted from video frames, it remains reference input rather than executable authority;
incomplete formulas/opcodes stay fail-closed.
