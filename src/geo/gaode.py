"""
高德地图 API 客户端：地理编码 + 距离计算（可选功能）。
无 Key 时自动跳过，不影响主流程。
"""
from __future__ import annotations

import logging
import math
import httpx

from src.config import GAODE_API_KEY, HOME_LNG, HOME_LAT

logger = logging.getLogger(__name__)


async def geocode(address: str) -> tuple[float, float] | None:
    """
    地理编码：地址 → 经纬度。
    返回 (lng, lat) 或 None。
    """
    if not GAODE_API_KEY:
        return None

    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {
        "key": GAODE_API_KEY,
        "address": address,
        "city": "成都",
        "output": "JSON",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            if data.get("status") == "1" and data.get("geocodes"):
                loc = data["geocodes"][0]["location"]
                lng, lat = loc.split(",")
                return float(lng), float(lat)
        except Exception as e:
            logger.warning(f"高德地理编码失败: {e}")
            pass

    return None


def calc_distance(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """
    Haversine 公式计算两点间距离（公里）。
    """
    R = 6371.0
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return round(R * c, 1)


async def get_distance(address: str) -> tuple[float, float, float] | None:
    """
    获取地址的经纬度和离家距离。
    返回 (lng, lat, distance_km) 或 None。
    """
    coords = await geocode(address)
    if not coords:
        return None

    lng, lat = coords
    dist = calc_distance(HOME_LNG, HOME_LAT, lng, lat)
    return lng, lat, dist
