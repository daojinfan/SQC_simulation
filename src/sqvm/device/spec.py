"""Device configuration schema and loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


SUPPORTED_SCHEMA_VERSION = "0.1"
SUPPORTED_TOPOLOGY = "2q1c2r"


@dataclass(frozen=True, slots=True)
class SquidSpec:
    rn1_ohm: float
    rn2_ohm: float
    flux_bias_phi0: float
    asymmetry: float = 0.0


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    name: str
    kind: str
    role: str
    floating: bool | None = None
    nodes: tuple[str, ...] = ()
    capacitance_fF: float | None = None
    squid: SquidSpec | None = None
    coupled_to: str | None = None
    coupling_node: str | None = None
    frequency_GHz: float | None = None
    coupling_capacitance_fF: float | None = None
    kappa_MHz: float | None = None


@dataclass(frozen=True, slots=True)
class CapacitorSpec:
    name: str
    node_a: str
    node_b: str
    capacitance_fF: float


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    name: str
    kind: str
    target: str
    port: str


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    schema_version: str
    name: str
    topology: str
    components: dict[str, ComponentSpec]
    capacitors: tuple[CapacitorSpec, ...] = ()
    channels: dict[str, ChannelSpec] = field(default_factory=dict)
    priors: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None


def load_device(path: str | Path) -> DeviceSpec:
    """Load a 2q1c2r device configuration from YAML."""

    source_path = Path(path)
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{source_path}: invalid YAML: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{source_path}: cannot read device config: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ValueError("device config root must be a mapping")

    schema_version = _required_str(raw, "schema_version")
    device_raw = _required_mapping(raw, "device")
    name = _required_str(device_raw, "name", display="device.name")
    topology = _required_str(device_raw, "topology", display="device.topology")
    components = _parse_components(_required_mapping(device_raw, "components", context="device"))
    capacitors = _parse_capacitors(device_raw.get("capacitors", ()))
    channels = _parse_channels(device_raw.get("channels", {}))
    priors = _copy_mapping(device_raw.get("priors", {}), "device.priors")

    return DeviceSpec(
        schema_version=schema_version,
        name=name,
        topology=topology,
        components=components,
        capacitors=capacitors,
        channels=channels,
        priors=priors,
        source_path=source_path,
    )


def _parse_components(raw: Mapping[str, Any]) -> dict[str, ComponentSpec]:
    components: dict[str, ComponentSpec] = {}
    for name, value in raw.items():
        path = f"device.components.{name}"
        mapping = _required_mapping({name: value}, name, context="device.components")
        squid = None
        if "squid" in mapping:
            squid_raw = _required_mapping(mapping, "squid", context=path)
            squid = SquidSpec(
                rn1_ohm=_required_float(squid_raw, "rn1_ohm", display=f"{path}.squid.rn1_ohm"),
                rn2_ohm=_required_float(squid_raw, "rn2_ohm", display=f"{path}.squid.rn2_ohm"),
                flux_bias_phi0=_required_float(
                    squid_raw, "flux_bias_phi0", display=f"{path}.squid.flux_bias_phi0"
                ),
                asymmetry=_optional_float(squid_raw, "asymmetry", default=0.0, path=f"{path}.squid.asymmetry"),
            )
        components[str(name)] = ComponentSpec(
            name=str(name),
            kind=_required_str(mapping, "kind", display=f"{path}.kind"),
            role=_required_str(mapping, "role", display=f"{path}.role"),
            floating=_optional_bool(mapping, "floating", path=f"{path}.floating"),
            nodes=_tuple_of_str(mapping.get("nodes", ()), f"{path}.nodes"),
            capacitance_fF=_optional_float(mapping, "capacitance_fF", path=f"{path}.capacitance_fF"),
            squid=squid,
            coupled_to=_optional_str(mapping, "coupled_to", path=f"{path}.coupled_to"),
            coupling_node=_optional_str(mapping, "coupling_node", path=f"{path}.coupling_node"),
            frequency_GHz=_optional_float(mapping, "frequency_GHz", path=f"{path}.frequency_GHz"),
            coupling_capacitance_fF=_optional_float(
                mapping, "coupling_capacitance_fF", path=f"{path}.coupling_capacitance_fF"
            ),
            kappa_MHz=_optional_float(mapping, "kappa_MHz", path=f"{path}.kappa_MHz"),
        )
    return components


def _parse_capacitors(raw: Any) -> tuple[CapacitorSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple):
        raise ValueError("device.capacitors must be a list")
    capacitors: list[CapacitorSpec] = []
    for index, item in enumerate(raw):
        path = f"device.capacitors[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{path} must be a mapping")
        between = item.get("between")
        if not isinstance(between, list | tuple) or len(between) != 2:
            raise ValueError(f"{path}.between must contain two node names")
        node_a, node_b = str(between[0]), str(between[1])
        name = str(item.get("name", f"C_{node_a}_{node_b}"))
        capacitors.append(
            CapacitorSpec(
                name=name,
                node_a=node_a,
                node_b=node_b,
                capacitance_fF=_required_float(item, "capacitance_fF", display=f"{path}.capacitance_fF"),
            )
        )
    return tuple(capacitors)


def _parse_channels(raw: Any) -> dict[str, ChannelSpec]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("device.channels must be a mapping")
    channels: dict[str, ChannelSpec] = {}
    for name, value in raw.items():
        path = f"device.channels.{name}"
        mapping = _required_mapping({name: value}, name, context="device.channels")
        channels[str(name)] = ChannelSpec(
            name=str(name),
            kind=_required_str(mapping, "kind", display=f"{path}.kind"),
            target=_required_str(mapping, "target", display=f"{path}.target"),
            port=_required_str(mapping, "port", display=f"{path}.port"),
        )
    return channels


def _copy_mapping(raw: Any, path: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must be a mapping")
    return {str(key): _plain(value) for key, value in raw.items()}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


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
    try:
        value = _lookup(raw, key)
    except ValueError as exc:
        if display is not None:
            raise ValueError(f"{display} is required") from exc
        raise
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{display or key} must be a non-empty string")
    return value.strip()


def _optional_str(raw: Mapping[str, Any], key: str, *, path: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _required_float(raw: Mapping[str, Any], key: str, *, display: str | None = None) -> float:
    try:
        value = _lookup(raw, key)
    except ValueError as exc:
        if display is not None:
            raise ValueError(f"{display} is required") from exc
        raise
    if not isinstance(value, int | float):
        raise ValueError(f"{display or key} must be a number")
    return float(value)


def _optional_float(raw: Mapping[str, Any], key: str, *, path: str, default: float | None = None) -> float | None:
    value = raw.get(key, default)
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise ValueError(f"{path} must be a number")
    return float(value)


def _optional_bool(raw: Mapping[str, Any], key: str, *, path: str) -> bool | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _tuple_of_str(raw: Any, path: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple):
        raise ValueError(f"{path} must be a list")
    return tuple(str(item) for item in raw)


def _lookup(raw: Mapping[str, Any], key: str) -> Any:
    if "." not in key:
        if key not in raw:
            raise ValueError(f"{key} is required")
        return raw[key]
    cursor: Any = raw
    for part in key.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            raise ValueError(f"{key} is required")
        cursor = cursor[part]
    return cursor
