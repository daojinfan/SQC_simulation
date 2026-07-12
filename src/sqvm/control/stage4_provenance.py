"""Stage 4 source and environment provenance."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
from pathlib import Path
import sys

from sqvm.hamiltonian.provenance import canonical_json_bytes


def stage4_source_tree_sha256(repository_root: str | Path) -> str:
    root = Path(repository_root).resolve()
    paths = list((root / "src" / "sqvm" / "control").rglob("*.py"))
    paths.extend((root / relative) for relative in (
        "src/sqvm/__main__.py",
        "scripts/run_stage_04_control_signal.py",
        "scripts/run_stage_04_control_signal_smoke.py",
    ))
    if any(not path.is_file() for path in paths):
        raise ValueError("Stage 4 source set is incomplete")
    digest = hashlib.sha256()
    rows = sorted(((path.relative_to(root).as_posix(), path) for path in paths), key=lambda row: row[0].encode("utf-8"))
    for relative, path in rows:
        raw = path.read_bytes()
        digest.update(relative.encode("utf-8")); digest.update(b"\0")
        digest.update(str(len(raw)).encode("ascii")); digest.update(b"\0"); digest.update(raw)
    return digest.hexdigest().upper()


def stage4_environment_fingerprint() -> tuple[dict[str, str], str]:
    try:
        packages = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "PyYAML", "nbformat", "nbclient")}
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError(f"Stage 4 environment package is missing: {exc.name}") from exc
    except Exception as exc:
        raise ValueError("Stage 4 environment metadata lookup failed") from exc
    if packages["nbclient"] != "0.10.2":
        raise ValueError("Stage 4 requires nbclient==0.10.2")
    payload = {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "numpy_version": packages["numpy"],
        "scipy_version": packages["scipy"],
        "pyyaml_version": packages["PyYAML"],
        "nbformat_version": packages["nbformat"],
        "nbclient_version": packages["nbclient"],
    }
    return payload, hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()
