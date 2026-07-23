# Stage 7.1.7 统一 Web 绘图协议

## 1. 目标

校准实验通过统一 `plot_spec` 向 Web 提供可视化数据。Web 不按实验类型解析原始工件，
也不为 spectroscopy、Rabi、Ramsey 等实验分别实现绘图器。新增实验只需要生成合法 spec。

首版协议满足：

- 任意对象集合，例如 `Q1`、`Q2`、`C`；
- 任意可选指标，例如 `P0`、`P1`、`leakage`；
- `line`、`scatter` 和 `heatmap`；
- 数据点或 heatmap cell 选择与坐标读数；
- 同一实验中按对象、指标和扫描阶段筛选；
- 原始实验数据、分析结果和证据 PNG 保持独立且不可变。

## 2. API 边界

实验详情接口返回：

```http
GET /api/v1/experiments/{run_id}
```

```json
{
  "datasets": {},
  "plot_specs": [],
  "plot_url": "/api/v1/experiments/{run_id}/asset/spectroscopy.png"
}
```

`datasets` 是完整实验数据，`plot_specs` 是只面向显示的标准化投影。Web 不能根据显示结果
回写实验数据。已发布 PNG 继续作为可审计证据，Canvas 是交互视图而非新 authority。

## 3. 通用结构

```json
{
  "schema_version": "1.0",
  "plot_id": "qubit_spectroscopy",
  "plot_type": "line",
  "title": "比特频谱",
  "objects": [
    {"id": "Q1", "label": "Q1", "default_visible": true}
  ],
  "metrics": [
    {"id": "P1", "label": "P1", "unit": "", "default_visible": true}
  ],
  "groups": [
    {"id": "coarse", "label": "粗扫"}
  ],
  "axes": {
    "x": {"label": "驱动频率", "unit": "GHz"},
    "y": {"label": "布居 / 泄漏", "unit": "", "zero_baseline": true}
  },
  "series": [
    {
      "id": "coarse:Q1:P1",
      "object_id": "Q1",
      "metric_id": "P1",
      "group_id": "coarse",
      "points": [
        {"id": "coarse:Q1:0:P1", "x": 5.1, "y": 0.2, "metadata": {}}
      ]
    }
  ]
}
```

`objects` 和 `metrics` 决定 Web 多选控件。`series` 必须引用已声明的对象和指标；
每个 point 具有稳定 ID、有限的 x/y 及可选 metadata。`line` 与 `scatter` 共用该结构。

## 4. Heatmap

Heatmap 使用相同的对象、指标和坐标轴定义，只把 `series` 替换为 `layers`：

```json
{
  "plot_type": "heatmap",
  "layers": [
    {
      "id": "Q1:P1",
      "object_id": "Q1",
      "metric_id": "P1",
      "cells": [
        {"id": "p0", "x": 0.01, "y": 10.0, "value": 0.2},
        {"id": "p1", "x": 0.02, "y": 10.0, "value": 0.4}
      ]
    }
  ]
}
```

每个 cell 的 x/y/value 必须有限。多个已选 layer 使用 small multiples，不进行不透明叠加。
点击 cell 后显示 x、y 和 value。

## 5. 当前频谱映射

当前频谱声明对象 `Q1`、`Q2`，指标 `P0`、`P1`、`leakage`，分组为粗扫、细扫和确认扫描。
默认显示两个对象的 `P1`。

`P0`、`P1` 从 dressed computational populations 明确求和：

```text
Q1.P0 = population_000 + population_001
Q1.P1 = population_100 + population_101
Q2.P0 = population_000 + population_100
Q2.P1 = population_001 + population_101
```

不使用 `1-P1` 推导 P0，因为 leakage 需要保持独立语义。当前实验没有 C 的观测数据，因此不显示 C；
后续实验在 `objects` 和 `series/layers` 中提供 C 后，Web 自动显示选择项。

## 6. 校验与交互

- schema 版本、plot 类型、对象和指标引用必须有效；
- x/y/value 拒绝 NaN 和无穷值；
- 对象和指标可独立多选，允许暂时无选择并显示空状态；
- 鼠标点击选择最近数据点，heatmap 点击选择所在 cell；
- 鼠标在数据点或 heatmap cell 上连续停留 300 ms 后显示悬浮坐标，移出目标立即隐藏；
- Canvas 获得焦点后使用左右方向键遍历当前可见点；
- 选中信息显示对象、指标、扫描分组以及横纵坐标；
- resize 后按当前筛选与选中状态重绘；
- 后端验证是协议边界，前端不得静默修复无效 spec。
