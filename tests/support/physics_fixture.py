"""Verified paths for the immutable, non-production physics fixture."""

from __future__ import annotations

from pathlib import Path

from tests.support.fixture_loader import fixture_path


def physics_fixture_root() -> Path:
    return fixture_path("physics_baseline_v1")


def physics_hamiltonian_config() -> Path:
    return physics_fixture_root() / "configs/hamiltonian.yaml"


def physics_smoke_config() -> Path:
    return physics_fixture_root() / "configs/spectrum_smoke.yaml"


def physics_acceptance_config() -> Path:
    return physics_fixture_root() / "configs/spectrum_acceptance.yaml"
