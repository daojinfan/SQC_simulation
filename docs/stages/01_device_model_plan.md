# 阶段 1 计划：器件配置与参数模型

## 阶段目的

创建目标 `2q1c2r` 虚拟量子计算机的最小可靠表示。

本阶段暂不构建完整哈密顿量，而是先让器件定义清晰、可验证、可检查。

## 参考来源

本阶段主要参考李少炜博士论文和旧 V1 项目。参考内容只用于确定器件配置、参数组织、电容网络和耦合器相关基础表示，不直接进入哈密顿量推导：

```text
1. 李少炜博士论文：
   作为阶段 1 的主要论文参考。
   参考超导量子比特高精度控制、双比特门、耦合器和实验校准背景。
   本阶段重点从中提炼器件组成、耦合器角色、控制/校准需要暴露哪些基础物理参数。

2. 旧 V1 项目：
   作为阶段 1 的主要工程参考。
   参考 device.yaml、NodeGraph、电容矩阵、结参数解析和参数管理思想。

3. 范道金博士论文：
   本阶段仅作为次要背景参考。
   主要留给后续控制链路、标定流程、实验组织和 Web lab 设计阶段继续使用。
```

本阶段暂不处理：

```text
哈密顿量矩阵具体构建
控制脉冲演化
读出动力学
具体量子门标定实验
```

## 范围

本阶段包含：

```text
device.yaml schema
组件定义
电容网络
结电阻和 EJ 解析
基础验证
参数快照
device_artifacts.json
verification.ipynb
```

本阶段不包含：

```text
QuTiP 仿真
完整脉冲控制
Web UI
校准实验
```

## 输入

第一版器件配置应包含：

```text
schema_version
device name
q1, q2, c 组件参数
r1, r2 读出谐振腔参数
电容网络
junction 或 SQUID 参数
控制通道名称
读出通道名称
```

## 输出

本阶段应产生：

```text
DeviceSpec object
ParameterStore 或等价对象
capacitance matrix
junction parameter table
device_artifacts.json
verification.ipynb
```

## 核心对象

初始对象：

```text
DeviceSpec
ComponentSpec
CapacitorSpec
JunctionSpec
ReadoutResonatorSpec
ControlChannelSpec
DeviceParameters
```

这些对象应保持简单。第一版只需要支持 `2q1c2r`。

## 公开接口

第一阶段应暴露以下稳定接口：

```text
load_device(path) -> DeviceSpec
validate_device(device) -> ValidationReport
build_capacitance_matrix(device) -> CapacitanceMatrix
resolve_junction_parameters(device) -> JunctionParameterTable
write_device_artifacts(device, output_dir) -> DeviceArtifactSet
verify_device(path, output_dir) -> VerificationReport
```

接口约定：

```text
配置文件和内部 summary 中，所有电容使用 fF。
除非明确标注，所有频率和能量使用 GHz。
结电阻使用 ohm。
磁通偏置使用 Phi0 单位。
错误信息必须指出导致问题的配置路径。
```

第一条面向用户的检查命令：

```text
python -m sqvm verify-device configs/devices/2q1c2r.yaml --output output/stage_01_device_model
```

详细接口、配置格式和输出格式见：

```text
docs/designs/01_device_model_design.md
```

## 正确性检查

本阶段应通过以下方式提供正确性证据：

```text
1. 配置加载和验证的单元测试。
2. 示例器件生成确定性的电容矩阵。
3. 检查电容矩阵对称性。
4. 检查电容矩阵对角元为正。
5. 检查已连接电容对应的非对角元为负。
6. 输出 junction Rn/EJ 解析表。
7. 输出 verification notebook。
```

verification notebook 必须从 `device_artifacts.json` 读取数据，并明确展示：

```text
device name
component list
capacitance matrix node order
capacitance matrix values
junction parameter values
checks passed
checks failed
warnings
```

## 测试

必需测试：

```text
加载合法 device config
拒绝缺少 q1/q2/c 的配置
拒绝负电容
拒绝非法结电阻
构建指定 shape 的电容矩阵
从 Rn 解析 EJ
写出 device_artifacts.json
生成已执行的 verification.ipynb
```

## 输出产物

示例产物应写入：

```text
output/stage_01_device_model/
```

预期文件：

```text
device_artifacts.json
verification.ipynb
```

## 验收标准

阶段 1 完成条件：

```text
1. 示例 2q1c2r device config 可以加载。
2. 验证错误清晰。
3. 电容矩阵生成是确定性的。
4. 结参数可以解析并列出。
5. 公开接口有文档说明，并被测试覆盖。
6. verification.ipynb 从 device_artifacts.json 读取数据，并清楚说明 pass/fail 检查。
7. 测试通过。
8. 开发日志记录结果。
```

## 已确认的关键设计

本阶段已经确认：

```text
q1/q2/c 全部使用 tunable SQUID 模型。
q1/q2 是 floating transmon。
c 是 grounded tunable coupler。
EJ 默认从 Rn 自动估算。
r1/r2 只作为读出组件和 metadata 记录，不进入阶段 1 默认 2q1c 电容矩阵。
q1-q2 直接电容支持可选，示例配置先保留。
priors 只作为参考信息，不参与阶段 1 核心计算。
无 error 即 ok，warning 只提示。
论文参数只作为参考，不作为阶段 1 严格范围约束。
配置字段第一版强制带单位。
阶段 1 默认输出 device_artifacts.json 和已执行的 verification.ipynb。
```
