# 阶段 N 计划：标题

## 阶段目的

说明本阶段为什么存在，以及要解决什么问题。

## 参考来源

列出本阶段参考的 PDF、旧代码或其他资料，并说明采用了哪些原则。

格式：

```text
1. 资料名称：
   用于参考 ...

本阶段暂不采用：
1. ...
```

## 范围

本阶段包含：

```text
item
```

本阶段不包含：

```text
item
```

## 输入

列出必需输入、配置文件、前一阶段产物和用户选择。

## 输出

列出本阶段产生的代码对象、文件、图、数据表和用户可见结果。

## 核心对象

列出主要类、函数、配置区块或数据对象。

## 公开接口

列出本阶段暴露的稳定接口。

每个接口应说明：

```text
name:
purpose:
input:
output:
units:
errors:
example:
stability:
```

## 正确性检查

列出用于证明本阶段结果足够可靠、可以继续往下构建的检查。

检查应包括：

```text
自动测试
数值 sanity check
人可读 verification notebook 或 report
已知参考案例
```

## 用户工作流

说明用户在本阶段如何操作。

## 实现任务

开发过程中使用以下列表：

```text
1. task
2. task
3. task
```

## 测试

必需测试：

```text
test
```

## 输出产物

预期输出位置：

```text
output/stage_N_name/
```

预期文件：

```text
file
verification.ipynb
```

## 验收标准

本阶段完成条件：

```text
1. criterion
2. criterion
3. development log entry is written
```

## 开放问题

列出实现前或实现过程中必须回答的问题。
