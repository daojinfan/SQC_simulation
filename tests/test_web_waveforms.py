from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import uuid

import numpy as np
import pytest

from sqvm.web.server import _waveform_request
from sqvm.web.waveforms import (
    DirectoryEvidenceReader,
    WaveformEvidenceError,
    waveform_catalog,
    waveform_point,
)
from tests.support.web_projection import (
    close_server,
    request,
    start_server,
)


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def _waveform_run(root: Path) -> tuple[Path, str, str]:
    run_id = "waveform-run"
    circuit_id = "circuit-000"
    source = "X2P Q1\nX2P Q1\n"
    run = root / "run"
    control = run / "execution" / circuit_id / "stage41" / "control"
    arrays_root = control / "arrays"
    arrays_root.mkdir(parents=True)
    (run / "workflow.json").write_text(
        json.dumps({"run_id": run_id, "workflow_id": "test"}), encoding="utf-8"
    )
    (run / "dataset.json").write_text(
        json.dumps(
            {
                "points": [
                    {
                        "point": {
                            "point_index": 0,
                            "circuit_id": circuit_id,
                            "coordinates_GHz": {"Q1": 5.2},
                            "qcis_source": source,
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows: list[dict[str, object]] = []

    def array(name: str, values: np.ndarray, unit: str) -> None:
        value = np.ascontiguousarray(values)
        path = arrays_root / f"{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value.tobytes()
        path.write_bytes(raw)
        rows.append(
            {
                "name": name,
                "path": f"arrays/{name}.bin",
                "dtype": value.dtype.str,
                "shape": [value.size],
                "unit": unit,
                "byte_length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest().upper(),
            }
        )

    logical_time = np.asarray([0.25, 0.75], dtype="<f8")
    array("logical/time_center_ns", logical_time, "ns")
    for name in ("q1_i", "q1_q", "q2_i", "q2_q"):
        array(f"logical/{name}", np.asarray([0.1, -0.1] if name == "q1_i" else [0.0, 0.0], dtype="<f8"), "GHz")
    for name in ("q1_flux_delta", "q2_flux_delta", "c_flux_delta"):
        array(f"logical/{name}", np.zeros(2, dtype="<f8"), "Phi/Phi0")

    awg_time = np.asarray([-0.25, 0.25, 0.75], dtype="<f8")
    delivered_time = np.asarray([-0.25, 0.25, 0.75, 1.25], dtype="<f8")
    array("awg/time_center_ns", awg_time, "ns")
    lanes = ("c_z", "q1_xy_i", "q1_xy_q", "q1_z", "q2_xy_i", "q2_xy_q", "q2_z")
    for lane in lanes:
        requested = np.asarray([0.0, 0.2, -0.2] if lane == "q1_xy_i" else [0.0, 0.0, 0.0], dtype="<f8")
        array(f"awg/{lane}/requested_voltage", requested, "V")
        array(f"awg/{lane}/dac_codes", np.rint(requested * 100).astype("<i8"), "DAC_code")
        array(f"awg/{lane}/reconstructed_voltage", requested, "V")
        array(f"awg/{lane}/delivered_voltage", np.append(requested, 0.0).astype("<f8"), "V")

    array("effective/time_center_ns", delivered_time, "ns")
    for name in ("q1_i", "q1_q", "q2_i", "q2_q"):
        array(f"effective/{name}", np.asarray([0.0, 0.1, -0.1, 0.0] if name == "q1_i" else [0.0] * 4, dtype="<f8"), "GHz")
    for target, idle in (("q1", 0.12), ("q2", 0.0), ("c", -0.03)):
        array(f"effective/{target}_flux_delta", np.zeros(4, dtype="<f8"), "Phi/Phi0")
        array(f"effective/{target}_flux_absolute", np.full(4, idle, dtype="<f8"), "Phi/Phi0")

    (control / "array_inventory.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "artifact_type": "stage_04_1_array_inventory",
                "artifact_version": "0.1",
                "arrays": rows,
            }
        ),
        encoding="utf-8",
    )
    (control / "control.json").write_text(
        json.dumps({"status": "published", "control_id": "C" * 64}), encoding="utf-8"
    )
    circuit_root = run / "execution" / circuit_id
    payload_files = []
    for path in sorted(control.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        payload_files.append(
            {
                "path": path.relative_to(circuit_root).as_posix(),
                "byte_length": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
            }
        )
    (circuit_root / "manifest.json").write_text(
        json.dumps({"payload_files": payload_files}), encoding="utf-8"
    )
    return run, run_id, circuit_id


def test_waveform_projection_binds_qcis_and_preserves_each_time_grid(tmp_path: Path) -> None:
    run, run_id, circuit_id = _waveform_run(tmp_path)
    reader = DirectoryEvidenceReader(run)
    catalog = waveform_catalog(reader, run_id)
    assert catalog["default_circuit_id"] == circuit_id
    assert catalog["points"][0]["label"] == "#0 · Q1=5.2 GHz"

    awg = waveform_point(reader, run_id, circuit_id, view="awg", max_points=100)
    assert awg["qcis"]["source"] == "X2P Q1\nX2P Q1\n"
    assert awg["control"]["sample_counts"] == {"awg": 3, "effective": 4}
    voltage = awg["plot_specs"][0]
    requested = next(row for row in voltage["series"] if row["id"] == "awg_voltage:q1_xy_i:requested_voltage")
    delivered = next(row for row in voltage["series"] if row["id"] == "awg_voltage:q1_xy_i:delivered_voltage")
    assert [row["x"] for row in requested["points"]] == [-0.25, 0.25, 0.75]
    assert [row["x"] for row in delivered["points"]] == [-0.25, 0.25, 0.75, 1.25]

    effective = waveform_point(reader, run_id, circuit_id, view="effective", max_points=100)
    logical = waveform_point(reader, run_id, circuit_id, view="logical", max_points=100)
    assert effective["control"]["sample_counts"] == {"effective": 4}
    assert logical["control"]["sample_counts"] == {"logical": 2}
    assert [row["plot_id"] for row in effective["plot_specs"]] == [
        "waveform_effective_xy",
        "waveform_effective_flux",
    ]


def test_waveform_projection_rejects_tampered_array_and_qcis_hash(tmp_path: Path) -> None:
    run, run_id, circuit_id = _waveform_run(tmp_path)
    reader = DirectoryEvidenceReader(run)
    target = run / "execution" / circuit_id / "stage41" / "control" / "arrays" / "awg" / "q1_xy_i" / "delivered_voltage.bin"
    raw = bytearray(target.read_bytes())
    raw[0] ^= 1
    target.write_bytes(raw)
    with pytest.raises(WaveformEvidenceError, match="哈希"):
        waveform_point(reader, run_id, circuit_id, view="awg")

    run, run_id, circuit_id = _waveform_run(tmp_path / "combined")
    control = run / "execution" / circuit_id / "stage41" / "control"
    target = control / "arrays" / "awg" / "q1_xy_i" / "delivered_voltage.bin"
    raw = bytearray(target.read_bytes())
    raw[-1] ^= 1
    target.write_bytes(raw)
    inventory_path = control / "array_inventory.json"
    inventory = json.loads(inventory_path.read_text("utf-8"))
    row = next(item for item in inventory["arrays"] if item["name"] == "awg/q1_xy_i/delivered_voltage")
    row["sha256"] = hashlib.sha256(raw).hexdigest().upper()
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(WaveformEvidenceError, match="manifest"):
        waveform_point(DirectoryEvidenceReader(run), run_id, circuit_id, view="awg")

    run, run_id, circuit_id = _waveform_run(tmp_path / "second")
    batch = run / "execution" / "batch"
    batch.mkdir()
    (batch / "request.json").write_text(
        json.dumps(
            {
                "circuits": [
                    {
                        "point_index": 0,
                        "circuit_id": circuit_id,
                        "circuit_sha256": "A" * 64,
                        "qcis_source": "X2P Q1\nX2P Q1\n",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(WaveformEvidenceError, match="QCIS"):
        waveform_point(DirectoryEvidenceReader(run), run_id, circuit_id, view="logical")


def test_waveform_query_and_http_routes_are_bounded(tmp_path: Path) -> None:
    assert _waveform_request("") == ("awg", 2_000)
    assert _waveform_request("view=effective&max_points=900") == ("effective", 900)
    with pytest.raises(Exception, match="view"):
        _waveform_request("view=unknown")

    server_base = ROOT / "tmp" / f"web_waveforms_{uuid.uuid4().hex}"
    server_base.mkdir(parents=True)
    server, thread, base_url = start_server(ROOT, server_base)
    calls: list[tuple[object, ...]] = []
    server.storage.waveform_catalog = lambda run_id: calls.append(("catalog", run_id)) or {"run_id": run_id, "points": []}  # type: ignore[method-assign]
    server.storage.waveform_point = lambda run_id, circuit_id, **options: calls.append(("point", run_id, circuit_id, options)) or {"run_id": run_id, "view": options["view"]}  # type: ignore[method-assign]
    try:
        assert request(f"{base_url}/api/v1/experiments/run-1/waveforms")[2]["run_id"] == "run-1"
        payload = request(
            f"{base_url}/api/v1/experiments/run-1/waveforms/circuit-2?view=logical&max_points=300"
        )[2]
        assert payload == {"run_id": "run-1", "view": "logical"}
        assert calls == [
            ("catalog", "run-1"),
            ("point", "run-1", "circuit-2", {"view": "logical", "max_points": 300}),
        ]
    finally:
        close_server(server, thread)
        shutil.rmtree(server_base, ignore_errors=True)


def test_frontend_exposes_point_bound_qcis_and_lazy_waveform_views() -> None:
    source = (ROOT / "src" / "sqvm" / "web" / "static" / "app.js").read_text("utf-8")
    styles = (ROOT / "src" / "sqvm" / "web" / "static" / "styles.css").read_text("utf-8")
    assert "function installWaveformExplorer(detail, routeContext)" in source
    assert "当前扫描点 QCIS 指令" in source
    assert "/waveforms/${encodeURIComponent(pointSelect.value)}?view=" in source
    assert "data-waveform-view=\"awg\"" in source
    assert ".waveform-view-tabs" in styles
