"""Independent Stage 7.0 QCIS acceptance tests.

The frozen design deliberately defines bytes and hashes rather than a Python API.
``_compiler`` is the only implementation-adaptation point: Stage 7.0 must expose
one of the listed direct, side-effect-free compiler entry points.  The result must
provide the named design artifacts either as mapping keys or attributes.
"""

from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _artifact(result: Any, *names: str) -> Any:
    for name in names:
        if isinstance(result, Mapping) and name in result:
            return result[name]
        if hasattr(result, name):
            return getattr(result, name)
    pytest.fail(f"QCIS compiler result lacks required design artifact; expected one of {names!r}")


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, np.ndarray):
        return np.ascontiguousarray(value).tobytes()
    return _canonical_json(value)


def _compiler():
    """Resolve only a public, pure Stage 7 compiler API; never a test double."""

    candidates = (
        ("sqvm.qcis", "compile_qcis"),
        ("sqvm.stage7.qcis", "compile_qcis_program"),
        ("sqvm.stage7.qcis", "compile_program"),
        ("sqvm.runtime.qcis", "compile_qcis_program"),
        ("sqvm.runtime", "compile_qcis_program"),
    )
    missing: list[str] = []
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name == module_name or module_name.startswith(f"{error.name}."):
                missing.append(f"{module_name}.{attribute}")
                continue
            if module_name == "sqvm.qcis" and error.name.startswith("sqvm.qcis."):
                pytest.fail(
                    "Stage 7.0 publicly exports sqvm.qcis but its compile_qcis entry is incomplete: "
                    f"missing internal module {error.name!r}."
                )
            raise
        compiler = getattr(module, attribute, None)
        if callable(compiler):
            return compiler
        missing.append(f"{module_name}.{attribute}")
    pytest.fail(
        "Stage 7.0 QCIS compiler API is not available. Expected one direct, "
        f"side-effect-free compiler entry point: {', '.join(missing)}."
    )


def _compile(
    program: Mapping[str, Any],
    *,
    authorities: Mapping[str, Any],
    idle_flux: Mapping[str, float] | None = None,
    scan_values: Mapping[str, Any] | None = None,
):
    """The one deliberately small implementation-adaptation boundary."""

    compiler = _compiler()
    kwargs = {
        "program": program,
        "authorities": authorities,
        "idle_flux": {"q1": 0.1, "q2": 0.0, "c": 0.27} if idle_flux is None else idle_flux,
    }
    if scan_values is not None:
        kwargs["scan_values"] = scan_values
    return compiler(**kwargs)


def _program(source: str, *, template_id: str, bindings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "program_schema_version": "0.1",
        "instruction_set_id": "qcis_stage7_calibration_v1",
        "template_id": template_id,
        "template_sha256": _sha_bytes(source.encode("utf-8")),
        "source_format": "qcis_template",
        "source": source,
        "bindings": {} if bindings is None else dict(bindings),
    }


def _literal(value: float, unit: str = "GHz") -> dict[str, Any]:
    return {"literal": value, "unit": unit}


def _scan_ref(name: str, unit: str = "GHz") -> dict[str, Any]:
    return {"scan_ref": name, "unit": unit}


APPEND_TEMPLATE = "PLSXY Q1 1 -1 1 $amplitude 5 0 0 1\nI Q1 2\nB Q1 Q2\n"
APPEND_SOURCE = "PLSXY Q1 1 -1 1 0.125 5 0 0 1\nI Q1 2\nB Q1 Q2\n"
RECTANGLE_SOURCE = "PLS C 0 2 2 0.25 0 0 0 2\nI Q1 4\nB Q1 C\n"
NONZERO_SOURCE = "RZ Q1 0.25\nPLSXY Q1 1 -1 3 0.125 5 0 0.5 1\n"
MACRO_SOURCE = "X2P Q1\nY2P Q1\n"
MACRO_TEMPLATE_ID = "qcis_xy2_macro_fixture_v1"
NONFIXTURE_MACRO_TEMPLATE_ID = "qcis_xy2_nonfixture_v1"


def _authorities(*, include_all_agents: bool = False) -> dict[str, Any]:
    """Frozen fixture authorities; implementations must snapshot and hash all of them."""

    authorities = {
        "instruction_profile": {"profile_id": "qcis_stage7_calibration_v1", "profile_version": "0.1", "schema_version": "0.1"},
        "qagent_registry": {
            "Q1": {"component": "q1", "xy_channel": "q1_xy", "z_channel": "q1_z"},
            "schema_version": "0.1",
        },
        "gate_configuration": {"Q1": {"active_xy2_setting": "fixture_q1_xy2_v1"}, "schema_version": "0.1"},
        "waveform_registry": {
            "schema_version": "0.1",
            "settings": {"fixture_q1_xy2_v1": {"formula_id": "qcis_gaussian_drag_samples_v1"}},
        },
        "clock": {"dt_ns": 0.5, "sample_rate_Hz": 2_000_000_000, "schema_version": "0.1"},
        "compiler": {
            "compiler_id": "sqvm_qcis_compiler_v1",
            "compiler_version": "0.1",
            "numeric_kernel": "cpython_3.12.10_scalar_binary64_v1",
            "schema_version": "0.1",
            "source_sha256": "A" * 64,
        },
        "calibration": {
            "calibration_id": "fixture_q1_xy2_v1",
            "q1": {
                "amplitude_GHz": 0.125,
                "carrier_frequency_GHz": 5.0,
                "dragAlpha_samples": 0.0,
                "formula_id": "qcis_gaussian_drag_samples_v1",
                "length_samples": 1,
                "r_sigma_samples": 1.0,
            },
            "schema_version": "0.1",
            "status": "accepted_simulation",
        },
        "templates": {
            "append": {"source": APPEND_TEMPLATE, "bindings": {"amplitude": {"unit": "GHz", "occurrences": 1, "position": [0, "amplitude"]}}},
            "rectangle": {"source": RECTANGLE_SOURCE, "bindings": {}},
            "nonzero": {"source": NONZERO_SOURCE, "bindings": {}},
            MACRO_TEMPLATE_ID: {"source": MACRO_SOURCE, "bindings": {}},
        },
    }
    if include_all_agents:
        authorities["qagent_registry"] = {
            **authorities["qagent_registry"],
            "Q2": {"component": "q2", "xy_channel": "q2_xy", "z_channel": "q2_z"},
            "C": {"component": "c", "z_channel": "c_flux"},
        }
    return authorities


def _registered_case(source: str, *, bindings: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Register a single controlled source so parser/semantic rejections are reached."""

    authorities = _authorities(include_all_agents=True)
    authorities["templates"]["case"] = {"source": source, "bindings": {} if bindings is None else dict(bindings)}
    return _program(source, template_id="case"), authorities


def _nonfixture_macro_authorities() -> dict[str, Any]:
    """A complete accepted calibration that must not inherit fixture-only hashes."""

    authorities = _authorities()
    authorities["calibration"] = {
        **authorities["calibration"],
        "calibration_id": "independent_q1_xy2_v2",
        "q1": {**authorities["calibration"]["q1"], "amplitude_GHz": 0.2},
    }
    authorities["gate_configuration"] = {
        "Q1": {"active_xy2_setting": "independent_q1_xy2_v2"},
        "schema_version": "0.1",
    }
    authorities["waveform_registry"] = {
        "schema_version": "0.1",
        "settings": {
            "independent_q1_xy2_v2": {
                "formula_id": "qcis_gaussian_drag_samples_v1",
                "targets": ["Q1"],
            },
        },
    }
    authorities["templates"][NONFIXTURE_MACRO_TEMPLATE_ID] = {"source": MACRO_SOURCE, "bindings": {}}
    authorities["expected_sha256"] = {
        name: _sha_bytes(_canonical_json(authorities[name]))
        for name in ("calibration", "instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")
    }
    return authorities


def _assert_sha(result: Any, expected: str, *names: str) -> None:
    assert _sha_bytes(_as_bytes(_artifact(result, *names))) == expected


def _assert_array_sha(result: Any, expected: str, *names: str) -> None:
    value = _artifact(result, *names)
    array = np.ascontiguousarray(np.asarray(value))
    assert _sha_bytes(array.tobytes()) == expected


def _failure_code(error: BaseException) -> str:
    for name in ("code", "reason", "reason_code"):
        value = getattr(error, name, None)
        if isinstance(value, str):
            return value
    return str(error)


def _assert_reject(
    program: Mapping[str, Any],
    expected_code: str,
    *,
    authorities: Mapping[str, Any] | None = None,
    scan_values: Mapping[str, Any] | None = None,
) -> None:
    with pytest.raises(Exception) as captured:
        _compile(
            program,
            authorities=_authorities() if authorities is None else authorities,
            scan_values=scan_values,
        )
    assert expected_code in _failure_code(captured.value)


def test_append_gaussian_materialization_ast_trace_and_raw_arrays():
    result = _compile(
        _program(APPEND_TEMPLATE, template_id="append", bindings={"amplitude": _literal(0.125)}),
        authorities=_authorities(include_all_agents=True),
    )
    assert _artifact(result, "materialized_source", "concrete_source") == APPEND_SOURCE
    _assert_sha(result, "FF3D3C375B574CF50DDDFA9ACD9A12954435B0A09D1F03EDD1CADE49C80922FA", "ast_bytes", "canonical_ast")
    _assert_sha(result, "BA26F4E7FB2C7126E0308903EF63206F6DF3CECAF2AE91F326081076FB0D56C4", "trace_bytes", "canonical_trace")
    _assert_array_sha(result, "872379342B861D074CDB5AC2585B3065CF7DBEF86A46E6D2DF8174A35C82F411", "q1_xy", "logical_q1_xy")
    _assert_array_sha(result, "17B0761F87B081D5CF10757CCC89F12BE355C70E2E29DF288B65B30710DCBCD1", "q2_xy", "logical_q2_xy")
    _assert_array_sha(result, "9D908ECFB6B256DEF8B49A7C504E6C889C4B0E41FE6CE3E01863DD7B61A20AA0", "q1_flux", "logical_q1_flux")
    _assert_array_sha(result, "9D908ECFB6B256DEF8B49A7C504E6C889C4B0E41FE6CE3E01863DD7B61A20AA0", "q2_flux", "logical_q2_flux")
    _assert_array_sha(result, "9D908ECFB6B256DEF8B49A7C504E6C889C4B0E41FE6CE3E01863DD7B61A20AA0", "c_flux", "logical_c_flux")


def test_literal_and_scan_ref_materialize_to_byte_identical_compilation():
    literal = _compile(
        _program(APPEND_TEMPLATE, template_id="append", bindings={"amplitude": _literal(0.125)}),
        authorities=_authorities(include_all_agents=True),
    )
    scanned = _compile(
        _program(APPEND_TEMPLATE, template_id="append", bindings={"amplitude": _scan_ref("drive_amplitude")}),
        authorities=_authorities(include_all_agents=True),
        scan_values={"drive_amplitude": {"value": 0.125, "unit": "GHz"}},
    )
    for names in (
        ("materialized_source", "concrete_source"),
        ("ast_bytes", "canonical_ast"),
        ("trace_bytes", "canonical_trace"),
        ("q1_xy", "logical_q1_xy"),
        ("q2_xy", "logical_q2_xy"),
        ("q1_flux", "logical_q1_flux"),
        ("q2_flux", "logical_q2_flux"),
        ("c_flux", "logical_c_flux"),
    ):
        assert _as_bytes(_artifact(literal, *names)) == _as_bytes(_artifact(scanned, *names))


def test_absolute_rectangle_ast_trace_and_all_raw_arrays():
    result = _compile(_program(RECTANGLE_SOURCE, template_id="rectangle"), authorities=_authorities(include_all_agents=True))
    _assert_sha(result, "CA60729BCB4DE74C92138BCD510B85DD424A26D9E1EC426B3E4314D9A2BBB337", "ast_bytes", "canonical_ast")
    _assert_sha(result, "15FF034CF23213AFCBC5AB2B8589C5267E251CC2BFE148278A3CD3674C6E9126", "trace_bytes", "canonical_trace")
    _assert_array_sha(result, "F5A5FD42D16A20302798EF6ED309979B43003D2320D9F0E8EA9831A92759FB4B", "q1_xy", "logical_q1_xy")
    _assert_array_sha(result, "F5A5FD42D16A20302798EF6ED309979B43003D2320D9F0E8EA9831A92759FB4B", "q2_xy", "logical_q2_xy")
    _assert_array_sha(result, "66687AADF862BD776C8FC18B8E9F8E20089714856EE233B3902A591D0D5F2925", "q1_flux", "logical_q1_flux")
    _assert_array_sha(result, "66687AADF862BD776C8FC18B8E9F8E20089714856EE233B3902A591D0D5F2925", "q2_flux", "logical_q2_flux")
    _assert_array_sha(result, "537C71B5C7EFC0E0DDCA126E2C3029A3A74CA8C50ED979D489962AAED797DD3D", "c_flux", "logical_c_flux")


def test_nonzero_drag_rz_bytes_carrier_effective_and_coefficient_inventory():
    result = _compile(_program(NONZERO_SOURCE, template_id="nonzero"), authorities=_authorities())
    _assert_sha(result, "32BFCBBD24895C9311C08661664C948A5F4961A26C5CB1D837BCA8C8E6F8598C", "ast_bytes", "canonical_ast")
    _assert_sha(result, "96ABE204A820B660E6B248BA6E63501FCAEA9BB6BD97F5A1DEC3BE6B1284B293", "trace_bytes", "canonical_trace")
    _assert_array_sha(result, "3828A0A3C7FDED386C5659F0791D0D3048088BF88D97BD0AB37A1938DE84A3A5", "q1_xy", "logical_q1_xy")
    _assert_sha(result, "2980D038907D648B022B38A95E36A1EA94387243D587006EFCD40D18008100F6", "carrier_metadata_bytes", "carrier_metadata")
    _assert_array_sha(result, "3828A0A3C7FDED386C5659F0791D0D3048088BF88D97BD0AB37A1938DE84A3A5", "effective_q1_xy", "effective_epsilon_q1")
    _assert_sha(result, "D8EE3AAAF759410DC48B65C1C167B04A6E0C374193AAB0516F4AF660F8F3EA3E", "coefficient_inventory_bytes", "coefficient_inventory")


def test_xy2_macro_authority_provenance_ast_trace_and_waveform():
    result = _compile(_program(MACRO_SOURCE, template_id=MACRO_TEMPLATE_ID), authorities=_authorities())
    _assert_sha(result, "FE3FACB9075ECAF35F79A73F452EDE7EB0234E9F665E333E2EABEE2605A80E3A", "ast_bytes", "canonical_ast")
    _assert_sha(result, "053FEB79E04020A4633D2F0098057AAB6E07CA75E6E467AE503F55D26749097C", "trace_bytes", "canonical_trace")
    _assert_array_sha(result, "AD92ABD3DBECF6F2B61E87834632713F9D97DA6A14AC3E6F1DB5A9739E89DD7E", "q1_xy", "logical_q1_xy")
    trace = json.loads(_as_bytes(_artifact(result, "trace_bytes", "canonical_trace")))
    assert set(trace["authority_sha256"]) == {
        "calibration", "clock", "compiler", "gate_configuration", "instruction_profile", "program", "qagent_registry", "waveform_registry",
    }


def test_nonfixture_accepted_macro_calibration_binds_its_own_authority_and_program_hashes():
    authorities = _nonfixture_macro_authorities()
    program = _program(MACRO_SOURCE, template_id=NONFIXTURE_MACRO_TEMPLATE_ID)
    result = _compile(program, authorities=authorities)
    trace = _artifact(result, "trace", "provenance_trace")
    actual = trace["authority_sha256"]
    assert actual["calibration"] == authorities["expected_sha256"]["calibration"]
    assert actual["gate_configuration"] == authorities["expected_sha256"]["gate_configuration"]
    assert actual["waveform_registry"] == authorities["expected_sha256"]["waveform_registry"]
    assert actual["program"] == _sha_bytes(_canonical_json(program))
    assert actual["calibration"] != "379A9E0871F785804A75AE58301865575E54911C614F877A6401ED6EF2FF383F"
    assert actual["program"] != "D0FF020540D28105E2783AFB1E4780C19C22984C052DEC233FDFD58A17757D2A"
    tampered = _nonfixture_macro_authorities()
    tampered["expected_sha256"] = {**tampered["expected_sha256"], "calibration": "0" * 64}
    _assert_reject(program, "QCIS_CALIBRATION_AUTHORITY_HASH_MISMATCH", authorities=tampered)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda authorities: authorities.update({"gate_configuration": {"Q1": {}, "schema_version": "0.1"}}),
        lambda authorities: authorities["gate_configuration"]["Q1"].update({"active_xy2_setting": "missing_setting"}),
        lambda authorities: authorities["waveform_registry"]["settings"]["independent_q1_xy2_v2"].update({"formula_id": "wrong_formula_v1"}),
        lambda authorities: authorities["waveform_registry"]["settings"]["independent_q1_xy2_v2"].update({"targets": ["Q2"]}),
    ],
    ids=("missing_active_setting", "unknown_setting", "formula_mismatch", "wrong_target"),
)
def test_macro_setting_resolution_rejects_missing_unknown_mismatched_and_inapplicable_entries(mutate, tmp_path: Path):
    authorities = _nonfixture_macro_authorities()
    mutate(authorities)
    authorities.pop("expected_sha256")
    program = _program(MACRO_SOURCE, template_id=NONFIXTURE_MACRO_TEMPLATE_ID)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    _assert_stable_qcis_reject(program, authorities=authorities)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


@pytest.mark.parametrize("invalid_length", [1.5, True, "1", float("nan"), float("inf")])
def test_macro_calibration_length_requires_a_finite_positive_integer_and_has_no_side_effects(invalid_length: Any, tmp_path: Path):
    authorities = _authorities()
    authorities["calibration"] = {
        **authorities["calibration"],
        "q1": {**authorities["calibration"]["q1"], "length_samples": invalid_length},
    }
    program = _program(MACRO_SOURCE, template_id=MACRO_TEMPLATE_ID)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    _assert_stable_qcis_reject(program, authorities=authorities)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


def test_macro_calibration_integer_length_one_retains_frozen_waveform_and_trace_hashes():
    authorities = _authorities()
    assert type(authorities["calibration"]["q1"]["length_samples"]) is int
    assert authorities["calibration"]["q1"]["length_samples"] == 1
    result = _compile(_program(MACRO_SOURCE, template_id=MACRO_TEMPLATE_ID), authorities=authorities)
    _assert_sha(result, "053FEB79E04020A4633D2F0098057AAB6E07CA75E6E467AE503F55D26749097C", "trace_bytes", "canonical_trace")
    _assert_array_sha(result, "AD92ABD3DBECF6F2B61E87834632713F9D97DA6A14AC3E6F1DB5A9739E89DD7E", "q1_xy", "logical_q1_xy")


@pytest.mark.parametrize("token", ["+1", "01", ".5", "1.", "1e+3", "-0", "-0.0", "NaN", "Inf", "0x1p0", "1,5", "pi"])
def test_canonical_decimal_tokens_reject(token: str):
    source = f"PLSXY Q1 1 -1 1 {token} 5 0 0 1\n"
    program, authorities = _registered_case(source)
    _assert_reject(program, "QCIS_NONCANONICAL_NUMBER", authorities=authorities)


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("PLSXY Q1 1 -1 1 0.125 5 0 0 1\n\n", "QCIS_NONCANONICAL_SOURCE"),
        ("plSXY Q1 1 -1 1 0.125 5 0 0 1\n", "QCIS_NONCANONICAL_SOURCE"),
        ("PLSXY Q01 1 -1 1 0.125 5 0 0 1\n", "QCIS_UNKNOWN_QAGENT"),
        ("RXY Q1 0.1\n", "QCIS_ARITY_MISMATCH"),
        ("PLSXY Q1 -1 0 0 1 2\n", "QCIS_ARITY_MISMATCH"),
        ("PLS C 3 -1 4 0.25 0 0 0 1\n", "QCIS_UNSUPPORTED_WAVE_INDEX"),
    ],
)
def test_source_and_opcode_rejections_have_stable_codes(source: str, code: str):
    program, authorities = _registered_case(source)
    _assert_reject(program, code, authorities=authorities)


def test_template_binding_and_authority_rejections_have_stable_codes():
    tampered = "PLSXY Q1 1 -1 1 $frequency 5 0 0 1\nI Q1 2\nB Q1 Q2\n"
    _assert_reject(
        _program(tampered, template_id="append", bindings={"frequency": _literal(0.125)}),
        "QCIS_TEMPLATE_SHA_MISMATCH",
        authorities=_authorities(include_all_agents=True),
    )
    _assert_reject(
        _program(APPEND_TEMPLATE, template_id="append", bindings={}),
        "QCIS_BINDING_SET_MISMATCH",
        authorities=_authorities(include_all_agents=True),
    )
    bad = _authorities()
    bad["expected_sha256"] = {"calibration": "379A9E0871F785804A75AE58301865575E54911C614F877A6401ED6EF2FF383F"}
    bad["calibration"] = {**bad["calibration"], "status": "tampered"}
    _assert_reject(_program(MACRO_SOURCE, template_id=MACRO_TEMPLATE_ID), "QCIS_CALIBRATION_AUTHORITY_HASH_MISMATCH", authorities=bad)


def test_timing_overlap_adds_without_artifact_side_effects(tmp_path: Path):
    overlap = "PLSXY Q1 1 0 2 0.125 5 0 0 1\nPLSXY Q1 1 1 2 0.125 5 0 0 1\n"
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    program, authorities = _registered_case(overlap)
    result = _compile(program, authorities=authorities)
    waveform = np.asarray(result.q1_xy).real
    assert waveform[0] == pytest.approx(waveform[2])
    assert waveform[1] == pytest.approx(2.0 * waveform[0])
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


def test_disjoint_absolute_intervals_accept_even_when_second_start_is_before_cursor():
    source = (
        "PLSXY Q1 1 5 2 0.125 5 0 0 1\n"
        "PLSXY Q1 1 0 4 0.125 5 0 0 1\n"
    )
    program, authorities = _registered_case(source)
    result = _compile(program, authorities=authorities)
    assert _artifact(result, "trace", "provenance_trace")["final_cursors"]["Q1"]["xy"] == 7
    waveform = np.asarray(_artifact(result, "q1_xy", "logical_q1_xy"))
    assert waveform.shape == (7,)
    assert np.flatnonzero(waveform).tolist() == [0, 1, 2, 3, 5, 6]
    assert waveform[4] == 0j


def _qcis_compilation_error_type():
    return importlib.import_module("sqvm.qcis").QCISCompilationError


def _assert_stable_qcis_reject(
    program: Mapping[str, Any],
    *,
    authorities: Mapping[str, Any],
    idle_flux: Mapping[str, Any] | None = None,
    scan_values: Mapping[str, Any] | None = None,
) -> None:
    with pytest.raises(_qcis_compilation_error_type()) as captured:
        _compile(program, authorities=authorities, idle_flux=idle_flux, scan_values=scan_values)
    code = getattr(captured.value, "code", None)
    assert isinstance(code, str) and code.startswith("QCIS_")


def test_zero_r_sigma_rejects_with_stable_qcis_error_and_no_side_effects(tmp_path: Path):
    source = "PLSXY Q1 1 -1 3 0.125 5 0 0.5 0\n"
    program, authorities = _registered_case(source)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    _assert_stable_qcis_reject(program, authorities=authorities)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


@pytest.mark.parametrize(
    "idle_flux",
    [
        {"q1": float("nan"), "q2": 0.0, "c": 0.27},
        {"q1": float("inf"), "q2": 0.0, "c": 0.27},
        {"q1": 0.1, "c": 0.27},
        {"q1": 0.1, "q2": 0.0, "c": 0.27, "unexpected": 1.0},
        {"q1": "not-a-number", "q2": 0.0, "c": 0.27},
    ],
)
def test_idle_flux_requires_exact_finite_numeric_mapping_before_materialization(idle_flux: Mapping[str, Any], tmp_path: Path):
    program, authorities = _registered_case(NONZERO_SOURCE)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    _assert_stable_qcis_reject(program, authorities=authorities, idle_flux=idle_flux)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before
    accepted = _compile(program, authorities=authorities)
    for name in ("q1_xy", "q2_xy", "q1_flux", "q2_flux", "c_flux"):
        assert np.isfinite(np.asarray(_artifact(accepted, name, f"logical_{name}"))).all()


@pytest.mark.parametrize(
    ("binding", "scan_values", "expected_code"),
    [
        ({}, None, "QCIS_BINDING_SET_MISMATCH"),
        ({"amplitude": _literal(0.125), "unexpected": _literal(0.25)}, None, "QCIS_BINDING_SET_MISMATCH"),
        ({"amplitude": {"literal": 0.125, "scan_ref": "drive_amplitude", "unit": "GHz"}}, None, None),
        ({"amplitude": _literal(0.125, "rad")}, None, "QCIS_BINDING_UNIT_MISMATCH"),
        ({"amplitude": _literal(True)}, None, None),
        ({"amplitude": _literal(float("nan"))}, None, None),
        ({"amplitude": _literal(float("inf"))}, None, None),
        ({"amplitude": _scan_ref("drive_amplitude")}, {}, None),
        ({"amplitude": _scan_ref("drive_amplitude")}, {"unexpected": {"value": 0.125, "unit": "GHz"}}, None),
        ({"amplitude": _scan_ref("drive_amplitude")}, {"drive_amplitude": {"value": 0.125, "unit": "rad"}}, "QCIS_BINDING_UNIT_MISMATCH"),
        ({"amplitude": _scan_ref("drive_amplitude")}, {"drive_amplitude": {"value": True, "unit": "GHz"}}, None),
        ({"amplitude": _scan_ref("drive_amplitude")}, {"drive_amplitude": {"value": float("nan"), "unit": "GHz"}}, None),
        ({"amplitude": _scan_ref("drive_amplitude")}, {"drive_amplitude": {"value": float("inf"), "unit": "GHz"}}, None),
    ],
)
def test_typed_binding_and_scan_ref_admission_fail_closed(
    binding: Mapping[str, Any], scan_values: Mapping[str, Any] | None, expected_code: str | None, tmp_path: Path
):
    program = _program(APPEND_TEMPLATE, template_id="append", bindings=binding)
    authorities = _authorities(include_all_agents=True)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    if expected_code is None:
        _assert_stable_qcis_reject(program, authorities=authorities, scan_values=scan_values)
    else:
        _assert_reject(program, expected_code, authorities=authorities, scan_values=scan_values)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


def test_structural_tstart_binding_rejects_before_any_artifact(tmp_path: Path):
    source = "PLSXY Q1 1 $start 1 0.125 5 0 0 1\n"
    bindings = {"start": {"unit": "samples", "occurrences": 1, "position": [0, "t_start"]}}
    program, authorities = _registered_case(source, bindings=bindings)
    program["bindings"] = {"start": _literal(0.0, "samples")}
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    _assert_reject(program, "QCIS_BINDING_POSITION_FORBIDDEN", authorities=authorities)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


def test_duplicate_compilation_is_deterministic_and_public_artifacts_are_deeply_immutable():
    first = _compile(_program(NONZERO_SOURCE, template_id="nonzero"), authorities=_authorities())
    second = _compile(_program(NONZERO_SOURCE, template_id="nonzero"), authorities=_authorities())
    for names in (("ast_bytes", "canonical_ast"), ("trace_bytes", "canonical_trace"), ("q1_xy", "logical_q1_xy")):
        assert _as_bytes(_artifact(first, *names)) == _as_bytes(_artifact(second, *names))
    array = _artifact(first, "q1_xy", "logical_q1_xy")
    with pytest.raises((TypeError, ValueError, RuntimeError)):
        array[0] = 0
    trace = _artifact(first, "trace", "provenance_trace")
    with pytest.raises((TypeError, AttributeError)):
        trace["final_sample_count"] = 0


def test_logical_effective_and_coefficient_tamper_verifiers_are_exposed_and_fail_closed():
    result = _compile(_program(NONZERO_SOURCE, template_id="nonzero"), authorities=_authorities())
    module = importlib.import_module(_compiler().__module__)
    checks = (
        ("verify_compilation", "QCIS_LOGICAL_WAVEFORM_HASH_MISMATCH", "q1_xy"),
        ("verify_effective_controls", "STAGE4_1_EFFECTIVE_CONTROL_HASH_MISMATCH", "effective_q1_xy"),
        ("verify_coefficient_inventory", "STAGE5_1_COEFFICIENT_INVENTORY_MISMATCH", "coefficient_inventory_bytes"),
    )
    for name, code, artifact_name in checks:
        verifier = getattr(module, name, None)
        assert callable(verifier), f"Stage 7.0 must expose {name} for the frozen tamper oracle"
        value = _artifact(result, artifact_name)
        if isinstance(value, bytes):
            corrupted = bytes([value[0] ^ 1]) + value[1:]
        else:
            corrupted = np.ascontiguousarray(np.asarray(value)).copy()
            corrupted.view(np.uint8)[0] ^= 1
        with pytest.raises(Exception) as captured:
            verifier(result, corrupted)
        assert code in _failure_code(captured.value)


_HANDOFF_CHANNELS = ("q1_xy", "q2_xy", "q1_flux", "q2_flux", "c_flux")


def _identity_handoff(result: Any, *, effective: bool) -> dict[str, np.ndarray]:
    """The identity-electronics fixture must preserve all five logical lanes."""

    return {
        "q1_xy": np.asarray(_artifact(result, "effective_q1_xy") if effective else _artifact(result, "q1_xy", "logical_q1_xy")).copy(),
        "q2_xy": np.asarray(_artifact(result, "effective_q2_xy") if effective else _artifact(result, "q2_xy", "logical_q2_xy")).copy(),
        "q1_flux": np.asarray(_artifact(result, "q1_flux", "logical_q1_flux")).copy(),
        "q2_flux": np.asarray(_artifact(result, "q2_flux", "logical_q2_flux")).copy(),
        "c_flux": np.asarray(_artifact(result, "c_flux", "logical_c_flux")).copy(),
    }


def _mutate_handoff_array(value: np.ndarray, mutation: str) -> np.ndarray:
    if mutation == "bit":
        result = value.copy()
        result.view(np.uint8)[0] ^= 1
        return result
    if mutation == "shape":
        return value.reshape((value.shape[0], 1))
    if mutation == "dtype":
        return value.astype(">c16" if np.issubdtype(value.dtype, np.complexfloating) else ">f8")
    if mutation == "length":
        return value[:-1]
    raise AssertionError(f"unknown mutation {mutation!r}")


def _assert_handoff_reject(result: Any, verifier_name: str, expected_code: str, channel: str, mutation: str) -> None:
    module = importlib.import_module(_compiler().__module__)
    verifier = getattr(module, verifier_name, None)
    assert callable(verifier), f"Stage 7.0 must expose {verifier_name} for five-lane handoff verification"
    candidate = _identity_handoff(result, effective=verifier_name == "verify_effective_controls")
    candidate[channel] = _mutate_handoff_array(candidate[channel], mutation)
    with pytest.raises(Exception) as captured:
        verifier(result, candidate)
    assert expected_code in _failure_code(captured.value)


@pytest.mark.parametrize("channel", _HANDOFF_CHANNELS)
def test_logical_handoff_rejects_single_bit_tamper_on_every_lane(channel: str):
    result = _compile(_program(RECTANGLE_SOURCE, template_id="rectangle"), authorities=_authorities(include_all_agents=True))
    _assert_handoff_reject(result, "verify_compilation", "QCIS_LOGICAL_WAVEFORM_HASH_MISMATCH", channel, "bit")


@pytest.mark.parametrize("channel", _HANDOFF_CHANNELS)
def test_identity_effective_handoff_rejects_single_bit_tamper_on_every_lane(channel: str):
    result = _compile(_program(RECTANGLE_SOURCE, template_id="rectangle"), authorities=_authorities(include_all_agents=True))
    _assert_handoff_reject(result, "verify_effective_controls", "STAGE4_1_EFFECTIVE_CONTROL_HASH_MISMATCH", channel, "bit")


@pytest.mark.parametrize("channel", _HANDOFF_CHANNELS)
@pytest.mark.parametrize("mutation", ("shape", "dtype", "length"))
@pytest.mark.parametrize(
    ("verifier_name", "expected_code"),
    [
        ("verify_compilation", "QCIS_LOGICAL_WAVEFORM_HASH_MISMATCH"),
        ("verify_effective_controls", "STAGE4_1_EFFECTIVE_CONTROL_HASH_MISMATCH"),
    ],
)
def test_handoff_rejects_shape_dtype_and_length_changes(
    verifier_name: str, expected_code: str, mutation: str, channel: str
):
    result = _compile(_program(RECTANGLE_SOURCE, template_id="rectangle"), authorities=_authorities(include_all_agents=True))
    _assert_handoff_reject(result, verifier_name, expected_code, channel, mutation)
