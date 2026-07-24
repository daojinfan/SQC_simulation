#!/usr/bin/env python3
"""Fail closed when a selected test lock is not hash-installable."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("windows", "linux"), required=True)
    parser.add_argument("--install", action="store_true", help="perform pip's --require-hashes dry-run")
    arguments = parser.parse_args()
    expected_system = "Windows" if arguments.platform == "windows" else "Linux"
    if platform.system() != expected_system:
        raise SystemExit(f"refusing {arguments.platform} lock verification on {platform.system()}")
    lock = ROOT / f"requirements-test-py312-{arguments.platform}-lock.txt"
    subprocess.run([sys.executable, str(ROOT / "tools" / "generate_test_locks.py"), "--verify"], check=True)
    if arguments.install:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--require-hashes", "-r", str(lock)],
            check=True,
        )


if __name__ == "__main__":
    main()
