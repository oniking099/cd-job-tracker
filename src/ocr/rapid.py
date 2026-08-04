"""
RapidOCR 离线文本提取（视觉 LLM 额度/延时兜底）。

本地 ONNX 推理，不消耗 API 额度。首次调用时下载模型（~50MB）到 ~/.rapidocr。
输入为截图 PNG bytes（与 capture_screenshot 输出一致），输出为按视觉顺序拼接的文本行。
"""
from __future__ import annotations

import io
import logging
from functools import lru_cache

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_engine():
    """懒加载 OCR 引擎：模块 import 时不加载，首次识别时才初始化。"""
    from rapidocr import RapidOCR  # 延迟 import，避免没有该依赖时拖垮整个管线
    return RapidOCR()


async def extract_text_from_screenshot(screenshot: bytes | list[bytes]) -> str:
    """对一或多张截图做 OCR，返回按视觉从上到下拼接的文本。

    多张截图各自内部按 y 坐标（再按 x）排序；多张图之间以分隔行衔接。
    """
    shots = [screenshot] if isinstance(screenshot, bytes) else list(screenshot)
    if not shots:
        return ""

    try:
        engine = _get_engine()
    except Exception as e:
        logger.warning(f"[ocr] 引擎初始化失败: {e}")
        return ""

    all_lines: list[str] = []
    for i, sb in enumerate(shots):
        try:
            img = np.array(Image.open(io.BytesIO(sb)).convert("RGB"))
        except Exception as e:
            logger.warning(f"[ocr] 图片解码失败: {e}")
            continue

        try:
            result = engine(img)
        except Exception as e:
            logger.warning(f"[ocr] 识别失败: {e}")
            continue

        txts = list(getattr(result, "txts", None) or [])
        boxes_attr = getattr(result, "boxes", None)
        boxes = list(boxes_attr) if boxes_attr is not None else []
        if not txts:
            continue

        # 同一张图内：按 box 顶部 y 排序，同行按 x 排序；无 box 时保序
        items = list(zip(txts, boxes)) if len(boxes) == len(txts) else [(t, None) for t in txts]
        items.sort(key=lambda it: _box_key(it[1]))

        if i > 0:
            all_lines.append(f"===== 截图 {i + 1} =====")
        all_lines.extend(t for t, _ in items)

    return "\n".join(all_lines)


def _box_key(box):
    """box(N×4×2) 的排序键：先按顶部 y，再按左 x。无 box 时兜底保序。"""
    if box is None or len(box) == 0:
        return (0.0, 0.0)
    ys = [p[1] for p in box]
    xs = [p[0] for p in box]
    return (min(ys), min(xs))
