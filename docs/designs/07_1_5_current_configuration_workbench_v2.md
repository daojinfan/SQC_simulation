# Stage 7.1.5 当前配置工作台 v2 详设

状态：已实现

## 1. 核心模型

Web 配置管理采用三层对象：

1. 当前配置（Current Configuration）：每台设备只有一份，可直接编辑和保存，是用户日常维护的工作副本。
2. 配置快照（Snapshot）：由当前配置显式保存得到，内容不可编辑，用于留档、比较和恢复。
3. 运行绑定版本：实验运行仍绑定不可变内容与哈希，不直接引用一个可能继续变化的浏览器表单。

旧 Draft、Publish、Active API 保留为兼容层，但不再出现在新版 Web 主流程中。快照的“应用”语义是把快照内容复制回当前配置并形成新的当前修订，不是把当前配置指针切换到快照对象。

## 2. 持久化

当前配置保存在 `output/platform-configurations/current/<device_id>.json`。文档包含：

- 设备、名称、备注、操作者与更新时间；
- 单调增加的当前修订号；
- 来源快照与父版本引用；
- 只读设备/编译器 authority；
- 可编辑的 control 与 calibration 分区；
- 当前内容哈希、校验状态和字段错误。

旧安装首次访问时，按 Active 快照、最新快照、最新 Draft 的顺序迁移生成当前配置。迁移不会修改来源对象。

## 3. 操作语义

### 保存当前配置

- 使用 `expected_content_sha256` 做乐观并发控制；
- 只允许 control 与 calibration 可编辑分区；
- 拒绝物理器件参数、生成字段和非有限数；
- 保存后立即重新校验并增加当前修订号；
- 保存不自动创建用户可见快照。

### 保存快照

- 当前配置必须校验通过；
- 服务端生成不可变记录的 revision、hash 和 calibration run identity；
- 可选择长期保存；普通快照继续遵守最近 10 个的自动保留策略；
- 保存完成后，当前配置以新快照作为后续修订基线，但波形和控制数值不变。

### 恢复快照

- 用户在只读快照页选择“恢复到当前配置”；
- 服务端校验当前内容哈希，避免覆盖并发修改；
- 快照值被复制并转换为可编辑记录；
- 当前修订号增加，来源快照更新；
- 原快照保持不可变。

## 4. API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/current-configurations/{device_id}` | 读取当前配置 |
| PUT | `/api/v1/current-configurations/{device_id}` | 直接保存当前配置 |
| POST | `/api/v1/current-configurations/{device_id}/initialize-calibration` | 初始化 typed calibration |
| POST | `/api/v1/current-configurations/{device_id}/snapshots` | 保存当前配置快照 |
| POST | `/api/v1/platform-snapshots/{snapshot_id}/apply` | 恢复快照到当前配置 |

## 5. Web 信息架构

配置中心默认显示“当前配置”，其次是“快照历史”和“旧版记录”。当前配置工作台保留对象导航：概览、Q1、Q2、C、控制链、状态与版本。

所有普通配置项按纵向参数行排列。每一行必须同时提供：

- 参数显示名称与单位；
- 数据类型（整数、浮点数、布尔值、枚举、文本或数组）；
- 参数含义；
- 当前值编辑控件。

波形 Setting、Mapper、电子学通道和混合矩阵使用可展开目录。组合门先按 CZ/FSIM Setting 分组，再在内部按 Q0、Q1、Coupler 波形分组。折叠不改变字段值或保存语义。

空闲磁通按物理对象显示：`idle_flux_phi0.q1` 位于 Q1 基础配置，`idle_flux_phi0.q2` 位于 Q2 基础配置，`idle_flux_phi0.c` 位于 C 的 CZ/FSIM 配置。底层仍统一存储在 `control_values.idle_flux_phi0`，仅改变 Web 对象归属；控制链页面不重复显示这些字段。

G2 Mapper 固定保存为两个等长浮点数组：`coupling_detune_GHz: list[float]` 与 `zbias_offset_phi0: list[float]`。相同下标组成一个插值点；耦合失谐轴必须严格递增，数组至少包含两个元素，并且点集包含 `(0, 0)`。Web 直接编辑两个逗号分隔 list，不转换为配对点对象。

## 6. 验收条件

1. 用户无需创建 Draft 即可编辑并保存当前配置。
2. 当前配置保存后修订号增加，校验错误在对应字段显示。
3. 有未保存修改时不能保存快照。
4. 快照页没有可编辑控件，并提供恢复到当前配置的明确操作。
5. 恢复快照不会修改快照文件，并产生新的当前配置修订。
6. 每个普通参数显示数据类型和参数含义。
7. 波形、通道和矩阵可分组展开且保持纵向阅读顺序。
8. 1440 × 900 与 390 × 844 均无页面级横向溢出；对象导航可局部横向滚动。
