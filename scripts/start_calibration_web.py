from __future__ import annotations

import argparse
from pathlib import Path
import sys
from threading import Thread
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqvm.web import create_calibration_web_server


def _service_is_running(url: str) -> bool:
    try:
        with urlopen(f"{url}/api/v1/health", timeout=0.8) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _open_browser_later(url: str) -> None:
    time.sleep(0.4)
    webbrowser.open(url)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="启动本地 SQVM 校准控制台，并默认在浏览器中打开页面。",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（仅支持本机）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--output-root", type=Path, default=None, help="实验结果目录")
    parser.add_argument(
        "--configuration-storage-root",
        type=Path,
        default=None,
        help="配置管理数据目录",
    )
    parser.add_argument("--experiment-hot-root", type=Path, default=None, help="已发布实验目录")
    parser.add_argument("--experiment-storage-root", type=Path, default=None, help="实验存储目录")
    parser.add_argument("--experiment-archive-root", type=Path, default=None, help="受信归档目录（可位于另一块本地磁盘）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser


def main() -> int:
    args = _parser().parse_args()
    requested_url = f"http://{args.host}:{args.port}"

    if args.port != 0 and _service_is_running(requested_url):
        print(f"SQVM 校准控制台已在运行：{requested_url}", flush=True)
        if not args.no_browser:
            webbrowser.open(requested_url)
        return 0

    try:
        server = create_calibration_web_server(
            ROOT,
            output_root=args.output_root,
            configuration_storage_root=args.configuration_storage_root,
            experiment_hot_root=args.experiment_hot_root,
            experiment_storage_root=args.experiment_storage_root,
            experiment_archive_root=args.experiment_archive_root,
            host=args.host,
            port=args.port,
        )
    except OSError as exc:
        print(f"无法启动 SQVM 校准控制台：{exc}", file=sys.stderr)
        print(
            f"端口 {args.port} 可能已被其他程序占用，可使用 --port 指定其他端口。",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"启动参数无效：{exc}", file=sys.stderr)
        return 2

    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}"
    print(f"SQVM 校准控制台已启动：{url}", flush=True)
    print("保持此窗口打开；按 Ctrl+C 停止服务。", flush=True)

    if not args.no_browser:
        Thread(target=_open_browser_later, args=(url,), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 SQVM 校准控制台...", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
