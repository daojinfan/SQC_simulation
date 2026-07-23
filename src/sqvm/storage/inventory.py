"""Read-only, no-follow filesystem inventory and volume accounting."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import stat
from typing import Iterator

from sqvm.storage.errors import StorageInventoryError
from sqvm.storage.models import StorageFile, StorageInventory, VolumeUsage


def cluster_round_up(byte_length: int, cluster_size: int) -> int:
    """Return the conservative allocation for a logical byte length."""

    if type(byte_length) is not int or byte_length < 0:
        raise StorageInventoryError("byte length is invalid")
    if type(cluster_size) is not int or cluster_size <= 0:
        raise StorageInventoryError("cluster size is invalid")
    return 0 if byte_length == 0 else ((byte_length + cluster_size - 1) // cluster_size) * cluster_size


def inventory_tree(root: str | Path, *, confinement_root: str | Path) -> StorageInventory:
    """Inventory regular files below a confined directory without following links."""

    directory, bound = _confined_directory(root, confinement_root)
    cluster_size = _windows_cluster_size(directory) if _is_windows() else None
    files: list[StorageFile] = []
    logical_bytes = 0
    allocated_bytes = 0
    allocated_estimated = False
    for path, info in _walk_regular_files(directory):
        logical = info.st_size
        allocated, estimated = _allocated_bytes(path, info, cluster_size)
        relative = path.relative_to(directory).as_posix()
        files.append(StorageFile(relative, logical, allocated, estimated))
        logical_bytes += logical
        allocated_bytes += allocated
        allocated_estimated = allocated_estimated or estimated
    return StorageInventory(
        root=directory.relative_to(bound).as_posix() or ".",
        files=tuple(files),
        logical_bytes=logical_bytes,
        allocated_bytes=allocated_bytes,
        allocated_estimated=allocated_estimated,
    )


def volume_usage(root: str | Path, *, confinement_root: str | Path) -> VolumeUsage:
    """Return actual free and total volume bytes for a safe, local root."""

    directory, bound = _confined_directory(root, confinement_root)
    try:
        usage = shutil.disk_usage(directory)
    except OSError as exc:
        raise StorageInventoryError("cannot inspect volume usage") from exc
    if usage.total < 0 or usage.free < 0 or usage.free > usage.total:
        raise StorageInventoryError("volume usage is invalid")
    return VolumeUsage(directory.relative_to(bound).as_posix() or ".", usage.free, usage.total)


def _confined_directory(root: str | Path, confinement_root: str | Path) -> tuple[Path, Path]:
    bound = _verified_directory_path(confinement_root, "confinement root")
    directory = _verified_directory_path(root, "inventory root")
    try:
        directory.relative_to(bound)
    except ValueError as exc:
        raise StorageInventoryError("inventory root is outside confinement root") from exc
    return directory, bound


def _walk_regular_files(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    def visit(directory: Path) -> Iterator[tuple[Path, os.stat_result]]:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.encode("utf-8"))
        except OSError as exc:
            raise StorageInventoryError("cannot enumerate inventory directory") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                # Windows DirEntry.stat can report st_nlink=0 for a hardlink.
                # lstat is the no-follow authoritative identity used for admission.
                info = path.lstat()
            except OSError as exc:
                raise StorageInventoryError("cannot inspect inventory entry") from exc
            if _is_link_or_reparse(path, info):
                raise StorageInventoryError("inventory contains a linked or reparse-backed entry")
            if stat.S_ISDIR(info.st_mode):
                yield from visit(path)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1:
                    raise StorageInventoryError("inventory contains a hard-linked file")
                yield path, info
            else:
                raise StorageInventoryError("inventory contains a special filesystem entry")

    yield from visit(root)


def _allocated_bytes(path: Path, info: os.stat_result, cluster_size: int | None) -> tuple[int, bool]:
    if _is_windows():
        actual = _windows_allocated_bytes(path)
        if actual is not None:
            return actual, False
        if cluster_size is None:
            raise StorageInventoryError("Windows allocation size is unavailable")
        return cluster_round_up(info.st_size, cluster_size), True
    blocks = getattr(info, "st_blocks", None)
    if type(blocks) is not int or blocks < 0:
        raise StorageInventoryError("POSIX allocation size is unavailable")
    return blocks * 512, False


def _verified_directory_path(value: str | Path, label: str) -> Path:
    """Validate every lexical component without resolving or following links."""

    path = Path(os.path.abspath(os.fspath(value)))
    anchor = Path(path.anchor)
    if not anchor.anchor:
        raise StorageInventoryError(f"{label} is not an absolute path")
    _assert_not_link_or_reparse(anchor, label)
    current = anchor
    parts = path.parts[1:]
    if not parts:
        return current
    for index, part in enumerate(parts):
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise StorageInventoryError(f"{label} component cannot be inspected") from exc
        matches = [entry for entry in entries if entry.name == part]
        if len(matches) != 1:
            raise StorageInventoryError(f"{label} component is missing or has incorrect case")
        entry = matches[0]
        candidate = current / entry.name
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise StorageInventoryError(f"{label} component cannot be inspected") from exc
        if _is_link_or_reparse(candidate, info):
            raise StorageInventoryError(f"{label} contains a linked or reparse-backed component")
        if not stat.S_ISDIR(info.st_mode):
            if index == len(parts) - 1:
                raise StorageInventoryError(f"{label} is not a directory")
            raise StorageInventoryError(f"{label} component is not a directory")
        current = candidate
    return current


def _assert_not_link_or_reparse(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StorageInventoryError(f"{label} cannot be inspected") from exc
    if _is_link_or_reparse(path, info):
        raise StorageInventoryError(f"{label} is linked or reparse-backed")


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        reparse and getattr(info, "st_file_attributes", 0) & reparse
    )


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_allocated_bytes(path: Path) -> int | None:
    if not _is_windows():
        return None
    kernel32 = ctypes.windll.kernel32
    get_size = kernel32.GetCompressedFileSizeW
    get_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    get_size.restype = ctypes.c_uint32
    high = ctypes.c_uint32(0)
    ctypes.set_last_error(0)
    low = get_size(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        return None
    return (high.value << 32) | low


def _windows_cluster_size(path: Path) -> int | None:
    if not _is_windows():
        return None
    root = Path(path).anchor or str(path)
    sectors = ctypes.c_uint32(0)
    bytes_per_sector = ctypes.c_uint32(0)
    free_clusters = ctypes.c_uint32(0)
    total_clusters = ctypes.c_uint32(0)
    result = ctypes.windll.kernel32.GetDiskFreeSpaceW(
        root,
        ctypes.byref(sectors),
        ctypes.byref(bytes_per_sector),
        ctypes.byref(free_clusters),
        ctypes.byref(total_clusters),
    )
    cluster_size = sectors.value * bytes_per_sector.value
    return cluster_size if result and cluster_size > 0 else None
