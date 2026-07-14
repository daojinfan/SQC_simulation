"""Stage 6 filesystem layout, locks, durability, and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import socket
import sys
from typing import Any, Mapping
import uuid

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.journal import utc_now_text


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    root: Path
    runs: Path
    staging: Path
    locks: Path
    resource_locks: Path
    recovery_locks: Path
    cancellation: Path
    quarantine: Path
    quarantine_records: Path
    resource_release_records: Path
    catalog: Path
    catalog_lock: Path


def validate_run_id(run_id: str) -> str:
    """Require the canonical lowercase UUID4 identity used by Stage 6."""

    if not isinstance(run_id, str):
        raise ValueError("run_id must be a canonical lowercase UUID4")
    try:
        parsed = uuid.UUID(run_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("run_id must be a canonical lowercase UUID4") from exc
    if parsed.version != 4 or str(parsed) != run_id:
        raise ValueError("run_id must be a canonical lowercase UUID4")
    return run_id


def initialize_layout(output_root: str | Path) -> RuntimeLayout:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    names = (
        "runs",
        "staging",
        "locks",
        "resource-locks",
        "recovery-locks",
        "cancellation",
        "quarantine",
        "quarantine-records",
        "resource-release-records",
    )
    paths = {}
    for name in names:
        path = root / name
        path.mkdir(exist_ok=True)
        if _is_link_or_junction(path) or not path.is_dir():
            raise ValueError(f"runtime layout path is unsafe: {name}")
        paths[name] = path
    if paths["runs"].stat().st_dev != paths["staging"].stat().st_dev:
        raise ValueError("runs and staging must be on the same volume")
    flush_directory(root)
    return RuntimeLayout(
        root,
        paths["runs"],
        paths["staging"],
        paths["locks"],
        paths["resource-locks"],
        paths["recovery-locks"],
        paths["cancellation"],
        paths["quarantine"],
        paths["quarantine-records"],
        paths["resource-release-records"],
        root / "catalog.sqlite",
        root / "catalog.lock",
    )


def resource_key(backend_id: str, device_snapshot_sha256: str) -> str:
    payload = {"backend_id": backend_id, "device_snapshot_sha256": device_snapshot_sha256}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


def create_run_locks(
    layout: RuntimeLayout,
    *,
    run_id: str,
    resource_key_value: str,
    request_sha256: str,
    environment_fingerprint: str,
) -> tuple[Path, Path]:
    payload = {
        "schema_version": "0.1",
        "run_id": run_id,
        "resource_key": resource_key_value,
        "created_utc": utc_now_text(),
        "host": socket.gethostname(),
        "process_id": os.getpid(),
        "request_sha256": request_sha256,
        "environment_fingerprint": environment_fingerprint,
    }
    run_lock = layout.locks / f"{run_id}.lock"
    resource_lock = layout.resource_locks / f"{resource_key_value}.lock"
    _write_exclusive(run_lock, canonical_json_bytes(payload))
    try:
        _write_exclusive(resource_lock, canonical_json_bytes(payload))
    except Exception:
        run_lock.unlink(missing_ok=True)
        flush_directory(layout.locks)
        raise
    return run_lock, resource_lock


def snapshot_locks(run_lock: Path, resource_lock: Path, snapshots_dir: Path) -> tuple[str, str]:
    snapshots_dir.mkdir(parents=True, exist_ok=False)
    run_raw = run_lock.read_bytes()
    resource_raw = resource_lock.read_bytes()
    _write_exclusive(snapshots_dir / "run_lock.json", run_raw)
    _write_exclusive(snapshots_dir / "resource_lock.json", resource_raw)
    return _sha(run_raw), _sha(resource_raw)


def write_canonical_new(path: str | Path, payload: Mapping[str, Any]) -> str:
    target = Path(path)
    raw = canonical_json_bytes(payload)
    _write_exclusive(target, raw)
    return _sha(raw)


def copy_new(source: str | Path, target: str | Path) -> str:
    raw = Path(source).read_bytes()
    _write_exclusive(Path(target), raw)
    return _sha(raw)


def atomic_publish(staging: str | Path, target: str | Path) -> None:
    source = Path(staging)
    destination = Path(target)
    if not source.is_dir() or _is_link_or_junction(source):
        raise ValueError("staging directory is missing or unsafe")
    if destination.exists():
        raise FileExistsError(f"run target already exists: {destination}")
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError("staging and target are on different volumes")
    flush_tree(source)
    _rename_directory_no_replace(source, destination)
    flush_directory(destination.parent)
    flush_directory(destination.parent.parent)


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename a directory while requiring an absent destination."""

    if os.name == "nt":
        _move_file_windows_no_replace(source, destination)
        return
    if sys.platform.startswith("linux"):
        _renameat2_linux_no_replace(source, destination)
        return
    if sys.platform == "darwin":
        _renamex_macos_no_replace(source, destination)
        return
    raise OSError(f"atomic no-replace directory rename is unsupported on {sys.platform}")


def flush_tree(root: str | Path) -> None:
    path = Path(root)
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if _is_link_or_junction(child):
            raise ValueError("publication tree cannot contain symlinks")
        if child.is_file():
            with child.open("rb+") as stream:
                os.fsync(stream.fileno())
        elif child.is_dir():
            flush_directory(child)
    flush_directory(path)


def flush_directory(path: str | Path) -> None:
    directory = Path(path)
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    _flush_windows_directory(directory)


def inventory_tree(root: str | Path) -> list[dict[str, Any]]:
    base = Path(root)
    rows = []
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        if _is_link_or_junction(path):
            raise ValueError("inventory cannot contain symlinks")
        if path.is_file():
            raw = path.read_bytes()
            rows.append(
                {
                    "path": path.relative_to(base).as_posix(),
                    "byte_length": len(raw),
                    "raw_sha256": _sha(raw),
                }
            )
    return rows


def inventory_tree_no_follow(root: str | Path) -> list[dict[str, Any]]:
    """Inventory malformed trees without following symlinks or reparse points."""

    base = Path(root)
    if _is_link_or_junction(base):
        return [{"path": ".", "entry_type": "link", "link_target": _link_target(base)}]
    rows: list[dict[str, Any]] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative = path.relative_to(base).as_posix()
                if entry.is_symlink() or _is_link_or_junction(path):
                    rows.append({"path": relative, "entry_type": "link", "link_target": _link_target(path)})
                elif entry.is_dir(follow_symlinks=False):
                    rows.append({"path": relative, "entry_type": "directory"})
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    raw = path.read_bytes()
                    rows.append({"path": relative, "entry_type": "file", "byte_length": len(raw), "raw_sha256": _sha(raw)})
                else:
                    rows.append({"path": relative, "entry_type": "other"})

    visit(base)
    return rows


def remove_resource_lock(resource_lock: str | Path) -> None:
    path = Path(resource_lock)
    path.unlink()
    flush_directory(path.parent)


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    flush_directory(path.parent)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _link_target(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return str(path.resolve())


def _flush_windows_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0xC0000000, 0x00000007, None, 3, 0x02000000, None)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        raise OSError(ctypes.get_last_error(), f"cannot open directory for durability flush: {path}")
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), f"cannot flush directory: {path}")
    finally:
        kernel32.CloseHandle(handle)


def _move_file_windows_no_replace(source: Path, destination: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    if not move_file(str(source), str(destination), 0):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError(error, f"run target already exists: {destination}", str(destination))
        raise OSError(error, f"cannot atomically publish directory: {source}")


def _renameat2_linux_no_replace(source: Path, destination: Path) -> None:
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is required for atomic no-replace publication")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, f"run target already exists: {destination}", str(destination))
        raise OSError(error, os.strerror(error), str(source))


def _renamex_macos_no_replace(source: Path, destination: Path) -> None:
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    renamex = getattr(libc, "renamex_np", None)
    if renamex is None:
        raise OSError(errno.ENOSYS, "renamex_np is required for atomic no-replace publication")
    renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex.restype = ctypes.c_int
    if renamex(os.fsencode(source), os.fsencode(destination), 0x00000004) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, f"run target already exists: {destination}", str(destination))
        raise OSError(error, os.strerror(error), str(source))
