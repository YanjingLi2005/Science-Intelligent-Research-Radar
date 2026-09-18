#!/usr/bin/env python3
"""Print LAN access URLs for Research Radar on the current machine.

Useful when opening the app from a phone/tablet on the same network:

    python scripts/lan_url.py
"""

from __future__ import annotations

import socket


def lan_ip() -> str:
    """Best-effort LAN IPv4 address without sending network traffic."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # connect() does not send packets; it just selects the route.
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def main() -> None:
    ip = lan_ip()
    print("Research Radar 局域网/移动端访问地址")
    print(f"  本机 IP      : {ip}")
    print(f"  Docker/Caddy : http://{ip}:8080")
    print(f"  本地 uvicorn : http://{ip}:8501")
    print(f"  HTTPS        : https://{ip}  （自签名证书，仅桌面端建议使用）")
    print()
    print("提示：手机打不开时，检查服务器防火墙是否放行 TCP 80/8080/443，")
    print("并确认手机与服务器在同一局域网。")


if __name__ == "__main__":
    main()
