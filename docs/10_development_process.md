# 开发流程

## 阶段规则

每个阶段必须按下面顺序推进：

```text
计划 -> 实现 -> 测试 -> 检查结果 -> 写日志 -> 决定下一阶段
```

当前阶段没有满足以下条件时，不进入下一阶段：

```text
设计说明
清晰的公开接口
可运行代码
测试或验证脚本
示例输出
人工可检查的正确性证据
开发日志
已知限制
```

## 阶段计划模板

每个阶段计划应包含：

```text
阶段目的
范围
输入
输出
核心对象
公开接口
正确性检查
用户工作流
实现任务
测试
输出产物
验收标准
开放问题
```

## 开发日志规则

开发日志记录实际发生了什么，不是润色后的设计文档。

每条日志应包含：

```text
日期
阶段
目标
完成的修改
运行的测试
观察到的结果
设计决定
问题
下一步
```

## 文档目录

项目级文档：

```text
docs/00_project_vision.md
docs/10_development_process.md
docs/20_roadmap.md
```

阶段计划：

```text
docs/stages/01_device_model_plan.md
docs/stages/02_hamiltonian_plan.md
docs/stages/03_control_signal_plan.md
docs/stages/04_experiment_runtime_plan.md
docs/stages/05_visualization_web_plan.md
```

开发日志：

```text
docs/logs/DEVELOPMENT_LOG.md
```

设计决策：

```text
docs/decisions/
```

结果说明：

```text
docs/results/
```

参考资料：

```text
docs/references/
```

## 目标代码结构

代码库后续应逐步形成下面的结构：

```text
src/sqvm/
  device/
  hamiltonian/
  control/
  simulation/
  experiments/
  analysis/
  web/

configs/
  devices/
  experiments/

tests/

output/
```

包名 `sqvm` 表示 superconducting quantum virtual machine。

## 验证习惯

每个阶段至少保留一个小型可执行示例。这个示例应足够快，方便频繁运行。

示例应在 `output/` 下生成产物，例如：

```text
哈密顿量矩阵
能谱表
扫描数据
拟合报告
图
Web 可读取的 JSON
```

## 接口规则

每个阶段都必须暴露清晰接口，后续阶段只能依赖这些接口。

每个接口应记录：

```text
名称
目的
输入类型
输出类型
单位
错误
最小示例
稳定性级别
```

稳定性级别：

```text
stable       后续阶段可以依赖
experimental 只建议当前阶段内部使用
internal     实现细节
```

宁可提供小而稳定的接口，也不要提供宽泛但含糊的接口。

## 正确性规则

每个阶段的正确性必须能从三方面检查：

```text
1. 自动测试
2. 确定性的示例产物
3. 人能读懂且可执行的 verification notebook 或 report
```

验证 notebook/report 应回答：

```text
检查了什么
使用了什么数值约定
使用了什么容差
哪些检查通过
哪些检查失败
还有什么不确定
```

物理模型相关阶段应包含以下 sanity check：

```text
矩阵形状和对称性
厄米性
电容为正
频率范围合理
单位一致
已知极限情况行为
可用时与解析近似比较
```

## 用户检查点

每个阶段结束时，用户都应有明确的检查命令或检查产物。

示例：

```text
python -m sqvm verify-device configs/devices/2q1c2r.yaml
output/stage_01_device_model/verification.ipynb
```

在用户理解并接受检查点之前，不进入下一阶段。

## 参考资料规则

项目设计应参考已有 PDF、旧版本代码和实验经验，但必须明确“参考了什么”和“没有参考什么”。

每个阶段的详细设计文档都应包含：

```text
参考来源
采用的物理或实验原则
没有采用的内容
待确认的问题
```

当前主要 PDF 参考资料记录在：

```text
docs/references/README.md
```
