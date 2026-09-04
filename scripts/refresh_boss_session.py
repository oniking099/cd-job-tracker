#!/usr/bin/env python3
"""BOSS 扫码登录 + 自动上传 cookie 到 GitHub secret（一条命令，每日刷新用）。

背景（用户 2026-08-09）：BOSS cookie 约 24h 过期，每天需扫码刷新，否则 17:00 检索拉不到 JD。
本脚本把「扫码登录 + 更新 secret」合并成一条命令，无需手动打开 GitHub 网页：
  1. 弹出浏览器（BOSS 扫码登录页），用手机 App 扫码
  2. 检测到登录成功 → 导出 .sessions/boss_cookie.json
  3. 自动用 git 凭据管理器里的 GitHub PAT，把 cookie 上传到 secret `BOSS_COOKIE`
  4. 完成 —— 当天 17:00 GitHub 检索自动用新 cookie 拉 BOSS

依赖：
  - 本地 git 曾用 HTTPS 推送过本仓库（git credential manager 里存有 GitHub PAT）
  - pynacl（加密 secret 用，缺了会自动提示 pip install）
  - camoufox 已安装（capture_session 的 BOSS 引擎，之前登录过就有）

用法：
  python scripts/refresh_boss_session.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import capture_session
except ModuleNotFoundError as e:
    print("❌ 缺依赖:", e)
    print("   本脚本需要 playwright + camoufox，请用装过依赖的 Python 运行：")
    print("     py .\\refresh_boss_session.py")
    print("   （即 C:\\Python313\\python.exe；不要用 conda base 的 python——(base) 环境没装这些依赖）")
    sys.exit(1)
from src.config import SESSIONS_DIR

OWNER = "oniking099"
REPO = "cd-job-tracker"
SECRET_NAME = "BOSS_COOKIE"
API = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets"


def _utf8_streams() -> None:
    """Windows 终端编码不一致 → 强制 UTF-8（与项目其他脚本一致）。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def get_token() -> str:
    """从 git credential manager 取 github.com 的 PAT（绝不显示/落地）。"""
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise SystemExit("git 凭据管理器里没有 GitHub 的 PAT（本机没推送过/凭据被清）")


def _api(method: str, url: str, token: str, body: dict | None = None) -> dict:
    """GitHub API 调用（SSRF 加固：仅 https + api.github.com 白名单，走 safe_request）。"""
    from src.net.safe_http import safe_request

    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data:
        headers["Content-Type"] = "application/json"
    resp = safe_request(
        method, url,
        allowed_hosts=("api.github.com",),
        headers=headers,
        content=data,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"HTTP {resp.status_code} {resp.reason_phrase}: "
                         f"{resp.text[:200]}")
    raw = resp.content
    return json.loads(raw) if raw else {}


def upload_to_secret(value: str) -> None:
    """用 git PAT + libsodium 把 cookies 数组加密上传到 GitHub secret BOSS_COOKIE。"""
    token = get_token()
    me = _api("GET", "https://api.github.com/user", token)
    print(f"已用 {me.get('login')} 的身份连接 GitHub")

    pk = _api("GET", f"{API}/public-key", token)
    key_id = pk["key_id"]
    from nacl.public import PublicKey, SealedBox

    sealed = SealedBox(PublicKey(base64.b64decode(pk["key"])))
    b64 = base64.b64encode(sealed.encrypt(value.encode("utf-8"))).decode()

    _api("PUT", f"{API}/{SECRET_NAME}", token, {"encrypted_value": b64, "key_id": key_id})
    print(f"✅ 已上传到 GitHub secret {SECRET_NAME}（{len(value)} 字节）")

    lst = _api("GET", API, token)
    if SECRET_NAME in [s["name"] for s in lst.get("secrets", [])]:
        print(f"✅ 校验通过：{SECRET_NAME} 已更新，当天 17:00 检索将使用新 cookie")
    else:
        raise SystemExit(f"{SECRET_NAME} 未出现在仓库 secrets 列表，上传可能失败")


async def main() -> int:
    # 1) 复用 capture_session 的 BOSS 扫码登录流程（打开浏览器 → 你扫码 → 自动导出 cookie）
    #    fresh=True：先清空持久 profile 旧 cookie，保证登录页出现扫码二维码，
    #    而不是直接打开上次的已登录状态（2026-08-12 用户反馈）
    rc = await capture_session.capture(
        "boss", capture_session.PLATFORM_URLS["boss"],
        manual=False, engine="camoufox", fresh=True,
    )
    if rc != 0:
        print("❌ 未检测到登录成功，未上传。请重新运行本命令完成扫码。")
        return 1

    # 2) 读取导出的 cookies 数组 → 自动上传 GitHub secret
    cookie_path = SESSIONS_DIR / "boss_cookie.json"
    if not cookie_path.exists():
        print(f"❌ 未找到导出文件 {cookie_path}，无法上传。")
        print("   可手动上传：把 .sessions/boss_cookie.json 全文粘到 GitHub secret BOSS_COOKIE。")
        return 1
    value = cookie_path.read_text(encoding="utf-8")
    print(f"\n登录成功，开始自动上传 {len(value)} 字节 cookie 到 secret {SECRET_NAME}...")
    try:
        upload_to_secret(value)
    except SystemExit as e:
        print(f"❌ 自动上传失败: {e}")
        print("   可手动上传：把 .sessions/boss_cookie.json 全文粘到 GitHub secret BOSS_COOKIE。")
        return 1
    except Exception as e:
        print(f"❌ 自动上传异常: {e}")
        print("   可手动上传：把 .sessions/boss_cookie.json 全文粘到 GitHub secret BOSS_COOKIE。")
        return 1
    print("\n🎉 完成！今天 BOSS 不用再操作了，17:00 检索自动用新 cookie。")
    return 0


if __name__ == "__main__":
    _utf8_streams()
    sys.exit(asyncio.run(main()))
