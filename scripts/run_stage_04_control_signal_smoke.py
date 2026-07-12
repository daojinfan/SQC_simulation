"""VS Code entry point for the non-acceptance Stage 4 smoke profile."""

from pathlib import Path

from sqvm.control import verify_control_signal


def main() -> int:
    receipt = verify_control_signal(
        Path("configs/control/2q1c2r_control_smoke.yaml"),
        Path("configs/control/2q1c2r_control_demo_smoke.yaml"),
        Path("output/stage_04_control_signal_smoke"),
    )
    return 0 if receipt.execution_succeeded and not receipt.to_dict()["blocking_reasons"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
