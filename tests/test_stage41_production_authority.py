"""Production admission tests for the Stage 4.1 parameterized-control context."""

from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

import json
import shutil
from pathlib import Path

import pytest
import yaml

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


def test_production_context_uses_every_active_control_section(tmp_path: Path) -> None:
    root = _copy_admitted_repository(tmp_path)
    source = yaml.safe_load((root / "configs/control/2q1c2r_control_smoke.yaml").read_text("utf-8"))
    control = {
        name: source[name]
        for name in (
            "clock",
            "dac",
            "lane_order",
            "lanes",
            "static_mixing",
            "idle_flux_phi0",
            "acceptance",
        )
    }
    control["clock"] = {"sample_rate_Hz": 1_000_000_000, "dt_ns": 1.0}
    control["dac"] = {
        "bits": 18,
        "full_scale_min_V": -3.0,
        "full_scale_max_exclusive_V": 3.0,
        "rounding": "half_even",
    }
    control["lanes"]["q1_xy_i"] = {"latency_samples": 7, "fir": [1.0]}
    control["static_mixing"]["xy"]["matrix"][0][0] = 0.2
    control["static_mixing"]["readout"]["matrix"][0][0] = 0.002
    control["idle_flux_phi0"]["q1"] = 0.12
    control["acceptance"]["max_formal_samples_per_scenario"] = 2_000
    control["acceptance"]["max_xy_area_relative_error"] = 0.01

    context = production_parameterized_control_context(
        _PLAN_AUTHORITY,
        root,
        root / "point-staging" / "stage41",
        idle_flux_phi0=control["idle_flux_phi0"],
        control_values=control,
    )
    resolved = context.control_chain_config
    assert resolved.sample_rate_Hz == 1_000_000_000
    assert float(resolved.dt_ns) == 1.0
    assert resolved.dac["bits"] == 18
    assert float(resolved.dac["full_scale_max_exclusive_V"]) == 3.0
    assert resolved.lanes["q1_xy_i"] == {"latency_samples": 7, "fir": (1.0,)}
    assert resolved.static_mixing["xy"]["matrix"][0, 0] == pytest.approx(0.2)
    assert resolved.static_mixing["readout"]["matrix"][0, 0] == pytest.approx(0.002)
    assert float(resolved.idle_flux_phi0["q1"]) == pytest.approx(0.12)
    assert resolved.acceptance["max_formal_samples_per_scenario"] == 2_000
    assert resolved.acceptance["max_xy_area_relative_error"] == pytest.approx(0.01)
    assert "stage4_1_runtime_control" in context.authority_sha256
    assert context.runtime_control_values is not None


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
