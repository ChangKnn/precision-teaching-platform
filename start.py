"""One-click local launcher for Windows, macOS and Linux (Python 3.11+)."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener
import venv
import webbrowser


ROOT = Path(__file__).resolve().parent


def environment_python(root: Path = ROOT) -> Path:
    relative = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return root / ".venv" / relative


def prepare_environment(root: Path = ROOT) -> Path:
    python = environment_python(root)
    if not python.is_file():
        print("首次启动：创建 Python 虚拟环境……", flush=True)
        venv.EnvBuilder(with_pip=True).create(root / ".venv")
    requirements = root / "requirements.txt"
    stamp = root / ".venv" / ".launcher-requirements.sha256"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not stamp.is_file() or stamp.read_text(encoding="utf-8") != fingerprint:
        print("安装／更新依赖（需要联网，首次运行可能需几分钟）……", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(requirements)],
            cwd=root, check=True,
        )
        stamp.write_text(fingerprint, encoding="utf-8")
    config = root / ".env"
    if not config.exists():
        shutil.copyfile(root / ".env.example", config)
        print("已创建 .env；默认使用 mock 模式，正式 AI 功能需配置 API Key。", flush=True)
    return python


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def open_when_ready(url: str, process: subprocess.Popen) -> None:
    # Ignore machine-wide HTTP proxies when checking the local server.
    opener = build_opener(ProxyHandler({}))
    for _ in range(60):
        if process.poll() is not None:
            return
        try:
            with opener.open(url + "/api/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            pass
        time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动思阶本地教学平台")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print("需要 Python 3.11 或以上版本，请安装后再启动。", file=sys.stderr)
        return 1
    if not 1 <= args.port <= 65535:
        parser.error("端口必须介于 1 和 65535 之间")
    url = f"http://127.0.0.1:{args.port}"
    if port_in_use(args.port):
        print(f"端口 {args.port} 已被占用。若平台已启动，可访问 {url}。")
        print("不会停止已有进程；也可用 python start.py --port 8001 启动其他端口。")
        return 1
    try:
        python = prepare_environment()
        print(f"启动平台：{url}\n请保持此窗口打开；按 Ctrl+C 停止服务。", flush=True)
        process = subprocess.Popen(
            [str(python), "-m", "uvicorn", "backend.app.main:app",
             "--host", "127.0.0.1", "--port", str(args.port), "--no-access-log"],
            cwd=ROOT,
        )
        if not args.no_browser:
            threading.Thread(target=open_when_ready, args=(url, process), daemon=True).start()
        try:
            return process.wait()
        except KeyboardInterrupt:
            print("\n正在停止服务……", flush=True)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return 0
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"启动失败：{error}\n请检查 Python 安装、网络和项目目录写入权限。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
