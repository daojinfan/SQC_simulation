"""Fail closed when the executed physics suite differs from its count lock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def verify_physics_suite_count(lock_path: Path, junit_path: Path) -> int:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if set(lock) != {"schema_version", "primary_marker", "expected_testcases"}:
        raise ValueError("physics suite lock fields are invalid")
    if lock["schema_version"] != "0.1" or lock["primary_marker"] != "physics_slow":
        raise ValueError("physics suite lock identity is invalid")
    expected = lock["expected_testcases"]
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise ValueError("physics suite expected_testcases is invalid")
    actual = sum(1 for _ in ET.parse(junit_path).iter("testcase"))
    if actual != expected:
        raise ValueError(f"physics suite testcase count changed: expected {expected}, executed {actual}")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("junit", type=Path)
    args = parser.parse_args(argv)
    actual = verify_physics_suite_count(args.lock, args.junit)
    print(f"physics_slow testcases: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
