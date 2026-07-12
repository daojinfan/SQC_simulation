# 开发路线图

## 总览

项目按窄而完整的纵向切片推进。每个切片都必须产生可运行、可检查的结果。

```text
阶段 1: 器件配置与参数模型
阶段 2: 2q1c 哈密顿量构建
阶段 2.1: Hamiltonian 重基线门（仅在阶段 2 已验收内容发生变更时触发）
阶段 3: 静态能谱与 dressed-state 分析
阶段 4: 控制信号链
阶段 5: QuTiP 时间演化
阶段 6: 虚拟实验运行框架
阶段 7: 第一批校准实验
阶段 8: 读出谐振腔与测量模型
阶段 9: Web lab 界面
阶段 10: 校准后的门级仿真
```

## 阶段 1: 器件配置

目的：

```text
用物理参数清晰表示一台 2q1c2r 器件。
```

验收：

```text
device.yaml 可以加载
参数可以验证
电容网络可以检查
结电阻 Rn / EJ 可以解析
```

## 阶段 2: 2q1c 哈密顿量

目的：

```text
从 q1、q2 和 coupler 的电容与结参数构建哈密顿量。
```

验收：

```text
可以生成哈密顿量矩阵
维度定义清楚
单位约定有文档记录
简单能谱可复现
```

## 阶段 2.1: Hamiltonian 重基线门

触发条件：

```text
阶段 2 验收后又修改 Hamiltonian API、配置、cutoff、artifact schema 或数值实现。
```

验收：

```text
阶段 1/2 完整回归和端到端 verify 通过
配置、器件 artifact、阶段 2 源码和新 artifact 由 SHA-256 内容摘要绑定
旧基线到新基线的数值变化有明确报告
Stage 2.1 independently rebuilds the lowest 12 gaps through Stage 2 APIs and checks the candidate artifact at error severity.
Stage 3 repeats the same dense consistency check after implementation; it is not a prerequisite of Stage 2.1 approval.
独立审查批准新基线
```

## 阶段 3: 静态表征与 q1-q2 耦合验证

目的：

```text
计算频率、非谐性和 residual ZZ，并在固定 coupler flux 下通过 q2 flux 将 q1/q2 拉到共振，
验证 q1-q2 avoided crossing 以及 coupler flux 对有效耦合强度的调制。
```

验收：

```text
Stage 2.1 provenance、manifest、approval 和内容 SHA-256 全部通过
关键频率、非谐性和 ZZ 达到数值收敛预算
在 coupler flux 0.200、0.270、0.385 Phi0 三个锚点获得 q1-q2 resonance、character exchange、
target-subspace continuity、低 coupler participation 和三模式 cutoff convergence 证据
三个锚点的 splitting/2 可作为 magnitude-only |g_eff|，且 coupler-flux modulation 显著通过
acceptance 使用已批准 solver backend 并通过 runtime gate
生成 canonical artifact、真实执行 verification notebook、report 和独立 hash-bound approval
Stage4ReadinessReport 验证 stage4_ready=true
```

## 阶段 4: 控制信号链

目的：

```text
用尽量接近真实实验系统的方式表示 XY、Z 和读出控制通道，把物理 pulse 请求编译成
采样 AWG 基带波形与 effective device signal。
```

验收：

```text
logical pulse -> AWG waveform -> effective device signal
q1/q2 XY、q1/q2/c Z、r1/r2 readout 通道都有严格 registry 和单位
采样、DAC 量化、延迟、FIR、静态串扰/混频和 clipping 可检查
波形分层绘图可检查，时序和物理通道冲突 fail closed
独立 approval 验证 stage5_ready=true
```

## 阶段 5: QuTiP 演化

目的：

```text
使用 QuTiP 在哈密顿量和控制信号下演化量子态。
```

验收：

```text
单个脉冲仿真可以运行
末态可以保存
population 和 leakage 图可以生成
```

## 阶段 6: 实验运行框架

目的：

```text
运行可由人配置的虚拟实验，并保存原始数据和元数据。
```

验收：

```text
实验配置可以加载
扫描可以运行
dataset 可以保存
run metadata 记录器件和校准状态
```

## 阶段 7: 校准实验

目的：

```text
实现第一批真正有用的实验。
```

初始实验：

```text
qubit spectroscopy
Rabi / X2P amplitude scan
Ramsey frequency refine
DRAG beta scan
coupler flux curve
CZ coarse scan
```

验收：

```text
每个实验都产生原始数据、图、拟合结果和推荐更新
参数更新必须人工接受
```

## 阶段 8: 读出模型

目的：

```text
加入 r1 和 r2 读出谐振腔，生成类测量输出。
```

验收：

```text
读出脉冲可以表示
可以生成合成 IQ 或 population 读出
读出校准实验可以运行
```

## 阶段 9: Web Lab

目的：

```text
通过浏览器界面展示器件参数、实验配置、运行数据和分析结果。
```

验收：

```text
用户可以查看当前参数
用户可以发起或检查实验
图和拟合结果可见
用户可以接受或拒绝推荐更新
```

## 阶段 10: 校准后的门仿真

目的：

```text
使用已接受的校准参数运行简单门程序。
```

验收：

```text
X2P 和 CZ 类门仿真可以运行
末态和指标可以保存
结果可以在 Web lab 中查看
```
