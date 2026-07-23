"""Production admission tests for the Stage 4.1 parameterized-control context."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sqvm.control.stage4_1_context import production_parameterized_control_context
from sqvm.control.stage4_1_models import (
    ParameterizedControlError,
    ParameterizedControlReasonCode,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_SNAPSHOT = "configs/control/stage41/source_snapshot_v1.json"
_AUTHORITY_FILES = (
    "configs/control/stage41/production_authority_v1.json",
    "configs/control/stage41/production_approval_v1.json",
    "configs/control/stage41/source_snapshot_v1.json",
    "configs/control/stage41/environment_snapshot_v1.json",
    "configs/control/stage41/publication_policy_v1.json",
    "docs/designs/07_qcis_compiler_design.md",
    "configs/control/2q1c2r_control_smoke.yaml",
    "configs/control/2q1c2r_channels.yaml",
    "configs/devices/2q1c2r.yaml",
)
_PLAN_AUTHORITY = {"qcis_program": "A" * 64}


def _copy_admitted_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    source = json.loads((_REPOSITORY_ROOT / _SOURCE_SNAPSHOT).read_text(encoding="utf-8"))
    paths = set(_AUTHORITY_FILES)
    paths.update(row["path"] for row in source["sources"])
    for relative in paths:
        original = _REPOSITORY_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    return root


def _assert_rejected(root: Path) -> None:
    with pytest.raises(ParameterizedControlError) as caught:
        production_parameterized_control_context(
            _PLAN_AUTHORITY,
            root,
            root / "points" / "stage41",
        )
    assert caught.value.code is ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID


def test_production_context_admits_tracked_authority_chain(tmp_path: Path) -> None:
    root = _copy_admitted_repository(tmp_path)
    output_root = root / "point-staging" / "stage41"

    context = production_parameterized_control_context(
        _PLAN_AUTHORITY,
        root,
        output_root,
    )

    assert context.repository_root == root.resolve()
    assert context.output_root == output_root.resolve()
    assert dict(context.expected_plan_authority_sha256) == _PLAN_AUTHORITY
    assert dict(context.device_flux_limits_phi0) == {
        "q1": (-1.0, 1.0),
        "q2": (-1.0, 1.0),
        "c": (-1.0, 1.0),
    }
    assert context.publication_policy["configured_base_root"] == "output/stage_04_1_parameterized_control"
    assert context.publication_policy["mode"] == "atomic_no_replace"


def test_production_context_binds_runtime_idle_flux_without_changing_electronics(
    tmp_path: Path,
) -> None:
    root = _copy_admitted_repository(tmp_path)
    context = production_parameterized_control_context(
        _PLAN_AUTHORITY,
        root,
        root / "point-staging" / "stage41",
        idle_flux_phi0={"q1": 0.12, "q2": 0.0, "c": 0.27},
    )

    assert {name: float(value) for name, value in context.control_chain_config.idle_flux_phi0.items()} == {
        "q1": 0.12,
        "q2": 0.0,
        "c": 0.27,
    }
    assert "stage4_1_runtime_idle_flux" in context.authority_sha256
    assert context.control_chain_config.static_mixing["z"]["matrix"].tolist() == [
        [0.5, 0.005, 0.01],
        [0.004, 0.5, 0.012],
        [0.008, 0.006, 0.5],
    ]


@pytest.mark.parametrize(
    "relative",
    (
        "configs/control/stage41/production_authority_v1.json",
        "configs/control/stage41/production_approval_v1.json",
        "configs/control/stage41/source_snapshot_v1.json",
        "configs/control/2q1c2r_control_smoke.yaml",
    ),
)
def test_production_context_rejects_bound_byte_drift(tmp_path: Path, relative: str) -> None:
    root = _copy_admitted_repository(tmp_path)
    target = root / relative
    target.write_bytes(target.read_bytes() + b"\n")

    _assert_rejected(root)


def test_production_context_rejects_invalid_plan_authority_hash(tmp_path: Path) -> None:
    root = _copy_admitted_repository(tmp_path)
    with pytest.raises(ParameterizedControlError) as caught:
        production_parameterized_control_context(
            {"qcis_program": "not-a-sha256"},
            root,
            root / "points" / "stage41",
        )
    assert caught.value.code is ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID


def test_production_context_rejects_output_root_outside_repository(tmp_path: Path) -> None:
    root = _copy_admitted_repository(tmp_path)

    with pytest.raises(ParameterizedControlError) as caught:
        production_parameterized_control_context(
            _PLAN_AUTHORITY,
            root,
            tmp_path / "outside" / "stage41",
        )
    assert caught.value.code is ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID
