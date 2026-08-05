"""探测 ModelScope api-inference 模型可用性与正确名称（2026-08-05 全量复测）。

覆盖用户提供的两批模型：
  一、多模态（VL）：Qwen3-VL 235B/30B/8B + Qwen2.5-VL 72B/32B/7B/3B
      → 每个都测英文 `Qwen/` 与中文 `千问/` 两种前缀（用户疑点：汉字 vs 英文名）
  二、推理（text）：DeepSeek-V4-Pro / Qwen3-Max-Thinking / GLM-5.1 / GLM-5 /
      DeepSeek-V3.2 / DeepSeek-V3.1 / R1-0528 / QwQ-32B / R1-Distill 系列 /
      Qwen3.5-397B-A17B / Qwen3-235B-A22B-Thinking-2507 / DeepSeek-V4-Flash
      → Qwen 家族测双前缀；DeepSeek 用 deepseek-ai/；GLM 用 ZhipuAI/ + zai-org/

线程池并行探测，控制墙钟时间。只做文本可达性（VL 模型也接受纯文本请求）。

用法：python scripts/probe_modelscope_models.py
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
import urllib.error
import urllib.request

API_TIMEOUT = 30          # 单次请求超时（秒）
MAX_TOKENS = 20           # 可达性探测只需极短输出
MAX_WORKERS = 8           # 并行探测数


def read_env(name: str) -> str:
    m = re.search(rf"^{name}=(.*)$", open(".env", encoding="utf-8").read(), re.M)
    return m.group(1).strip() if m else ""


API_KEY = read_env("MODELSCOPE_API_KEY")
BASE = read_env("MODELSCOPE_BASE_URL") or "https://api-inference.modelscope.cn/v1"

# ---- 一、多模态（VL）：用户给的 7 个 → Instruct/Thinking 拆开全测 ----
VL_NAMES = [
    "Qwen3-VL-235B-A22B-Instruct",
    "Qwen3-VL-235B-A22B-Thinking",
    "Qwen2.5-VL-72B-Instruct",
    "Qwen3-VL-30B-A3B-Instruct",
    "Qwen3-VL-30B-A3B-Thinking",
    "Qwen2.5-VL-32B-Instruct",
    "Qwen3-VL-8B-Instruct",
    "Qwen2.5-VL-7B-Instruct",
    "Qwen2.5-VL-3B-Instruct",
]

# ---- 二、推理（text）：用户给的 12 个 + flash 兜底候选 ----
# Qwen 家族用 Qwen/；DeepSeek 用 deepseek-ai/；GLM 双 org 候选（ZhipuAI / zai-org）
TEXT_CANDIDATES = [
    "Qwen/Qwen3-Max-Thinking",
    "deepseek-ai/DeepSeek-V4-Pro",
    "Qwen/Qwen3.5-397B-A17B",
    "Qwen/Qwen3.5-397B-A17B-Thinking",
    "ZhipuAI/GLM-5.1",
    "zai-org/GLM-5.1",
    "deepseek-ai/DeepSeek-V3.2-Thinking",
    "deepseek-ai/DeepSeek-V3.2",
    "ZhipuAI/GLM-5",
    "zai-org/GLM-5",
    "deepseek-ai/DeepSeek-R1-0528",
    "Qwen/QwQ-32B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
    "Qwen/Qwen3-235B-A22B-Thinking-2507",
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V4-Flash",  # flash 兜底候选
]

Qwen_FAMILY = ("Qwen/", "千问/")


def expand_qwen_candidates(names: list[str]) -> list[str]:
    """Qwen 家族模型：每个名字都测英文 Qwen/ 与中文 千问/ 两种前缀。"""
    ids: list[str] = []
    for name in names:
        for ns in Qwen_FAMILY:
            ids.append(f"{ns}{name}")
    return ids


def probe(model_id: str) -> tuple[str, str, int, str]:
    """探测单个模型 ID。返回 (kind, model_id, status_code, detail)。"""
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "你好，请只回复：OK"}],
        "max_tokens": MAX_TOKENS,
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
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"] or ""
            return "vl", model_id, resp.status, content[:30].replace("\n", " ")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:100].replace("\n", " ")
        return "vl", model_id, e.code, detail
    except Exception as e:  # 网络/超时
        return "vl", model_id, 0, str(e)[:100]


def main() -> None:
    if not API_KEY:
        print("MODELSCOPE_API_KEY 未在 .env 设置")
        return

    targets: list[tuple[str, str, str]] = []  # (kind, label, model_id)
    for name in VL_NAMES:
        for ns in Qwen_FAMILY:
            targets.append(("vl", f"{ns}{name}", f"{ns}{name}"))
    for cand in TEXT_CANDIDATES:
        targets.append(("text", cand, cand))

    print(f"共 {len(targets)} 个候选，并行 {MAX_WORKERS} 线程，单次超时 {API_TIMEOUT}s\n")
    results: list[tuple[str, str, int, str]] = []

    def _run(t: tuple[str, str, str]) -> tuple[str, str, int, str]:
        kind, _label, model_id = t
        st = time.time()
        k, mid, code, detail = probe(model_id)
        return k, mid, code, detail

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for k, mid, code, detail in ex.map(_run, targets):
            results.append((k, mid, code, detail))

    # 按 kind 分组输出，OK 优先
    for kind in ("vl", "text"):
        print(f"===== {kind} 模型 =====")
        for k, mid, code, detail in sorted(results, key=lambda r: (0 if r[2] == 200 else 1, r[0])):
            if k != kind:
                continue
            tag = "OK " if code == 200 else f"{code}"
            print(f"  [{tag:>4}] {mid:<46} {detail}")
        print()

    ok = [mid for _, mid, code, _ in results if code == 200]
    fail = [mid for _, mid, code, _ in results if code != 200]
    print(f"=== 结论 ===")
    print(f"可用 ({len(ok)}):")
    for mid in ok:
        print(f"  {mid}")
    print(f"\n不可用 ({len(fail)}):")
    for mid in fail:
        print(f"  {mid}")


if __name__ == "__main__":
    main()
