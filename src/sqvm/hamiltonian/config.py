"""Hamiltonian configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


SUPPORTED_SCHEMA_VERSION = "0.1"
SUPPORTED_MODES = ("q1", "c", "q2")


@dataclass(frozen=True, slots=True)
class BasisConfig:
    charge_cutoffs: dict[str, int]


@dataclass(frozen=True, slots=True)
class SolverConfig:
    num_eigenvalues: int
    method: str = "eigh"
    tolerance: float | None = None


@dataclass(frozen=True, slots=True)
class HamiltonianConfig:
    schema_version: str
    name: str
    source_device_artifacts: Path
    basis: BasisConfig
    solver: SolverConfig
    source_path: Path | None = None


def load_hamiltonian_config(path: str | Path) -> HamiltonianConfig:
    source_path = Path(path)
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{source_path}: invalid YAML: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{source_path}: cannot read Hamiltonian config: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ValueError("Hamiltonian config root must be a mapping")
    schema_version = _required_str(raw, "schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError("schema_version is unsupported")
    ham = _required_mapping(raw, "hamiltonian")
    name = _required_str(ham, "name", display="hamiltonian.name")
    source_device_artifacts = Path(
        _required_str(ham, "source_device_artifacts", display="hamiltonian.source_device_artifacts")
    )
    basis = _parse_basis(_required_mapping(ham, "basis", context="hamiltonian"))
    solver = _parse_solver(ham.get("solver", {}))
    return HamiltonianConfig(
        schema_version=schema_version,
        name=name,
        source_device_artifacts=source_device_artifacts,
        basis=basis,
        solver=solver,
        source_path=source_path,
    )


def _parse_basis(raw: Mapping[str, Any]) -> BasisConfig:
    modes = set(raw)
    expected = set(SUPPORTED_MODES)
    if modes != expected:
        raise ValueError(f"hamiltonian.basis must contain exactly {', '.join(SUPPORTED_MODES)}")
    cutoffs: dict[str, int] = {}
    for mode in SUPPORTED_MODES:
        mapping = _required_mapping(raw, mode, context="hamiltonian.basis")
        value = mapping.get("charge_cutoff")
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"hamiltonian.basis.{mode}.charge_cutoff must be a positive integer")
        cutoffs[mode] = value
    return BasisConfig(charge_cutoffs=cutoffs)


def _parse_solver(raw: Any) -> SolverConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("hamiltonian.solver must be a mapping")
    num = raw.get("num_eigenvalues", 12)
    if not isinstance(num, int) or num < 1:
        raise ValueError("hamiltonian.solver.num_eigenvalues must be a positive integer")
    method = raw.get("method", "eigh")
    if method != "eigh":
        raise ValueError("hamiltonian.solver.method currently supports only eigh")
    tolerance = raw.get("tolerance")
    if tolerance is not None and not isinstance(tolerance, int | float):
        raise ValueError("hamiltonian.solver.tolerance must be a number")
    if isinstance(tolerance, int | float) and tolerance <= 0:
        raise ValueError("hamiltonian.solver.tolerance must be positive")
    return SolverConfig(num_eigenvalues=num, method=str(method), tolerance=float(tolerance) if tolerance is not None else None)


def _required_mapping(raw: Mapping[str, Any], key: str, *, context: str | None = None) -> Mapping[str, Any]:
    if key not in raw:
        prefix = f"{context}." if context else ""
        raise ValueError(f"{prefix}{key} is required")
    value = raw[key]
    if not isinstance(value, Mapping):
        prefix = f"{context}." if context else ""
        raise ValueError(f"{prefix}{key} must be a mapping")
    return value


def _required_str(raw: Mapping[str, Any], key: str, *, display: str | None = None) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{display or key} must be a non-empty string")
    return value.strip()
