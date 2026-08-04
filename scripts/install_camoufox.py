#!/usr/bin/env python3
"""手动安装 Camoufox 浏览器二进制（绕开 camoufox 内部下载客户端不走代理的问题）。

背景：`python -m camoufox fetch` 用 requests 直连 api.github.com 被 GFW 重置
（SSL EOF），内部客户端不读 Clash 代理环境变量。改用代理 curl 手动下载 zip，
再按 camoufox 包 multiversion 的目录约定手工装进缓存目录。

用法：
  python scripts/install_camoufox.py --zip <path> [--sha256 <hex>]

流程：
  1. （可选）校验 zip 的 sha256（与 GitHub release asset 的 digest 比对）
  2. 从 zip 文件名解析 version/build，解压到 Cache/browsers/official/{ver}-{build}-{sha8}/
  3. 写 version.json + config.json（active_version）+ .0.5_FLAG
  4. 验证 camoufox_path()/launch_path() 可用
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camoufox.pkgman import INSTALL_DIR, OS_NAME  # noqa: E402
from camoufox.multiversion import COMPAT_FLAG  # noqa: E402

# zip 文件名结构：{name}-{version}-{build}-{os}.{arch}.zip
ZIP_RE = re.compile(
    rf"camoufox-(\d+\.\d+\.\d+)-(.+?)-{re.escape(OS_NAME)}\.(x86_64|i686|arm64)\.zip"
)


def parse_name(zip_name: str) -> tuple[str, str]:
    """从 zip 文件名解析 (version, build)，如 152.0.4 / beta.28"""
    m = ZIP_RE.match(zip_name)
    if not m:
        raise ValueError(f"无法从文件名解析版本: {zip_name}")
    return m.group(1), m.group(2)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def flatten(src: Path, dst: Path) -> None:
    """解压并展平可能存在的单层包裹目录（Firefox zip 可能自带一层目录）。"""
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dst)
    # 若仅有一个子目录且没有顶层文件，则把子目录内容上移
    top_files = [p for p in dst.iterdir()]
    if len(top_files) == 1 and top_files[0].is_dir():
        inner = top_files[0]
        for p in inner.iterdir():
            shutil.move(str(p), str(dst / p.name))
        inner.rmdir()


def main() -> int:
    # Windows 终端编码不一致 → 强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="手动安装 Camoufox 浏览器二进制")
    parser.add_argument("--zip", required=True, help="Camoufox 浏览器 zip 路径")
    parser.add_argument("--sha256", default="", help="期望 sha256（GitHub asset digest，可跳过校验）")
    args = parser.parse_args()

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"❌ zip 不存在: {zip_path}")
        return 1

    # 1. 校验 sha256
    if args.sha256:
        print("校验 sha256 ...")
        actual = sha256_of(zip_path)
        if actual.lower() != args.sha256.lower():
            print(f"❌ sha256 不匹配\n  期望: {args.sha256}\n  实际: {actual}")
            return 1
        print(f"✅ sha256 校验通过 ({actual[:16]}...)")
    else:
        print("⚠️ 跳过 sha256 校验（未传 --sha256）")

    # 2. 解析版本，组装安装路径
    version, build = parse_name(zip_path.name)
    sha8 = (args.sha256[:8]) if args.sha256 else ""
    version_folder = f"{version}-{build}" + (f"-{sha8}" if sha8 else "")
    install_path = INSTALL_DIR / "browsers" / "official" / version_folder
    print(f"版本: {version}-{build}  →  {install_path}")

    if install_path.exists():
        print("⚠️ 目标目录已存在，先清空重装")
        shutil.rmtree(install_path)

    # 3. 解压
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "stage"
        staging.mkdir()
        print(f"解压 {zip_path.name} ...")
        flatten(zip_path, staging)
        shutil.copytree(staging, install_path)
    print("✅ 解压完成")

    # 4. 写 version.json
    metadata = {
        "version": version,
        "build": build,
        "prerelease": False,
        "sha256": args.sha256 or None,
        "created_at": "2026-07-19T07:19:17Z",
    }
    (install_path / "version.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )

    # 5. 写 config.json（active_version）+ .0.5_FLAG
    relative = f"browsers/official/{version_folder}"
    config_path = INSTALL_DIR / "config.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    config["active_version"] = relative
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    COMPAT_FLAG.touch()
    print(f"✅ config.json active_version={relative}，.0.5_FLAG 已创建")

    # 6. 验证
    from camoufox.pkgman import camoufox_path, launch_path

    print(f"camoufox_path() = {camoufox_path()}")
    print(f"launch_path()   = {launch_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
