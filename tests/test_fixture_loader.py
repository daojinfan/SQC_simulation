from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.contract

import os
from pathlib import Path

import pytest

from tests.support.fixture_loader import copy_fixture, fixture_path, verify_fixture_manifest


def test_fixture_copy_is_writable_only_outside_the_golden_tree(tmp_path: Path) -> None:
    source = fixture_path("device_model_v1")
    target = copy_fixture("device_model_v1", tmp_path / "fixture")
    copied = target / "output/stage_01_device_model/device_artifacts.json"
    original = source / "output/stage_01_device_model/device_artifacts.json"
    copied.write_bytes(copied.read_bytes() + b"tamper")
    assert copied.read_bytes() != original.read_bytes()
    verify_fixture_manifest(source)
    with pytest.raises(ValueError, match="bytes do not match"):
        verify_fixture_manifest(target)


def test_fixture_manifest_rejects_unlisted_file(tmp_path: Path) -> None:
    target = copy_fixture("platform_configuration_reference_v1", tmp_path / "fixture")
    (target / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or unlisted"):
        verify_fixture_manifest(target)


def test_fixture_loader_refuses_existing_destination(tmp_path: Path) -> None:
    target = tmp_path / "fixture"
    target.mkdir()
    with pytest.raises(FileExistsError):
        copy_fixture("device_model_v1", target)


def test_fixture_manifest_rejects_hardlinked_payload(tmp_path: Path) -> None:
    target = copy_fixture("device_model_v1", tmp_path / "fixture")
    payload = target / "output/stage_01_device_model/device_artifacts.json"
    external = tmp_path / "external.json"
    external.write_bytes(payload.read_bytes())
    payload.unlink()
    os.link(external, payload)

    with pytest.raises(ValueError, match="is linked"):
        verify_fixture_manifest(target)
