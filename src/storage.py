"""
数据存储层：JSON 文件读写 + 去重 + 跨轮次数据管理。
所有数据存储在 data/YYYY-MM-DD/ 目录下，Git tracked。
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.config import DATA_DIR, bjt_today
from src.models import CompanyType, Job, SearchRound


def _today_dir() -> Path:
    today = bjt_today()
    d = DATA_DIR / today
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_round(round_data: SearchRound) -> Path:
    """保存一轮搜索结果到 JSON"""
    today = bjt_today()
    path = DATA_DIR / today / f"round-{round_data.round_label}.json"
    data = {
        "round_label": round_data.round_label,
        "keywords_used": round_data.keywords_used,
        "total_raw": round_data.total_raw,
        "total_after_filter": round_data.total_after_filter,
        "errors": round_data.errors,
        "stats": round_data.stats,
        "scraped_at": datetime.now().isoformat(),
        "jobs": [_job_to_dict(j) for j in round_data.jobs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_round(round_label: str, target_date: str | None = None) -> SearchRound | None:
    """加载指定轮次的数据"""
    day = target_date or bjt_today()
    path = DATA_DIR / day / f"round-{round_label}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SearchRound(
        round_label=data["round_label"],
        keywords_used=data.get("keywords_used", []),
        total_raw=data.get("total_raw", 0),
        total_after_filter=data.get("total_after_filter", 0),
        jobs=[_dict_to_job(j) for j in data.get("jobs", [])],
        errors=data.get("errors", []),
        stats=data.get("stats", {}),
    )


def load_all_rounds(target_date: str | None = None) -> list[SearchRound]:
    """加载当天所有轮次的数据"""
    d = DATA_DIR / (target_date or bjt_today())
    if not d.exists():
        return []
    rounds = []
    for f in sorted(d.glob("round-*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        rounds.append(SearchRound(
            round_label=data["round_label"],
            keywords_used=data.get("keywords_used", []),
            total_raw=data.get("total_raw", 0),
            total_after_filter=data.get("total_after_filter", 0),
            jobs=[_dict_to_job(j) for j in data.get("jobs", [])],
            errors=data.get("errors", []),
            stats=data.get("stats", {}),
        ))
    return rounds


def deduplicate_all(jobs: list[Job]) -> list[Job]:
    """全局去重：平台内 + 跨平台"""
    seen_keys: set[str] = set()
    seen_cross: set[str] = set()
    result: list[Job] = []

    for job in jobs:
        pk = job.dedup_key
        cpk = job.cross_platform_key
        if pk in seen_keys or cpk in seen_cross:
            continue
        seen_keys.add(pk)
        seen_cross.add(cpk)
        result.append(job)

    return result


def save_deduped(jobs: list[Job], target_date: str | None = None) -> Path:
    """保存去重后的最终结果"""
    today = target_date or bjt_today()
    d = DATA_DIR / today
    d.mkdir(parents=True, exist_ok=True)
    path = d / "deduped.json"
    data = {
        "date": today,
        "total": len(jobs),
        "generated_at": datetime.now().isoformat(),
        "jobs": [_job_to_dict(j) for j in jobs],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_deduped(target_date: str | None = None) -> list[Job]:
    """加载去重后的结果"""
    d = DATA_DIR / (target_date or bjt_today())
    path = d / "deduped.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [_dict_to_job(j) for j in data.get("jobs", [])]


# ---- 辅助函数 ----

def _job_to_dict(job: Job) -> dict:
    return {
        "platform": job.platform,
        "job_id": job.job_id,
        "url": job.url,
        "title": job.title,
        "company": job.company,
        "company_type": job.company_type.value if job.company_type else None,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_text": job.salary_text,
        "location": job.location,
        "district": job.district,
        "lng": job.lng,
        "lat": job.lat,
        "distance_km": job.distance_km,
        "responsibilities": job.responsibilities,
        "requirements": job.requirements,
        "hr_active": job.hr_active,
        "posted_date": job.posted_date,
        "scraped_at": job.scraped_at,
        "search_round": job.search_round,
        "excluded": job.excluded,
        "exclude_reason": job.exclude_reason,
    }


def _dict_to_job(d: dict) -> Job:
    ct = d.get("company_type")
    return Job(
        platform=d.get("platform", ""),
        job_id=d.get("job_id", ""),
        url=d.get("url", ""),
        title=d.get("title", ""),
        company=d.get("company", ""),
        company_type=CompanyType(ct) if ct else None,
        salary_min=float(d.get("salary_min", 0)),
        salary_max=float(d.get("salary_max", 0)),
        salary_text=d.get("salary_text", ""),
        location=d.get("location", ""),
        district=d.get("district", ""),
        lng=d.get("lng"),
        lat=d.get("lat"),
        distance_km=d.get("distance_km"),
        responsibilities=d.get("responsibilities", ""),
        requirements=d.get("requirements", ""),
        hr_active=bool(d.get("hr_active", False)),
        posted_date=d.get("posted_date", ""),
        scraped_at=d.get("scraped_at", ""),
        search_round=d.get("search_round", ""),
        excluded=bool(d.get("excluded", False)),
        exclude_reason=d.get("exclude_reason", ""),
    )

