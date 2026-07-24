"""Verify the successor rebaseline closure without claiming historical replay."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.tools.generate_successor_rebaseline_fixture import validate_successor_fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--repository-root", type=Path)
    args = parser.parse_args(argv)
    chain = validate_successor_fixture(
        args.fixture.resolve(),
        args.repository_root.resolve() if args.repository_root is not None else None,
    )
    print(f"{chain['authority_id']}: {chain['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
