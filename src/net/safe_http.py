"""
SSRF 加固的出站 HTTP 工具。

按安全门禁（Mimosa）要求，服务端请求统一走 safe_request：
- 仅允许 https 协议
- 目标 host 必须命中调用方声明的白名单
- 请求前解析全部 IP，阻断 私网/环回/链路本地/保留/组播 地址（含 IPv6，防 DNS rebinding）
- 禁用重定向跟随
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx


def safe_request(
    method: str,
    url: str,
    *,
    allowed_hosts: tuple[str, ...] | set[str],
    headers: dict | None = None,
    content: bytes | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """SSRF 加固的同步 HTTP 请求。白名单外 host / 非 https / 内网 IP 一律拒绝。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"仅允许 https 协议: {url}")
    host = parsed.hostname or ""
    if not host or host not in allowed_hosts:
        raise ValueError(f"目标 host 不在白名单 {sorted(allowed_hosts)}: {host}")

    # 解析并校验所有地址：任一命中内网/保留段即拒绝（逐 IP 校验，防 DNS rebinding）
    try:
        addrinfos = socket.getaddrinfo(host, parsed.port or 443)
    except socket.gaierror as e:
        raise ValueError(f"目标 host 解析失败: {host} ({e})") from e
    for ai in addrinfos:
        ip = ipaddress.ip_address(ai[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"目标解析到内网/保留地址，拒绝请求: {host} -> {ip}")

    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        return client.request(method, url, headers=headers, content=content)
