"""Verify one or more deterministic fixture manifests."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support.fixture_loader import FIXTURES_ROOT, verify_fixture_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+", type=Path)
    args = parser.parse_args(argv)
    for root in args.fixtures:
        manifest = verify_fixture_manifest(root)
        print(f"{manifest['fixture_id']}: {manifest['aggregate_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
