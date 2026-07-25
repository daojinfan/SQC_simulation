from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest as _pytest

pytestmark = _pytest.mark.physics_slow


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FIXTURE = ROOT / "tests" / "fixtures" / "platform_configuration_reference_v1" / "platform-configurations"


def _run_rabi():
    # This slow gate must use production QuTiP rather than a synthetic runner.
    from sqvm.calibration import run_rabi

    return run_rabi


def test_real_qutip_rabi_publication_is_phase_audited_and_idempotent() -> None:
    run_rabi = _run_rabi()
    operation_id = "8dc263a6-88a4-4f8c-b1b0-567711c3c9f7"
    # Keep the fixture portable inside deep Codex/Git worktree roots while the
    # product's Windows evidence-path preflight remains strict.
    isolated_root = ROOT / "tmp" / f"ra_{uuid.uuid4().hex[:8]}"
    storage = isolated_root / "platform-configurations"
    collection = isolated_root / "experiments"
    try:
        shutil.copytree(CONFIG_FIXTURE, storage)
        run = run_rabi(
            target="Q1",
            amplitude_range_GHz=(0.0, 0.03),
            amplitude_step_GHz=0.0075,
            output_root=collection,
            configuration_storage_root=storage,
            repository_root=ROOT,
            operation_id=operation_id,
            timeout_s=120.0,
            batch_deadline_s=600.0,
        )
        root = Path(run.root)
        assert root.name == f"qubit_rabi_{operation_id.replace('-', '')}"
        assert {path.name for path in root.iterdir()} >= {
            "workflow.json",
            "dataset.json",
            "manifest.json",
            "verification_report.json",
            "receipt.json",
            "execution",
        }
        workflow = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
        dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
        assert workflow["workflow_id"] == "qubit_rabi_x2p_amplitude_scan_v1"
        assert dataset["axis"]["values"] == [0.0, 0.0075, 0.015, 0.0225, 0.03]
        assert list(dataset["series"]["Q1"]) == ["P0", "P1", "leakage", "norm_error"]
        for index, point in enumerate(dataset["points"]):
            assert point["circuit_id"] == f"rabi_q1_{index:04d}"
            audit = point["phase_audit"]
            assert audit["second_start_sample"] == audit["first_start_sample"] + audit["length_samples"]
        batch_request = json.loads(
            (root / "execution" / "batch" / "request.json").read_text(encoding="utf-8")
        )
        for index, point in enumerate(batch_request["circuits"]):
            lines = point["qcis_source"].splitlines()
            assert lines[1:] == ["X2P Q1", "X2P Q1"]
            tokens = lines[0].split()
            assert tokens[:3] == ["SET", "Q1", "setting.active_xy2_setting.amplitude_GHz"]
            assert float(tokens[3]) == dataset["axis"]["values"][index]
            assert "PLSXY" not in point["qcis_source"]

        replay = run_rabi(
            target="Q1",
            amplitude_range_GHz=(0.0, 0.03),
            amplitude_step_GHz=0.0075,
            output_root=collection,
            configuration_storage_root=storage,
            repository_root=ROOT,
            operation_id=operation_id,
            timeout_s=120.0,
            batch_deadline_s=600.0,
        )
        assert Path(replay.root) == root
    finally:
        shutil.rmtree(isolated_root, ignore_errors=True)
