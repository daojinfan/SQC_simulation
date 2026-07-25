from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import nbformat
import pytest

from sqvm.calibration import RabiRun
from sqvm.calibration.rabi import RabiAnalysis, RabiDataset
from sqvm.runtime.batch import CircuitBatchHandle


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


def _rabi_notebook_cells() -> dict[str, str]:
    path = ROOT / "user" / "02_x2p_rabi_calibration.ipynb"
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    return {
        cell.id: cell.source
        for cell in notebook.cells
        if cell.cell_type == "code"
    }


def _rabi_run(*, eligible: bool, target: str = "Q1") -> RabiRun:
    batch = CircuitBatchHandle(
        "00000000-0000-4000-8000-000000000000", ROOT / "output", "A" * 64,
        "B" * 64, "completed", 1, 0, (), {},
    )
    dataset = RabiDataset(
        target, (0.0, 0.05, 0.1), (1.0, 0.75, 0.1), (0.0, 0.25, 0.9),
        (0.0, 0.0, 0.01), (0.0, 0.0, 0.0), (), "C" * 64, batch,
    )
    analysis = RabiAnalysis(
        True, 0.0, 0.9, 0.05, 0.01, 0.02, 0.99, 1, (0.0, 0.1),
        (0.0, 0.3, 0.85), None,
    )
    candidate = {
        "candidate_id": "Q1.xy2_amplitude",
        "recommendation_eligible": eligible,
        "reason": None if eligible else "analysis policy has not been approved",
    }
    return RabiRun(
        ROOT / "output" / "rabi-run", "00000000-0000-4000-8000-000000000000",
        "recommendation", dataset, analysis, eligible, {target: candidate}, "D" * 64,
        "E" * 64,
    )


def test_x2p_rabi_notebook_uses_the_current_public_api_and_real_run_shape():
    cells = _rabi_notebook_cells()
    path = ROOT / "user" / "02_x2p_rabi_calibration.ipynb"

    source = "\n".join(
        cells.values()
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
    assert 'result.data["amplitude_GHz"]' not in source
    assert "result.data.get(TARGET)" in source
    assert "candidate.get('recommendation_eligible') is not True" in source

    for working_directory in (ROOT, ROOT / "user"):
        completed = subprocess.run(
            [sys.executable, "-c", cells["rabi-imports"]],
            cwd=working_directory,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    namespace: dict[str, object] = {"__name__": "__notebook_validation__"}
    exec(compile(cells["rabi-imports"], f"{path}:imports", "exec"), namespace)
    exec(compile(cells["rabi-parameters"], f"{path}:parameters", "exec"), namespace)
    run = _rabi_run(eligible=True)
    namespace["run_rabi"] = lambda **_kwargs: run
    exec(compile(cells["run-rabi"], f"{path}:run", "exec"), namespace)

    assert namespace["TARGET"] == "Q1"
    assert namespace["AMPLITUDE_RANGE_GHZ"] == (0.0, 0.2)
    assert namespace["AMPLITUDE_STEP_GHZ"] == 0.005
    assert isinstance(namespace["OPERATION_ID"], str)
    assert namespace["result"] is run

    calls = []
    namespace["UPDATE_CANDIDATE"] = True
    namespace["apply_calibration_candidates_to_current_configuration"] = (
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(
            device_id="demo_2q1c2r", current_revision=2,
            applied_values={"calibration_values.waveform_registry.settings.q1_xy2.amplitude_GHz": 0.05},
        )
    )
    exec(compile(cells["apply-rabi-candidate"], f"{path}:apply", "exec"), namespace)
    assert calls[0][1]["candidate_ids"] == ["Q1.xy2_amplitude"]

    namespace["result"] = _rabi_run(eligible=False)
    with pytest.raises(RuntimeError, match="analysis policy has not been approved"):
        exec(compile(cells["apply-rabi-candidate"], f"{path}:ineligible", "exec"), namespace)
    assert len(calls) == 1

    namespace["result"] = _rabi_run(eligible=True, target="Q2")
    with pytest.raises(RuntimeError, match="未生成校准候选"):
        exec(compile(cells["apply-rabi-candidate"], f"{path}:missing", "exec"), namespace)
    assert len(calls) == 1


def test_setup_script_checks_both_public_calibration_imports():
    source = (ROOT / "user" / "setup_environment.cmd").read_text("utf-8")
    assert source.count("from sqvm.calibration import run_spectroscopy, run_rabi") == 3
