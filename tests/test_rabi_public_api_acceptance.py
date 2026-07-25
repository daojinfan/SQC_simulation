from __future__ import annotations

import inspect
from pathlib import Path

import pytest as _pytest

pytestmark = _pytest.mark.integration


ROOT = Path(__file__).resolve().parents[1]


def _rabi_api():
    # This is a deliberate black-box import: the baseline lacks this public API.
    from sqvm.calibration import cancel_rabi, run_rabi

    return cancel_rabi, run_rabi


def test_public_api_has_the_frozen_rabi_signature_and_root_lazy_exports() -> None:
    cancel_rabi, run_rabi = _rabi_api()
    assert tuple(inspect.signature(run_rabi).parameters) == (
        "target",
        "amplitude_range_GHz",
        "amplitude_step_GHz",
        "device_id",
        "output_root",
        "configuration_storage_root",
        "repository_root",
        "timeout_s",
        "batch_deadline_s",
        "operation_id",
        "cancellation_token",
        "progress_callback",
    )
    assert tuple(inspect.signature(cancel_rabi).parameters)[:1] == ("operation_id",)

    import sqvm

    assert sqvm.run_rabi is run_rabi
    assert sqvm.cancel_rabi is cancel_rabi


def test_rabi_notebook_uses_the_frozen_simple_public_workflow() -> None:
    import nbformat

    path = ROOT / "user" / "02_x2p_rabi_calibration.ipynb"
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    source = "\n".join(
        cell.source for cell in notebook.cells if cell.cell_type == "code"
    )
    assert "from sqvm.calibration import" in source
    assert "run_rabi" in source
    assert "apply_calibration_candidates_to_current_configuration" in source
    assert "operation_id=OPERATION_ID" in source
    assert "APPLY CALIBRATION CANDIDATES" in source
    assert "sys.path" not in source
    assert "SRC_ROOT" not in source
