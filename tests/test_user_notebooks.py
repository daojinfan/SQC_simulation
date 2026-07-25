from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from pathlib import Path

import nbformat
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_qubit_spectroscopy_notebook_uses_the_simple_public_api():
    path = ROOT / "user" / "01_qubit_spectroscopy.ipynb"
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)

    source = "\n".join(
        cell.source for cell in notebook.cells if cell.cell_type == "code"
    )
    assert "from sqvm.calibration import" in source
    assert "run_spectroscopy" in source
    assert "sys.path" not in source
    assert "SRC_ROOT" not in source
    assert "RUN_EXPERIMENT =" in source
    assert "operation_id=OPERATION_ID" in source
    assert "apply_calibration_candidates_to_current_configuration" in source
    assert "UPDATE_PARAMETERS = False" in source
    assert "APPLY CALIBRATION CANDIDATES" in source
    assert "SpectroscopyCalibrationRequest" not in source
    assert "SpectroscopyCalibrationPolicy" not in source
    assert "SpectroscopyAxis" not in source
    assert "timeout_s=" not in source

    namespace: dict[str, object] = {"__name__": "__notebook_validation__"}
    for index, cell in enumerate(notebook.cells):
        if cell.cell_type != "code":
            continue
        source_without_execution = cell.source.replace(
            "RUN_EXPERIMENT = True", "RUN_EXPERIMENT = False"
        )
        exec(
            compile(source_without_execution, f"{path}:cell-{index}", "exec"),
            namespace,
        )

    assert namespace["FREQUENCY_RANGES_GHZ"] == {
        "Q1": (5.00, 5.40),
        "Q2": (5.10, 5.50),
    }
    step = namespace["FREQUENCY_STEP_GHZ"]
    assert isinstance(namespace["OPERATION_ID"], str)
    assert isinstance(step, float) and step > 0.0
    for start, stop in namespace["FREQUENCY_RANGES_GHZ"].values():
        intervals = round((stop - start) / step)
        assert intervals >= 2
        assert start + intervals * step == pytest.approx(stop)


def test_x2p_rabi_notebook_uses_the_public_four_step_api(monkeypatch):
    path = ROOT / "user" / "02_x2p_rabi_calibration.ipynb"
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)

    source = "\n".join(
        cell.source for cell in notebook.cells if cell.cell_type == "code"
    )
    assert "from sqvm.calibration import" in source
    assert "run_rabi" in source
    assert "sys.path" not in source
    assert "TARGET = 'Q1'" in source
    assert "AMPLITUDE_RANGE_GHZ = (0.0, 0.2)" in source
    assert "AMPLITUDE_STEP_GHZ = 0.005" in source
    assert "operation_id=OPERATION_ID" in source
    assert "candidate_ids=[candidate_id]" in source
    assert "APPLY CALIBRATION CANDIDATES" in source

    import sqvm.calibration as calibration

    # The smoke path deliberately disables execution; core Rabi supplies this export.
    monkeypatch.setattr(calibration, "run_rabi", lambda **_kwargs: None, raising=False)
    namespace: dict[str, object] = {"__name__": "__notebook_validation__"}
    for index, cell in enumerate(notebook.cells):
        if cell.cell_type != "code":
            continue
        source_without_execution = cell.source.replace(
            "RUN_EXPERIMENT = True", "RUN_EXPERIMENT = False"
        )
        exec(
            compile(source_without_execution, f"{path}:cell-{index}", "exec"),
            namespace,
        )

    assert namespace["TARGET"] == "Q1"
    assert namespace["AMPLITUDE_RANGE_GHZ"] == (0.0, 0.2)
    assert namespace["AMPLITUDE_STEP_GHZ"] == 0.005
    assert isinstance(namespace["OPERATION_ID"], str)
