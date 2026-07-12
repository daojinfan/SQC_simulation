"""Strict Stage 4.0 control-channel registry loading."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from sqvm.control.models import ControlChannelRegistry, ControlChannelSpec


CHANNEL_ORDER = ("q1_xy", "q2_xy", "q1_z", "q2_z", "c_z", "r1_ro", "r2_ro")
BASE_CHANNEL_ORDER = ("q1_xy", "q2_xy", "c_z", "r1_ro", "r2_ro")
ADDED_CHANNEL_ORDER = ("q1_z", "q2_z")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ROOT_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "device_name",
    "base_device_config", "base_device_artifact", "channels",
}
CHANNEL_KEYS = {"kind", "target", "port", "awg_lanes"}


def load_control_channel_registry(path: str | Path) -> ControlChannelRegistry:
    """Load the exact seven-channel registry and reject unknown structure."""

    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load control channel registry: {exc}") from exc
    root = _mapping(payload, "control channel registry")
    _exact_keys(root, ROOT_KEYS, "control channel registry")
    _equals(root, "schema_version", "0.1")
    _equals(root, "artifact_type", "control_channel_registry")
    _equals(root, "artifact_version", "0.1")
    _equals(root, "device_name", "demo_2q1c2r")
    _equals(root, "base_device_config", "configs/devices/2q1c2r.yaml")
    _equals(root, "base_device_artifact", "output/stage_01_device_model/device_artifacts.json")

    raw_channels = _mapping(root["channels"], "channels")
    if set(raw_channels) != set(CHANNEL_ORDER):
        raise ValueError("channels must contain the exact seven frozen channel identifiers")
    channels: list[ControlChannelSpec] = []
    ports: set[str] = set()
    lanes: set[str] = set()
    for name in CHANNEL_ORDER:
        _identifier(name, f"channels.{name}")
        row = _mapping(raw_channels[name], f"channels.{name}")
        _exact_keys(row, CHANNEL_KEYS, f"channels.{name}")
        kind = _text(row["kind"], f"channels.{name}.kind")
        target = _text(row["target"], f"channels.{name}.target")
        port = _text(row["port"], f"channels.{name}.port")
        for value, label in ((target, "target"), (port, "port")):
            _identifier(value, f"channels.{name}.{label}")
        if kind not in {"xy", "z", "readout"}:
            raise ValueError(f"channels.{name}.kind is invalid")
        raw_lanes = row["awg_lanes"]
        if not isinstance(raw_lanes, list) or isinstance(raw_lanes, (str, bytes)):
            raise ValueError(f"channels.{name}.awg_lanes must be a list")
        awg_lanes = tuple(_text(value, f"channels.{name}.awg_lanes") for value in raw_lanes)
        expected_count = 1 if kind == "z" else 2
        if len(awg_lanes) != expected_count:
            raise ValueError(f"channels.{name}.awg_lanes must contain exactly {expected_count} lanes")
        for lane in awg_lanes:
            _identifier(lane, f"channels.{name}.awg_lanes")
        if port in ports:
            raise ValueError(f"duplicate channel port: {port}")
        duplicate_lanes = lanes.intersection(awg_lanes)
        if duplicate_lanes or len(set(awg_lanes)) != len(awg_lanes):
            duplicate = sorted(duplicate_lanes or set(awg_lanes))[0]
            raise ValueError(f"duplicate AWG lane: {duplicate}")
        ports.add(port)
        lanes.update(awg_lanes)
        channels.append(ControlChannelSpec(name, kind, target, port, awg_lanes))

    return ControlChannelRegistry(
        schema_version="0.1",
        artifact_type="control_channel_registry",
        artifact_version="0.1",
        device_name="demo_2q1c2r",
        base_device_config=Path(str(root["base_device_config"])),
        base_device_artifact=Path(str(root["base_device_artifact"])),
        channels=tuple(channels),
        source_path=source,
    )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{path} fields are not exact")


def _equals(value: Mapping[str, Any], key: str, expected: str) -> None:
    if value.get(key) != expected:
        raise ValueError(f"{key} must be {expected}")


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _identifier(value: str, path: str) -> None:
    if not IDENTIFIER.fullmatch(value) or not value.isascii():
        raise ValueError(f"{path} is not a valid ASCII identifier")
