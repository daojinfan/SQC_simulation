import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

import json
import subprocess
import sys
from pathlib import Path

import nbformat

from sqvm.hamiltonian import verify_hamiltonian


CONFIG = Path("configs/hamiltonians/2q1c_charge_basis.yaml")


def test_verify_hamiltonian_writes_artifacts(tmp_path):
    report = verify_hamiltonian(CONFIG, tmp_path)
    assert report.ok
    assert (tmp_path / "hamiltonian_artifacts.json").exists()
    assert (tmp_path / "verification.ipynb").exists()


def test_hamiltonian_artifacts_contains_stage2_fields(tmp_path):
    verify_hamiltonian(CONFIG, tmp_path)
    data = json.loads((tmp_path / "hamiltonian_artifacts.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "0.2"
    assert data["artifact_type"] == "stage_02_hamiltonian"
    assert data["artifact_version"] == "0.2"
    assert set(data["provenance"]) == {
        "device_artifacts_sha256",
        "hamiltonian_config_sha256",
        "stage2_model_source_tree_sha256",
    }
    assert data["hamiltonian_summary"]["solver_method"] == "eigh"
    assert data["node_capacitance_matrix_fF"]
    assert data["offset_charge_ng"] == {"q1": 0.0, "c": 0.0, "q2": 0.0}
    assert any(item["severity"] == "warning" for item in data["checks"])


def test_hamiltonian_artifact_serialization_is_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    verify_hamiltonian(CONFIG, first)
    verify_hamiltonian(CONFIG, second)
    assert (first / "hamiltonian_artifacts.json").read_bytes() == (
        second / "hamiltonian_artifacts.json"
    ).read_bytes()


def test_verification_notebook_reads_hamiltonian_artifacts(tmp_path):
    verify_hamiltonian(CONFIG, tmp_path)
    notebook = nbformat.read(tmp_path / "verification.ipynb", as_version=4)
    sources = "\n".join(cell.source for cell in notebook.cells)
    assert "hamiltonian_artifacts.json" in sources
    assert any(
        "image/png" in output.data
        for cell in notebook.cells
        for output in getattr(cell, "outputs", [])
        if hasattr(output, "data")
    )


def test_cli_verify_hamiltonian_success(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sqvm",
            "verify-hamiltonian",
            str(CONFIG),
            "--output",
            str(tmp_path),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "hamiltonian_artifacts.json").exists()


def test_vscode_runner_smoke():
    result = subprocess.run(
        [sys.executable, "scripts/run_stage_02_hamiltonian.py"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
