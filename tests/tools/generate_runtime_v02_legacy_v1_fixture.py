"""Generate the frozen Runtime v0.2/v1 evidence from its historical writer.

This script intentionally runs only the writer shipped by REQUIRED_COMMIT.  The
current runtime modules are never imported.  UUIDs, clocks, host/PID,
environment snapshot, output-relative paths, and invocation order are pinned
below so the evidence bytes can be reproduced on the locked Windows baseline.
The catalog SQLite database is a derived cache, not a run evidence artifact;
it is removed after the old runner succeeds because SQLite embeds mutable
storage metadata that is outside the runtime manifest contract.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


REQUIRED_COMMIT = "8e054797227ed58a53ec80a1e35df5ac205de032"
SOURCE_COMPILER_BLOB = "9735d9c082def0053819a0689f60c14f1d065d66"
SOURCE_COMPILER_SHA256 = "7E94B3B7D24EDA24AA720D532754F4ED564B9D8E700B2B125BC59522E01ACB23"
GENERATOR_PATH = "tests/tools/generate_runtime_v02_legacy_v1_fixture.py"
TERMINAL_RUN_ID = "11111111-1111-4111-8111-111111111111"
INTERRUPTED_RUN_ID = "22222222-2222-4222-8222-222222222222"
FIXED_UTC_START = "2026-07-16T00:00:00.000000Z"
FIXED_HOST = "runtime-v02-legacy-fixture"
FIXED_PROCESS_ID = 4242
FIXED_ENVIRONMENT = {
    "schema_version": "0.1",
    "python": {"implementation": "CPython", "version": "3.12.10"},
    "platform": {"system": "Windows", "release": "10", "machine": "AMD64", "architecture": "64bit"},
    "packages": {
        "PyYAML": "6.0.3", "matplotlib": "3.10.9", "nbformat": "5.10.4", "numpy": "2.4.6",
        "pytest": "9.0.3", "qutip": "5.3.0", "scipy": "1.17.1",
    },
    "blas_threading": [],
}


class GenerationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_extract_archive(data: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise GenerationError(f"unsafe archive member: {member.name}")
            target = (destination / name).resolve()
            if not _is_relative_to(target, destination.resolve()):
                raise GenerationError(f"archive member escapes destination: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source:
                    if source is None:
                        raise GenerationError(f"archive member has no payload: {member.name}")
                    with target.open("xb") as output:
                        shutil.copyfileobj(source, output)
            else:
                raise GenerationError(f"unsupported archive member type: {member.name}")


def _manifest(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "provenance.json":
            continue
        entries.append({"path": relative, "byte_length": path.stat().st_size, "raw_sha256": _sha256(path)})
    return entries


def _compare_trees(actual: Path, expected: Path) -> None:
    actual_manifest = _manifest(actual)
    expected_manifest = _manifest(expected)
    if actual_manifest != expected_manifest:
        raise GenerationError("generated evidence does not byte-match --verify-against fixture")


def _validate_fixed_environment() -> None:
    observed = {
        "schema_version": "0.1",
        "python": {"implementation": platform.python_implementation(), "version": platform.python_version()},
        "platform": {
            "system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "architecture": platform.architecture()[0],
        },
        "packages": {name: metadata.version(name) for name in FIXED_ENVIRONMENT["packages"]},
        "blas_threading": [],
    }
    if observed != FIXED_ENVIRONMENT:
        raise GenerationError("installed environment does not match the fixture's fixed baseline")


def _driver_source() -> str:
    environment = repr(FIXED_ENVIRONMENT)
    return f'''from __future__ import annotations
import sys
from pathlib import Path

archive_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(archive_root / "src"))

from sqvm.runtime_v02 import runner
from sqvm.runtime import journal, lifecycle, storage

FIXED_ENVIRONMENT = {environment}
TERMINAL_RUN_ID = {TERMINAL_RUN_ID!r}
INTERRUPTED_RUN_ID = {INTERRUPTED_RUN_ID!r}
FIXED_HOST = {FIXED_HOST!r}
FIXED_PROCESS_ID = {FIXED_PROCESS_ID!r}

class Counter:
    def __init__(self):
        self.value = 0
    def next_utc(self):
        value = self.value
        self.value += 1
        return f"2026-07-16T00:00:{{value:02d}}.000000Z"
    def monotonic(self):
        self.value += 1
        return 1_000.0 + self.value / 1000.0
    def monotonic_ns(self):
        self.value += 1
        return 1_000_000_000_000 + self.value

def install(run_id):
    counter = Counter()
    runner.uuid.uuid4 = lambda: __import__("uuid").UUID(run_id)
    runner.utc_now_text = counter.next_utc
    journal.utc_now_text = counter.next_utc
    storage.utc_now_text = counter.next_utc
    runner.time.monotonic = counter.monotonic
    journal.time.monotonic_ns = counter.monotonic_ns
    lifecycle.monotonic = counter.monotonic
    storage.socket.gethostname = lambda: FIXED_HOST
    storage.os.getpid = lambda: FIXED_PROCESS_ID
    runner.build_environment_snapshot = lambda: FIXED_ENVIRONMENT

config = Path("configs/experiments/platform_qcis_compile_smoke_v1.yaml")
install(TERMINAL_RUN_ID)
terminal = runner.run_experiment_v02(config, "tests/fixtures/runtime_v02_legacy_v1/terminal-output", archive_root)
(archive_root / "tests/fixtures/runtime_v02_legacy_v1/terminal-output/catalog_v02.sqlite").unlink()

install(INTERRUPTED_RUN_ID)
runner.atomic_publish = lambda staging, target: (_ for _ in ()).throw(OSError("fixed atomic publish failure"))
try:
    runner.run_experiment_v02(config, "tests/fixtures/runtime_v02_legacy_v1/interrupted-output", archive_root)
except OSError as exc:
    if str(exc) != "fixed atomic publish failure":
        raise
else:
    raise AssertionError("atomic publication failure was not injected")
'''


def _write_provenance(target: Path, repository_root: Path) -> None:
    files = _manifest(target)
    encoded = json.dumps(files, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    script_path = Path(__file__).resolve()
    provenance = {
        "schema_version": "0.2",
        "artifact_type": "runtime_v02_legacy_v1_fixture_provenance",
        "source_commit": REQUIRED_COMMIT,
        "source_compiler_blob": SOURCE_COMPILER_BLOB,
        "source_compiler_sha256": SOURCE_COMPILER_SHA256,
        "generator_path": GENERATOR_PATH,
        "generator_raw_sha256": _sha256(script_path),
        "prerequisites": {
            "python": "CPython 3.12.10",
            "git": "git executable with required commit available",
            "commit": REQUIRED_COMMIT,
            "environment": FIXED_ENVIRONMENT,
        },
        "fixed_inputs": {
            "terminal_run_id": TERMINAL_RUN_ID,
            "interrupted_run_id": INTERRUPTED_RUN_ID,
            "utc_start": FIXED_UTC_START,
            "host": FIXED_HOST,
            "process_id": FIXED_PROCESS_ID,
            "terminal_output_relative": "tests/fixtures/runtime_v02_legacy_v1/terminal-output",
            "interrupted_output_relative": "tests/fixtures/runtime_v02_legacy_v1/interrupted-output",
            "generation_order": ["terminal", "interrupted_atomic_publish_failure"],
            "excluded_derived_files": ["terminal-output/catalog_v02.sqlite"],
        },
        "reproduction_command": (
            "python tests/tools/generate_runtime_v02_legacy_v1_fixture.py --repository-root . "
            "--target D:/sqvm_runtime_v02_legacy_v1_generated "
            "--verify-against tests/fixtures/runtime_v02_legacy_v1"
        ),
        "file_count": len(files),
        "file_manifest_encoding": "UTF-8 canonical JSON over sorted file entries, provenance excluded",
        "file_manifest_aggregate_sha256": hashlib.sha256(encoded).hexdigest().upper(),
        "files": files,
    }
    (target / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _run(repository_root: Path, target: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(repository_root), "archive", "--format=tar", REQUIRED_COMMIT],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if archive.returncode:
        raise GenerationError(archive.stderr.decode("utf-8", "replace").strip())
    temp_root = Path(tempfile.mkdtemp(prefix="sqc-v02-legacy-", dir=target.parent))
    try:
        archive_root = temp_root / "archive"
        _safe_extract_archive(archive.stdout, archive_root)
        if _sha256(archive_root / "src/sqvm/qcis/compiler.py") != SOURCE_COMPILER_SHA256:
            raise GenerationError("archived compiler.py does not match the immutable v1 source SHA-256")
        driver = temp_root / "run_historical_writer.py"
        driver.write_text(_driver_source(), encoding="utf-8", newline="\n")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(archive_root / "src")
        environment["PYTHONHASHSEED"] = "0"
        completed = subprocess.run(
            [sys.executable, str(driver), str(archive_root)], cwd=archive_root, env=environment,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if completed.returncode:
            raise GenerationError(completed.stderr or completed.stdout)
        generated = archive_root / "tests/fixtures/runtime_v02_legacy_v1"
        shutil.copytree(generated, target)
        _write_provenance(target, repository_root)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--verify-against", type=Path)
    args = parser.parse_args()
    repository_root = args.repository_root.resolve()
    target = args.target.resolve()
    if sys.implementation.name != "cpython" or sys.version_info[:3] != (3, 12, 10):
        raise GenerationError("requires CPython 3.12.10")
    _validate_fixed_environment()
    if not (repository_root / ".git").exists():
        raise GenerationError("--repository-root must be a git working tree")
    if target.exists():
        raise GenerationError("--target must not already exist")
    if _is_relative_to(target, repository_root):
        raise GenerationError("--target must be outside --repository-root (including output)")
    if not target.parent.exists():
        raise GenerationError("--target parent must already exist")
    if args.verify_against is not None:
        expected = args.verify_against.resolve()
        if not expected.is_dir():
            raise GenerationError("--verify-against must be an existing fixture directory")
    else:
        expected = None
    try:
        _run(repository_root, target)
        if expected is not None:
            _compare_trees(target, expected)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    print(f"generated {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
