import pytest as _pytest

pytestmark = _pytest.mark.contract

from pathlib import Path

import pytest

from sqvm.device import load_device


CONFIG = Path("configs/devices/2q1c2r.yaml")


def test_load_minimal_valid_device():
    device = load_device(CONFIG)
    assert device.schema_version == "0.1"
    assert device.name == "demo_2q1c2r"
    assert device.topology == "2q1c2r"
    assert set(device.components) == {"q1", "q2", "c", "r1", "r2"}


def test_reject_missing_device_name(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("schema_version: '0.1'\ndevice:\n  topology: 2q1c2r\n  components: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="device.name"):
        load_device(path)


def test_requires_component_mapping(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "schema_version: '0.1'\ndevice:\n  name: bad\n  topology: 2q1c2r\n  components: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="device.components"):
        load_device(path)
