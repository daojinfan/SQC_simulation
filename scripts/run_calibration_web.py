from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqvm.web import serve_calibration_web


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local SQVM calibration console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--configuration-storage-root", type=Path, default=None)
    parser.add_argument("--experiment-hot-root", type=Path, default=None)
    parser.add_argument("--experiment-storage-root", type=Path, default=None)
    parser.add_argument("--experiment-archive-root", type=Path, default=None)
    args = parser.parse_args()
    print(f"SQVM calibration console: http://{args.host}:{args.port}", flush=True)
    serve_calibration_web(
        ROOT,
        output_root=args.output_root,
        configuration_storage_root=args.configuration_storage_root,
        experiment_hot_root=args.experiment_hot_root,
        experiment_storage_root=args.experiment_storage_root,
        experiment_archive_root=args.experiment_archive_root,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
