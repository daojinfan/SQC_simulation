"""Reliable publication boundary for long-running calibration workflows."""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable

from sqvm.runtime.storage import atomic_publish


_WINDOWS_TRANSIENT_RENAME_ERRORS = frozenset({5, 32, 33})
_MAX_ATTEMPTS = 40


def publish_calibration_directory(staging: str | Path, target: str | Path) -> None:
    """Atomically publish, retrying only transient Windows sharing failures."""

    _publish_with_retry(
        Path(staging),
        Path(target),
        publisher=atomic_publish,
        platform_name=os.name,
        sleeper=time.sleep,
    )


def _publish_with_retry(
    source: Path,
    destination: Path,
    *,
    publisher: Callable[[Path, Path], None],
    platform_name: str,
    sleeper: Callable[[float], None],
) -> None:
    delay_s = 0.02
    for attempt in range(_MAX_ATTEMPTS):
        try:
            publisher(source, destination)
            return
        except FileExistsError:
            raise
        except OSError as exc:
            error = getattr(exc, "winerror", None) or exc.errno
            retryable = (
                platform_name == "nt"
                and error in _WINDOWS_TRANSIENT_RENAME_ERRORS
                and attempt < _MAX_ATTEMPTS - 1
            )
            if not retryable:
                raise
            if destination.exists():
                raise FileExistsError(
                    f"calibration target already exists: {destination}"
                ) from exc
            if not source.is_dir():
                raise
            sleeper(delay_s)
            delay_s = min(delay_s * 2.0, 1.0)
