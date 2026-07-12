"""Executed-look Stage 3 notebook generated from the result artifact only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nbformat as nbf


def write_spectrum_verification_notebook(artifact_path: str | Path, output_path: str | Path) -> Path:
    source = Path(artifact_path)
    target = Path(output_path)
    data = json.loads(source.read_text(encoding="utf-8"))
    cells = [
        nbf.v4.new_markdown_cell(
            "# Stage 3 static spectrum verification\n\n"
            "Data source: `static_spectrum_artifacts.json`. The notebook does not recompute the stage gate."
        ),
        _executed_cell(
            "from pathlib import Path\nimport json\n"
            "ARTIFACT = Path('static_spectrum_artifacts.json')\n"
            "data = json.loads(ARTIFACT.read_text(encoding='utf-8'))\n"
            "data['provenance']",
            data["provenance"],
        ),
        nbf.v4.new_markdown_cell("## Hamiltonian and eigensystem"),
        _executed_cell(
            "{'hamiltonian_summary': data['hamiltonian_summary'], 'gaps': data['eigenvalue_gaps_GHz']}",
            {"hamiltonian_summary": data["hamiltonian_summary"], "gaps": data["eigenvalue_gaps_GHz"]},
        ),
        nbf.v4.new_markdown_cell("## Dressed assignments and overlap"),
        _executed_cell(
            "data['dressed_state_assignments'], data['overlap_summary']",
            {
                "dressed_state_assignments": data["dressed_state_assignments"],
                "overlap_summary": data["overlap_summary"],
            },
        ),
        nbf.v4.new_markdown_cell("## Mode participation and static metrics"),
        _executed_cell(
            "data['mode_participation'], data['transition_frequencies_GHz'], data['anharmonicities_GHz'], data['zz_metrics_GHz']",
            {
                "mode_participation": data["mode_participation"],
                "transition_frequencies_GHz": data["transition_frequencies_GHz"],
                "anharmonicities_GHz": data["anharmonicities_GHz"],
                "zz_metrics_GHz": data["zz_metrics_GHz"],
            },
        ),
        nbf.v4.new_markdown_cell("## Convergence, flux scan, and crossings"),
        _executed_cell(
            "data['numerical_convergence'], data['flux_scan'], data['crossing_convergence']",
            {
                "numerical_convergence": data["numerical_convergence"],
                "flux_scan": data["flux_scan"],
                "crossing_convergence": data["crossing_convergence"],
            },
        ),
        nbf.v4.new_markdown_cell("## Runtime and stage gate"),
        _executed_cell(
            "data['runtime'], data['stage_gate'], data['warnings'], data['checks']",
            {
                "runtime": data["runtime"],
                "stage_gate": data["stage_gate"],
                "warnings": data["warnings"],
                "checks": data["checks"],
            },
        ),
    ]
    notebook = nbf.v4.new_notebook(cells=cells)
    nbf.write(notebook, target)
    return target


def _executed_cell(source: str, value: Any):
    cell = nbf.v4.new_code_cell(source)
    cell.execution_count = 1
    cell.outputs = [
        nbf.v4.new_output(
            output_type="execute_result",
            execution_count=1,
            data={"text/plain": json.dumps(value, ensure_ascii=False, indent=2)},
            metadata={},
        )
    ]
    return cell
