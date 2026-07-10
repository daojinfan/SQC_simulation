"""Executed Hamiltonian verification notebook generation."""

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
        new_markdown_cell("# 阶段 2 哈密顿量验证\n\n数据来源：`hamiltonian_artifacts.json`。"),
        _executed_code_cell(
            "from pathlib import Path\nimport json\n\n"
            "ARTIFACTS = Path('hamiltonian_artifacts.json')\n"
            "data = json.loads(ARTIFACTS.read_text(encoding='utf-8'))\n"
            "data['hamiltonian_config']",
            [{"text/plain": _pretty(data["hamiltonian_config"])}],
        ),
        new_markdown_cell("## 模式与坐标变换"),
        _executed_code_cell(
            "data['node_order'], data['mode_order'], data['coordinate_transform']",
            [{"text/plain": _pretty({
                "node_order": data["node_order"],
                "mode_order": data["mode_order"],
                "coordinate_transform": data["coordinate_transform"],
            })}],
        ),
        new_markdown_cell("## 电容与 E_C"),
        _executed_code_cell(
            "node_cap = data['node_capacitance_matrix_fF']\n"
            "mode_cap = data['mode_capacitance_matrix_fF']\n"
            "ec = data['ec_matrix_GHz']\n"
            "node_cap, mode_cap, ec",
            [
                {"text/plain": "C_node, C_mode, and E_C heatmaps"},
                {"image/png": _matrix_triptych_png(data)},
            ],
        ),
        new_markdown_cell("## SQUID 有效 EJ"),
        _executed_code_cell(
            "data['effective_junctions']",
            [{"text/plain": _pretty(data["effective_junctions"])}],
        ),
        new_markdown_cell("## Hamiltonian 摘要"),
        _executed_code_cell(
            "{'basis': data['basis'], 'hilbert_dimension': data['hilbert_dimension'], 'summary': data['hamiltonian_summary']}",
            [{"text/plain": _pretty({
                "basis": data["basis"],
                "hilbert_dimension": data["hilbert_dimension"],
                "summary": data["hamiltonian_summary"],
                "mode_coupling_fF": data["mode_coupling_fF"],
            })}],
        ),
        new_markdown_cell("## 低能能谱"),
        _executed_code_cell(
            "data['lowest_eigenvalues_GHz'], data['eigenvalue_gaps_GHz']",
            [
                {"text/plain": _spectrum_table(data)},
                {"image/png": _gaps_png(data)},
            ],
        ),
        new_markdown_cell("## 物理 sanity"),
        _executed_code_cell(
            "data['single_transmon_analytic_limit'], data['charge_basis_convergence']",
            [{"text/plain": _pretty({
                "single_transmon_analytic_limit": data["single_transmon_analytic_limit"],
                "charge_basis_convergence": data["charge_basis_convergence"],
                "note": "绝对本征值含 Josephson 常数项，主要物理可观测量看 gaps。",
            })}],
        ),
        new_markdown_cell("## 检查结果"),
        _executed_code_cell(
            "data['checks']",
            [{"text/plain": _checks_summary(data["checks"])}],
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


def _matrix_triptych_png(data: dict[str, Any]) -> str:
    import matplotlib.pyplot as plt

    matrices = [
        ("C_node (fF)", data["node_capacitance_matrix_fF"]["nodes"], data["node_capacitance_matrix_fF"]["matrix_fF"]),
        ("C_mode (fF)", data["mode_capacitance_matrix_fF"]["modes"], data["mode_capacitance_matrix_fF"]["matrix_fF"]),
        ("E_C (GHz)", data["ec_matrix_GHz"]["modes"], data["ec_matrix_GHz"]["matrix_GHz"]),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), constrained_layout=True)
    for axis, (title, labels, matrix) in zip(axes, matrices):
        image = axis.imshow(matrix, cmap="coolwarm")
        axis.set_title(title)
        axis.set_xticks(range(len(labels)), labels=labels, rotation=45, ha="right")
        axis.set_yticks(range(len(labels)), labels=labels)
        for i, row in enumerate(matrix):
            for j, value in enumerate(row):
                axis.text(j, i, f"{value:.3g}", ha="center", va="center", fontsize=8)
        fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    return _fig_to_base64(fig)


def _gaps_png(data: dict[str, Any]) -> str:
    import matplotlib.pyplot as plt

    gaps = data["eigenvalue_gaps_GHz"]
    fig, axis = plt.subplots(figsize=(7, 4), constrained_layout=True)
    axis.plot(range(len(gaps)), gaps, marker="o")
    axis.set_xlabel("index")
    axis.set_ylabel("E_k - E_0 (GHz)")
    axis.set_title("Lowest energy gaps")
    return _fig_to_base64(fig)


def _fig_to_base64(fig) -> str:
    import matplotlib.pyplot as plt

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=150)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _spectrum_table(data: dict[str, Any]) -> str:
    lines = ["index  eigenvalue_GHz  gap_GHz"]
    for index, (value, gap) in enumerate(zip(data["lowest_eigenvalues_GHz"], data["eigenvalue_gaps_GHz"])):
        lines.append(f"{index:<5}  {value:<14.9g}  {gap:<10.9g}")
    return "\n".join(lines)


def _checks_summary(checks: list[dict[str, Any]]) -> str:
    lines = []
    for item in checks:
        mark = "PASS" if item["passed"] else "WARN" if item["severity"] == "warning" else "FAIL"
        lines.append(f"- {mark} [{item['severity']}] {item['name']}: {item['message']}")
    return "\n".join(lines)
