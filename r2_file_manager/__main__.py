from __future__ import annotations

import threading
import webbrowser
import os
import socket

from waitress import serve

from .app import create_app


def available_port(preferred: int = 8877) -> int:
    configured = os.environ.get("R2_FILE_MANAGER_PORT")
    if configured:
        port = int(configured)
        if not 1 <= port <= 65535:
            raise ValueError("R2_FILE_MANAGER_PORTは1～65535で指定してください。")
        return port
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.05)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("利用可能なローカルポートが見つかりません。")


def main() -> None:
    host = "127.0.0.1"
    port = available_port()
    app = create_app()
    threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"R2 File Manager: http://{host}:{port}")
    serve(app, host=host, port=port, threads=12)


if __name__ == "__main__":
    main()
