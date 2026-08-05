"""探测 ModelScope api-inference 上 7 个 Qwen-VL 视觉模型的正确 API 名称。

用户给出的 7 个视觉模型（中文显示名），需确认 API 调用名是英文 Qwen 前缀
还是中文 千问 前缀。逐个探测 `Qwen/<Name>` 与 `千问/<Name>` 两种写法。

用法：python scripts/probe_modelscope_models.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request


def read_env(name: str) -> str:
    m = re.search(rf"^{name}=(.*)$", open(".env", encoding="utf-8").read(), re.M)
    return m.group(1).strip() if m else ""


API_KEY = read_env("MODELSCOPE_API_KEY")
BASE = read_env("MODELSCOPE_BASE_URL") or "https://api-inference.modelscope.cn/v1"

# 用户确认可用的 7 个视觉模型（中文显示名 → 猜测的英文名）
MODELS = [
    "Qwen3-VL-30B-A3B-Thinking",
    "Qwen3-VL-235B-A22B-Instruct",
    "Qwen3-VL-8B-Instruct",
    "Qwen2.5-VL-72B-Instruct",
    "Qwen2.5-VL-32B-Instruct",
    "Qwen2.5-VL-7B-Instruct",
    "Qwen2.5-VL-3B-Instruct",
]
NAMESPACES = ["Qwen", "千问"]


def probe(model_id: str) -> tuple[str, int, str]:
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "你好，请只回复：OK"}],
        "max_tokens": 10,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"] or ""
            return "OK", resp.status, content[:40].replace("\n", " ")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:120].replace("\n", " ")
        return "FAIL", e.code, detail
    except Exception as e:  # 网络/超时
        return "ERR", 0, str(e)[:120]


def main() -> None:
    if not API_KEY:
        print("MODELSCOPE_API_KEY 未在 .env 设置")
        return
    ok_en: list[str] = []
    ok_cn: list[str] = []
    for name in MODELS:
        for ns in NAMESPACES:
            model_id = f"{ns}/{name}"
            status, code, detail = probe(model_id)
            tag = "OK" if status == "OK" else f"{status}({code})"
            print(f"[{tag:>12}] {model_id:<50} {detail}")
            if status == "OK":
                (ok_en if ns == "Qwen" else ok_cn).append(model_id)
            time.sleep(1)
    print("\n=== 结论 ===")
    print(f"英文 Qwen 前缀可用: {ok_en}")
    print(f"中文 千问 前缀可用: {ok_cn}")


if __name__ == "__main__":
    main()
