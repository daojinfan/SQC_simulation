"""Effective SQUID Josephson energies for Hamiltonian construction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import cos, isfinite, pi, sqrt
from numbers import Real
from typing import Any

from sqvm.hamiltonian.artifacts import DeviceArtifacts


@dataclass(frozen=True, slots=True)
class EffectiveJunction:
    mode: str
    component: str
    ej1_GHz: float
    ej2_GHz: float
    flux_bias_phi0: float
    ej_effective_GHz: float
    formula: str = "sqrt((EJ1+EJ2)^2*cos(pi*phi)^2 + (EJ1-EJ2)^2*sin(pi*phi)^2)"


def resolve_effective_junctions(
    device_artifacts: DeviceArtifacts,
    flux_bias_overrides_phi0: Mapping[str, float] | None = None,
) -> tuple[EffectiveJunction, ...]:
    overrides = _validated_flux_overrides(flux_bias_overrides_phi0)
    rows_by_component: dict[str, list[dict[str, Any]]] = {"q1": [], "q2": [], "c": []}
    for row in device_artifacts.payload["junction_parameters"]:
        component = row.get("component")
        if component in rows_by_component:
            rows_by_component[component].append(row)

    components = device_artifacts.payload["components"]
    resolved: list[EffectiveJunction] = []
    for mode in ("q1", "c", "q2"):
        rows = sorted(rows_by_component[mode], key=lambda item: str(item.get("junction", "")))
        if len(rows) != 2:
            raise ValueError(f"junction_parameters for {mode} must contain exactly two rows")
        squid = components.get(mode, {}).get("squid")
        if not isinstance(squid, dict) or "flux_bias_phi0" not in squid:
            raise ValueError(f"components.{mode}.squid.flux_bias_phi0 is required")
        ej1 = float(rows[0]["ej_GHz"])
        ej2 = float(rows[1]["ej_GHz"])
        flux = overrides.get(mode, float(squid["flux_bias_phi0"]))
        resolved.append(
            EffectiveJunction(
                mode=mode,
                component=mode,
                ej1_GHz=ej1,
                ej2_GHz=ej2,
                flux_bias_phi0=flux,
                ej_effective_GHz=effective_ej_GHz(ej1, ej2, flux),
            )
        )
    return tuple(resolved)


def _validated_flux_overrides(overrides: Mapping[str, float] | None) -> dict[str, float]:
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise ValueError("flux_bias_overrides_phi0 must be a mapping or None")

    supported_modes = {"q1", "c", "q2"}
    validated: dict[str, float] = {}
    for mode, value in overrides.items():
        if mode not in supported_modes:
            raise ValueError("flux override keys must be q1, c, or q2")
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"flux override for {mode} must be a finite real number")
        converted = float(value)
        if not isfinite(converted):
            raise ValueError(f"flux override for {mode} must be a finite real number")
        validated[mode] = converted
    return validated


def effective_ej_GHz(ej1_GHz: float, ej2_GHz: float, flux_bias_phi0: float) -> float:
    ej_sum = ej1_GHz + ej2_GHz
    ej_delta = ej1_GHz - ej2_GHz
    phase = pi * flux_bias_phi0
    return sqrt((ej_sum * cos(phase)) ** 2 + (ej_delta * np_sin(phase)) ** 2)


def np_sin(value: float) -> float:
    from math import sin

    return sin(value)
