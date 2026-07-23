from __future__ import annotations

from pathlib import Path

import pytest

from sqvm.runtime.publication import _publish_with_retry


def test_calibration_publication_retries_transient_windows_lock(tmp_path: Path):
    staging = tmp_path / "staging"
    target = tmp_path / "published"
    staging.mkdir()
    (staging / "payload.bin").write_bytes(b"payload")
    attempts = []
    delays = []

    def transient_publish(source: Path, destination: Path) -> None:
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise OSError(5, "temporarily locked")
        source.rename(destination)

    _publish_with_retry(
        staging,
        target,
        publisher=transient_publish,
        platform_name="nt",
        sleeper=delays.append,
    )

    assert len(attempts) == 3
    assert delays == [0.02, 0.04]
    assert not staging.exists()
    assert (target / "payload.bin").read_bytes() == b"payload"


@pytest.mark.parametrize("platform_name,error", (("posix", 5), ("nt", 87)))
def test_calibration_publication_does_not_retry_other_errors(
    tmp_path: Path, platform_name: str, error: int
):
    staging = tmp_path / "staging"
    target = tmp_path / "published"
    staging.mkdir()
    attempts = []

    def failed_publish(_source: Path, _destination: Path) -> None:
        attempts.append(True)
        raise OSError(error, "publication failed")

    with pytest.raises(OSError) as captured:
        _publish_with_retry(
            staging,
            target,
            publisher=failed_publish,
            platform_name=platform_name,
            sleeper=lambda _delay: None,
        )

    assert captured.value.errno == error
    assert attempts == [True]


def test_calibration_publication_preserves_no_replace_conflict(tmp_path: Path):
    staging = tmp_path / "staging"
    target = tmp_path / "published"
    staging.mkdir()
    target.mkdir()
    attempts = []

    def conflict(_source: Path, _destination: Path) -> None:
        attempts.append(True)
        raise FileExistsError("target exists")

    with pytest.raises(FileExistsError):
        _publish_with_retry(
            staging,
            target,
            publisher=conflict,
            platform_name="nt",
            sleeper=lambda _delay: None,
        )

    assert attempts == [True]
    assert staging.is_dir()
