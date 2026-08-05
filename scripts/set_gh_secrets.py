"""
设置 GitHub Actions Secrets（SILICONFLOW_API_KEY / MODELSCOPE_API_KEY）。

自动化免手动操作：
- PAT 从 git remote URL 提取（项目 checkout 用的同一个 token）
- secret 值从本地 .env 读取（已写好，不入库）
- 加密用 pynacl 的 crypto_box_seal（GitHub Actions 标准加密）

用法：python scripts/set_gh_secrets.py
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    import nacl.bindings
except ImportError:
    sys.exit("需要 pynacl：python -m pip install pynacl")


def get_repo_auth() -> tuple[str, str, str]:
    """从 git remote 解析 (PAT, owner, repo)。"""
    out = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    m = re.match(r"https://([^@]+)@github.com/([^/]+)/([^/]+?)(?:\.git)?$", out)
    if not m:
        sys.exit(f"无法从 remote 解析 owner/repo/PAT: {out}")
    return m.group(1), m.group(2), m.group(3)


def api(token: str, owner: str, repo: str, method: str, path: str, body: dict | None = None):
    """带重试的 GitHub API 调用（CN 网络下 api.github.com 偶发 TLS 重置/空响应）。"""
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{owner}/{repo}{path}",
                data=json.dumps(body).encode() if body else None,
                method=method,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "cd-job-tracker",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.status == 204:
                    # GitHub Actions Secrets PUT 成功返回 204 No Content（无响应体）
                    return {}
                if 200 <= resp.status < 300:
                    if not raw.strip():
                        raise ConnectionError("empty response body on 2xx")
                    return json.loads(raw)
                raise ConnectionError(
                    f"HTTP {resp.status}: {raw.decode(errors='replace')[:200]}"
                )
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise last_err


def read_env(name: str) -> str:
    m = re.search(rf"^{name}=(.*)$", open(".env", encoding="utf-8").read(), re.M)
    return m.group(1).strip() if m else ""


def main() -> None:
    token, owner, repo = get_repo_auth()
    pub = api(token, owner, repo, "GET", "/actions/secrets/public-key")
    key_id, key_b64 = pub["key_id"], pub["key"]
    pub_key = base64.b64decode(key_b64)

    for name in ("SILICONFLOW_API_KEY", "MODELSCOPE_API_KEY"):
        val = read_env(name)
        if not val:
            print(f"跳过 {name}：.env 未设置")
            continue
        sealed = nacl.bindings.crypto_box_seal(val.encode("utf-8"), pub_key)
        api(
            token, owner, repo, "PUT",
            f"/actions/secrets/{name}",
            {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key_id},
        )
        print(f"[OK] set Secret {name} ({len(val)} chars, {owner}/{repo})")

    print("[DONE]")


if __name__ == "__main__":
    main()
