from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import ctypes
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

import pytest

import sqvm.storage.inventory as inventory_module
from sqvm.storage.errors import StorageInventoryError
from sqvm.storage.inventory import cluster_round_up, inventory_tree, volume_usage


def test_cluster_rounding_is_exact_at_boundaries() -> None:
    assert cluster_round_up(0, 4096) == 0
    assert cluster_round_up(1, 4096) == 4096
    assert cluster_round_up(4096, 4096) == 4096
    assert cluster_round_up(4097, 4096) == 8192


def test_empty_tree_has_zero_logical_and_allocated_bytes(tmp_path: Path) -> None:
    report = inventory_tree(tmp_path, confinement_root=tmp_path)

    assert report.file_count == 0
    assert report.logical_bytes == 0
    assert report.allocated_bytes == 0


def test_small_and_large_files_report_logical_and_allocated_bytes(tmp_path: Path) -> None:
    (tmp_path / "small.bin").write_bytes(b"x")
    (tmp_path / "large.bin").write_bytes(b"y" * 8193)

    report = inventory_tree(tmp_path, confinement_root=tmp_path)

    assert report.file_count == 2
    assert report.logical_bytes == 8194
    assert report.allocated_bytes >= report.logical_bytes
    assert len(report.files) == 2


def test_inventory_marks_windows_cluster_fallback_as_estimated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "item.bin").write_bytes(b"x")
    monkeypatch.setattr("sqvm.storage.inventory._is_windows", lambda: True)
    monkeypatch.setattr("sqvm.storage.inventory._windows_allocated_bytes", lambda path: None)
    monkeypatch.setattr("sqvm.storage.inventory._windows_cluster_size", lambda path: 4096)

    report = inventory_tree(tmp_path, confinement_root=tmp_path)

    assert report.allocated_bytes == 4096
    assert report.allocated_estimated is True


def test_inventory_prefers_windows_actual_allocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "item.bin").write_bytes(b"x")
    monkeypatch.setattr("sqvm.storage.inventory._is_windows", lambda: True)
    monkeypatch.setattr("sqvm.storage.inventory._windows_allocated_bytes", lambda path: 8192)

    report = inventory_tree(tmp_path, confinement_root=tmp_path)

    assert report.allocated_bytes == 8192
    assert report.allocated_estimated is False


def test_inventory_rejects_symlink_and_does_not_follow_outside(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"outside")
    link = tmp_path / "escape.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(StorageInventoryError, match="linked or reparse"):
        inventory_tree(tmp_path, confinement_root=tmp_path)


def test_inventory_rejects_symlinked_ancestor_that_escapes_confinement(tmp_path: Path) -> None:
    bound = tmp_path / "bound"
    bound.mkdir()
    outside_child = tmp_path / "outside" / "child"
    outside_child.mkdir(parents=True)
    (outside_child / "evidence.bin").write_bytes(b"outside")
    ancestor_link = bound / "link"
    try:
        ancestor_link.symlink_to(outside_child.parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this host")

    with pytest.raises(StorageInventoryError, match="linked or reparse"):
        inventory_tree(ancestor_link / "child", confinement_root=bound)


def test_inventory_rejects_reparse_marked_ancestor_before_descending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = tmp_path / "bound"
    child = bound / "ancestor" / "child"
    child.mkdir(parents=True)
    (child / "evidence.bin").write_bytes(b"evidence")
    ancestor = bound / "ancestor"
    original = __import__("sqvm.storage.inventory", fromlist=["_is_link_or_reparse"])._is_link_or_reparse

    def flagged(path: Path, info: os.stat_result) -> bool:
        return path == ancestor or original(path, info)

    monkeypatch.setattr("sqvm.storage.inventory._is_link_or_reparse", flagged)

    with pytest.raises(StorageInventoryError, match="linked or reparse"):
        inventory_tree(child, confinement_root=bound)


def test_inventory_fails_closed_when_reparse_probe_flags_regular_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "reparse.bin"
    candidate.write_bytes(b"evidence")
    original = __import__("sqvm.storage.inventory", fromlist=["_is_link_or_reparse"])._is_link_or_reparse

    def flagged(path: Path, info: os.stat_result) -> bool:
        return path == candidate or original(path, info)

    monkeypatch.setattr("sqvm.storage.inventory._is_link_or_reparse", flagged)

    with pytest.raises(StorageInventoryError, match="linked or reparse"):
        inventory_tree(tmp_path, confinement_root=tmp_path)


def test_inventory_rejects_root_outside_confinement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    with pytest.raises(StorageInventoryError, match="outside confinement"):
        inventory_tree(root, confinement_root=other)


def test_inventory_fails_closed_on_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.scandir

    def denied(path: object):
        if Path(path) == tmp_path:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr("sqvm.storage.inventory.os.scandir", denied)
    with pytest.raises(StorageInventoryError, match="cannot enumerate"):
        inventory_tree(tmp_path, confinement_root=tmp_path)


def test_volume_usage_reports_logical_volume_values(tmp_path: Path) -> None:
    usage = volume_usage(tmp_path, confinement_root=tmp_path)

    assert usage.total_bytes > 0
    assert 0 <= usage.free_bytes <= usage.total_bytes


@pytest.mark.parametrize("byte_length,cluster_size", [(-1, 1), (1, 0), (True, 4096), (1, True)])
def test_cluster_rounding_rejects_invalid_public_arguments(byte_length: object, cluster_size: object) -> None:
    with pytest.raises(StorageInventoryError):
        cluster_round_up(byte_length, cluster_size)  # type: ignore[arg-type]


def test_inventory_rejects_real_hardlink_and_stat_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.bin"
    first.write_bytes(b"evidence")
    linked = tmp_path / "linked.bin"
    os.link(first, linked)
    with pytest.raises(StorageInventoryError, match="hard-linked"):
        inventory_tree(tmp_path, confinement_root=tmp_path)

    linked.unlink()
    original_lstat = Path.lstat
    def denied_lstat(path: Path):
        if path == first:
            raise PermissionError("lstat denied")
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", denied_lstat)
    with pytest.raises(StorageInventoryError, match="cannot inspect inventory entry"):
        inventory_tree(tmp_path, confinement_root=tmp_path)


def test_volume_usage_fails_closed_for_os_error_and_invalid_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(StorageInventoryError, match="cannot inspect"):
        volume_usage(tmp_path, confinement_root=tmp_path)

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(100, 1, 101))
    with pytest.raises(StorageInventoryError, match="invalid"):
        volume_usage(tmp_path, confinement_root=tmp_path)


def test_inventory_confinement_file_missing_nested_and_utf8_order(tmp_path: Path) -> None:
    file_root = tmp_path / "file-root"
    file_root.write_bytes(b"x")
    with pytest.raises(StorageInventoryError, match="not a directory"):
        inventory_tree(file_root, confinement_root=tmp_path)
    with pytest.raises(StorageInventoryError, match="missing or has incorrect case"):
        inventory_tree(tmp_path / "missing", confinement_root=tmp_path)

    nested = tmp_path / "nested" / "deep"
    nested.mkdir(parents=True)
    (tmp_path / "z.bin").write_bytes(b"z")
    (tmp_path / "a.bin").write_bytes(b"a")
    (nested / "é.bin").write_bytes(b"e")
    report = inventory_tree(tmp_path, confinement_root=tmp_path)
    assert [row.relative_path for row in report.files] == sorted(
        (row.relative_path for row in report.files), key=lambda value: value.encode("utf-8")
    )
    assert report.root == "." and report.file_count == 4


def test_volume_usage_relative_root_and_negative_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    child = tmp_path / "child"
    child.mkdir()
    assert volume_usage(child, confinement_root=tmp_path).root == "child"
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(-1, 0, 0))
    with pytest.raises(StorageInventoryError, match="invalid"):
        volume_usage(tmp_path, confinement_root=tmp_path)


def test_inventory_allocation_and_directory_os_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = tmp_path / "item.bin"
    item.write_bytes(b"payload")
    monkeypatch.setattr("sqvm.storage.inventory._is_windows", lambda: True)
    monkeypatch.setattr("sqvm.storage.inventory._windows_allocated_bytes", lambda _path: None)
    monkeypatch.setattr("sqvm.storage.inventory._windows_cluster_size", lambda _path: None)
    with pytest.raises(StorageInventoryError, match="allocation size"):
        inventory_tree(tmp_path, confinement_root=tmp_path)

    monkeypatch.setattr("sqvm.storage.inventory._windows_cluster_size", lambda _path: 4096)
    report = inventory_tree(tmp_path, confinement_root=tmp_path)
    assert report.allocated_estimated and report.allocated_bytes == 4096


def test_inventory_rejects_special_entry_and_intermediate_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    special = tmp_path / "special.bin"
    special.write_bytes(b"payload")
    original_lstat = Path.lstat

    def special_lstat(path: Path):
        if path == special:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0, st_nlink=1)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", special_lstat)
    with pytest.raises(StorageInventoryError, match="special filesystem"):
        inventory_tree(tmp_path, confinement_root=tmp_path)

    monkeypatch.setattr(Path, "lstat", original_lstat)
    intermediate = tmp_path / "intermediate"
    intermediate.write_bytes(b"file")
    with pytest.raises(StorageInventoryError, match="component is not a directory"):
        inventory_tree(intermediate / "child", confinement_root=tmp_path)


def test_inventory_rejects_uninspectable_or_linked_volume_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = Path(tmp_path.anchor)
    original_lstat = Path.lstat

    def denied(path: Path):
        if path == anchor:
            raise PermissionError("anchor denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(StorageInventoryError, match="cannot be inspected"):
        volume_usage(tmp_path, confinement_root=tmp_path)

    monkeypatch.setattr(Path, "lstat", original_lstat)
    monkeypatch.setattr(inventory_module, "_is_link_or_reparse", lambda path, info: path == anchor)
    with pytest.raises(StorageInventoryError, match="linked or reparse"):
        volume_usage(tmp_path, confinement_root=tmp_path)


def test_platform_allocation_adapters_cover_success_and_error_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NativeCall:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def compressed_success(_path, high):
        high._obj.value = 2
        return 3

    kernel32 = SimpleNamespace(
        GetCompressedFileSizeW=NativeCall(compressed_success),
        GetDiskFreeSpaceW=NativeCall(lambda _root, sectors, bytes_per_sector, _free, _total: (
            setattr(sectors._obj, "value", 8), setattr(bytes_per_sector._obj, "value", 512), 1
        )[-1]),
    )
    monkeypatch.setattr(inventory_module, "_is_windows", lambda: True)
    monkeypatch.setattr(inventory_module.ctypes, "windll", SimpleNamespace(kernel32=kernel32))
    monkeypatch.setattr(ctypes, "set_last_error", lambda _value: None)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0)
    assert inventory_module._windows_allocated_bytes(tmp_path / "item.bin") == (2 << 32) | 3
    assert inventory_module._windows_cluster_size(tmp_path) == 4096

    kernel32.GetCompressedFileSizeW = NativeCall(lambda _path, _high: 0xFFFFFFFF)
    kernel32.GetDiskFreeSpaceW = NativeCall(lambda *_args: 0)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    assert inventory_module._windows_allocated_bytes(tmp_path / "item.bin") is None
    assert inventory_module._windows_cluster_size(tmp_path) is None

    monkeypatch.setattr(inventory_module, "_is_windows", lambda: False)
    assert inventory_module._windows_allocated_bytes(tmp_path / "item.bin") is None
    assert inventory_module._windows_cluster_size(tmp_path) is None
    assert inventory_module._allocated_bytes(
        tmp_path / "item.bin", SimpleNamespace(st_blocks=3, st_size=1), None
    ) == (1536, False)
    with pytest.raises(StorageInventoryError, match="POSIX allocation"):
        inventory_module._allocated_bytes(
            tmp_path / "item.bin", SimpleNamespace(st_blocks=-1, st_size=1), None
        )
