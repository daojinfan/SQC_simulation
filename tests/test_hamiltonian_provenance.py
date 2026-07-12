import json
from pathlib import Path

from sqvm.hamiltonian import (
    canonical_json_bytes,
    raw_file_sha256,
    stage2_model_source_tree_sha256,
)


def test_raw_file_sha256_uses_unmodified_bytes(tmp_path):
    path = tmp_path / "raw.bin"
    path.write_bytes(b"abc\r\n\0")
    assert raw_file_sha256(path) == "A6035160A7AA6524AA8195B60B97F9ACD1828A59B78D4C14C3BC3F6CC18634A1"


def test_stage2_source_tree_digest_normative_vector(tmp_path):
    files = {
        "pyproject.toml": b"project\r\n",
        "src/sqvm/device/z.py": b"z\n",
        "src/sqvm/device/mu_\u03bc.py": b"mu\n",
        "src/sqvm/hamiltonian/a.py": b"a\0\n",
    }
    for relative, raw in files.items():
        path = tmp_path / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    assert stage2_model_source_tree_sha256(tmp_path) == (
        "3DEB24939791178BF986FDC6607B0669F118DBF6F33A42F9DE010EB11643422E"
    )


def test_canonical_json_bytes_are_sorted_utf8_lf_and_deterministic():
    left = {"z": 1, "unicode": "\u03bc", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "unicode": "\u03bc", "z": 1}
    encoded = canonical_json_bytes(left)
    assert encoded == canonical_json_bytes(right)
    assert encoded.startswith(b'{\n  "nested"')
    assert encoded.endswith(b"\n")
    assert b"\r" not in encoded
    assert json.loads(encoded) == left
