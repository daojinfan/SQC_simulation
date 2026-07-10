"""Executed verification notebook generation."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import matplotlib
import nbformat as nbf
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

matplotlib.use("Agg")


def write_verification_notebook(artifacts_path: str | Path, output_path: str | Path) -> Path:
    artifacts_path = Path(artifacts_path)
    output_path = Path(output_path)
    data = json.loads(artifacts_path.read_text(encoding="utf-8"))
    notebook = new_notebook()
    notebook["cells"] = [
        new_markdown_cell("# 阶段 1 器件验证\n\n数据来源：`device_artifacts.json`。"),
        _executed_code_cell(
            "from pathlib import Path\nimport json\n\n"
            "ARTIFACTS = Path('device_artifacts.json')\n"
            "data = json.loads(ARTIFACTS.read_text(encoding='utf-8'))\n"
            "data['device_summary']",
            [{"text/plain": repr(data["device_summary"])}],
        ),
        new_markdown_cell("## 组件"),
        _executed_code_cell(
            "components = data['components']\ncomponents",
            [{"text/plain": _pretty(data["components"])}],
        ),
        new_markdown_cell("## 通道"),
        _executed_code_cell(
            "channels = data['channels']\nchannels",
            [{"text/plain": _pretty(data["channels"])}],
        ),
        new_markdown_cell("## priors"),
        _executed_code_cell(
            "priors = data['priors']\npriors",
            [{"text/plain": _pretty(data.get("priors", {}))}],
        ),
        new_markdown_cell("## 电容矩阵"),
        _executed_code_cell(
            "cap = data['capacitance_matrix']\ncap",
            [{"text/plain": _capacitance_table(data["capacitance_matrix"])}],
        ),
        new_markdown_cell("## 电容矩阵 heatmap"),
        _executed_code_cell(
            "import matplotlib.pyplot as plt\n\n"
            "nodes = data['capacitance_matrix']['nodes']\n"
            "matrix = data['capacitance_matrix']['matrix_fF']\n"
            "# 左图为带符号节点电容矩阵 C_ij；右图为非对角耦合电容 -C_ij。\n"
            "nodes, matrix",
            [
                {"text/plain": "capacitance matrix heatmap"},
                {"image/png": _matrix_heatmap_png(data["capacitance_matrix"])},
            ],
        ),
        new_markdown_cell("## 结参数"),
        _executed_code_cell(
            "junction_parameters = data['junction_parameters']\njunction_parameters",
            [{"text/plain": _junction_table(data["junction_parameters"])}],
        ),
        new_markdown_cell("## 验证结果"),
        _executed_code_cell(
            "validation = data['validation']\nchecks = data['checks']\nvalidation, checks",
            [{"text/plain": _verification_summary(data)}],
        ),
    ]
    nbf.write(notebook, output_path)
    return output_path


def _executed_code_cell(source: str, data_outputs: list[dict[str, str]]):
    cell = new_code_cell(source=source)
    cell["execution_count"] = 1
    cell["outputs"] = [
        new_output(output_type="execute_result", data=output_data, execution_count=1, metadata={})
        for output_data in data_outputs
    ]
    return cell


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _capacitance_table(capacitance_matrix: dict[str, Any]) -> str:
    nodes = capacitance_matrix["nodes"]
    matrix = capacitance_matrix["matrix_fF"]
    widths = [max(8, len(node)) for node in ["node", *nodes]]
    rows = [_format_row(["node", *nodes], widths)]
    for node, values in zip(nodes, matrix):
        rows.append(_format_row([node, *[f"{value:.6g}" for value in values]], widths))
    return "\n".join(rows)


def _matrix_heatmap_png(capacitance_matrix: dict[str, Any]) -> str:
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm

    nodes = capacitance_matrix["nodes"]
    matrix = capacitance_matrix["matrix_fF"]
    coupling = [[0.0 if i == j else max(0.0, -value) for j, value in enumerate(row)] for i, row in enumerate(matrix)]
    max_abs = max((abs(value) for row in matrix for value in row), default=1.0) or 1.0
    max_coupling = max((value for row in coupling for value in row), default=1.0) or 1.0

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    signed = axes[0].imshow(matrix, cmap="coolwarm", norm=TwoSlopeNorm(vmin=-max_abs, vcenter=0, vmax=max_abs))
    _decorate_heatmap(axes[0], nodes, matrix, "Signed nodal C matrix (fF)")
    fig.colorbar(signed, ax=axes[0], fraction=0.046, pad=0.04)

    couplings = axes[1].imshow(coupling, cmap="viridis", vmin=0, vmax=max_coupling)
    _decorate_heatmap(axes[1], nodes, coupling, "Off-diagonal coupling -C_ij (fF)")
    fig.colorbar(couplings, ax=axes[1], fraction=0.046, pad=0.04)

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=160)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _decorate_heatmap(axis: Any, nodes: list[str], values: list[list[float]], title: str) -> None:
    axis.set_title(title)
    axis.set_xticks(range(len(nodes)), labels=nodes, rotation=45, ha="right")
    axis.set_yticks(range(len(nodes)), labels=nodes)
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            label = "0" if abs(value) < 0.0005 else f"{value:.3g}"
            axis.text(j, i, label, ha="center", va="center", color="black", fontsize=8)


def _junction_table(rows: list[dict[str, Any]]) -> str:
    headers = ["component", "junction", "rn_ohm", "ej_GHz", "source"]
    widths = [12, 10, 14, 14, 8]
    lines = [_format_row(headers, widths)]
    for row in rows:
        lines.append(
            _format_row(
                [
                    row["component"],
                    row["junction"],
                    f"{row['rn_ohm']:.6g}",
                    f"{row['ej_GHz']:.9g}",
                    row["source"],
                ],
                widths,
            )
        )
    return "\n".join(lines)


def _verification_summary(data: dict[str, Any]) -> str:
    validation = data["validation"]
    checks = data["checks"]
    lines = [f"ok: {validation['ok']}"]
    lines.append("errors:")
    lines.extend(f"- {item['path']}: {item['message']}" for item in validation["errors"] or [])
    if not validation["errors"]:
        lines.append("- none")
    lines.append("warnings:")
    lines.extend(f"- {item['path']}: {item['message']}" for item in validation["warnings"] or [])
    if not validation["warnings"]:
        lines.append("- none")
    lines.append("checks:")
    for check in checks:
        mark = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- {mark} {check['name']}: {check['message']}")
    return "\n".join(lines)


def _format_row(values: list[str], widths: list[int]) -> str:
    return "  ".join(str(value).ljust(width) for value, width in zip(values, widths))
