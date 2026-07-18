from __future__ import annotations

from pathlib import Path

import nbformat


ROOT = Path(__file__).resolve().parents[1]


def test_qubit_spectroscopy_notebook_uses_calibration_api_and_is_safe_by_default():
    path = ROOT / "user" / "01_qubit_spectroscopy.ipynb"
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)

    source = "\n".join(
        cell.source for cell in notebook.cells if cell.cell_type == "code"
    )
    assert "from sqvm.calibration import" in source
    assert "run_active_qubit_spectroscopy_calibration" in source
    assert "RUN_EXPERIMENT = False" in source
