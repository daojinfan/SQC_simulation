"""VS Code entry point for the formal Stage 4 control-signal candidate."""

from pathlib import Path

from sqvm.control import verify_control_signal


def main() -> int:
    receipt = verify_control_signal(
        Path("configs/control/2q1c2r_control.yaml"),
        Path("configs/control/2q1c2r_control_demo.yaml"),
        Path("output/stage_04_control_signal"),
    )
    return 0 if receipt.acceptance_candidate_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
