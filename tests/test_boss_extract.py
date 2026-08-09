"""
BOSS直聘 DOM 提取器 单元测试（plan B1 增量，参照智联双层模式）。

- _extract_boss_from_dom：SSR JSON 优先、DOM 卡片回退、双空/异常兜底
- 集成：extract_jobs_from_page 对 BOSS直聘走 DOM 优先（不经 OCR/标题匹配回填）

⚠️ 提取器本身未经实页验证（2026-08-09 实测：本机出口 IP 被 BOSS 风控拦截
"当前 IP 地址可能存在异常访问行为"，MCP 浏览器/API 都看不到卡片，本机无法
验证；需 CI/Camoufox 环境实跑复核）。SSR/DOM 字段路径沿用旧 scrapers/boss.py
已验证结果。这里 mock evaluate 返回值，验证 Python 侧的分派/兜底/集成逻辑。
"""
from __future__ import annotations

import pytest

from src.agent.extract import _extract_boss_from_dom, extract_jobs_from_page


def _ssr_to_out(items) -> list[dict]:
    """镜像 _extract_boss_from_dom JS 里的 SSR→输出字段映射（mock 里补跑 JS 逻辑）。"""
    out = []
    for it in items or []:
        if not it.get("jobName"):
            continue
        boss = it.get("bossInfo") or {}
        eid = it.get("encryptJobId") or it.get("jobId") or ""
        out.append({
            "title": (it.get("jobName") or "").strip(),
            "company": it.get("brandName") or "",
            "url": f"https://www.zhipin.com/job_detail/{eid}.html" if eid else "",
            "salary_text": it.get("salaryDesc") or "",
            "location": (it.get("cityName") or "") + (it.get("areaDistrict") or ""),
            "requirements": ", ".join(it.get("jobLabels") or []),
            "hr_active": bool(boss.get("online")),
        })
    return out


class _FakeBossPage:
    """模拟 BOSS 页面：按 evaluate 的 JS 内容返回 SSR JSON 结果或 DOM 结果（或抛异常）。"""

    def __init__(self, ssr_out=None, dom_out=None, raises: bool = False):
        self.ssr_out = ssr_out
        self.dom_out = dom_out
        self.raises = raises

    async def evaluate(self, expr, arg=None):
        if self.raises:
            raise RuntimeError("mock evaluate boom")
        if "__NEXT_DATA__" in expr:
            # 镜像 JS 内部分支：SSR 出数则用 SSR，空则回退 DOM 卡片
            transformed = _ssr_to_out(self.ssr_out)
            return transformed if transformed else (self.dom_out or [])
        return self.dom_out if self.dom_out is not None else []


# BOSS 实测痛点（旧 scraper 时代）：同标题多公司并排，SSR 字段 jobName/encryptJobId 成组
BOSS_SSR = [
    {
        "jobName": "AI算法工程师", "brandName": "甲智能科技有限公司",
        "encryptJobId": "abc123", "salaryDesc": "20-40K·16薪",
        "cityName": "成都", "areaDistrict": "高新区",
        "jobLabels": ["团队氛围好", "五险一金"], "bossInfo": {"online": True},
    },
    {
        "jobName": "AI算法工程师", "brandName": "乙数据科技有限公司",
        "encryptJobId": "xyz789", "salaryDesc": "15-25K·13薪",
        "cityName": "成都", "areaDistrict": "武侯区",
        "jobLabels": ["弹性工作"], "bossInfo": {"online": False},
    },
]

BOSS_DOM = [
    {
        "title": "气象分析师", "company": "某气象科技公司",
        "url": "https://www.zhipin.com/job_detail/dom001.html",
        "salary_text": "10-15K", "location": "成都·锦江区",
        "requirements": "本科", "hr_active": True,
    },
]


class TestBossDomExtract:
    @pytest.mark.asyncio
    async def test_ssr_json_primary_same_title_multi_company(self):
        """SSR JSON 有数据时直接返回；同标题多公司各取各的 URL（根治错配核心效果）。"""
        out = await _extract_boss_from_dom(_FakeBossPage(ssr_out=BOSS_SSR))
        assert len(out) == 2
        assert out[0]["title"] == "AI算法工程师"
        assert out[0]["company"] == "甲智能科技有限公司"
        assert out[0]["url"] == "https://www.zhipin.com/job_detail/abc123.html"
        assert out[1]["company"] == "乙数据科技有限公司"
        assert out[1]["url"] == "https://www.zhipin.com/job_detail/xyz789.html"
        # 地点=城市+区，自带成都前缀（下游 _filter_city 可保留）
        assert out[0]["location"] == "成都高新区"
        # requirements 来自 jobLabels，hr_active 来自 bossInfo.online
        assert out[0]["requirements"] == "团队氛围好, 五险一金"
        assert out[0]["hr_active"] is True
        assert out[1]["hr_active"] is False

    @pytest.mark.asyncio
    async def test_dom_fallback_when_ssr_empty(self):
        """SSR JSON 为空（页面无 __NEXT_DATA__）时回退 DOM 卡片扫描。"""
        out = await _extract_boss_from_dom(_FakeBossPage(ssr_out=[], dom_out=BOSS_DOM))
        assert len(out) == 1
        assert out[0]["title"] == "气象分析师"
        assert out[0]["url"] == "https://www.zhipin.com/job_detail/dom001.html"
        assert out[0]["hr_active"] is True

    @pytest.mark.asyncio
    async def test_both_empty_returns_empty(self):
        """SSR + DOM 都空 -> 返回空（上游回退 OCR，安全不丢数据）。"""
        out = await _extract_boss_from_dom(_FakeBossPage(ssr_out=[], dom_out=[]))
        assert out == []

    @pytest.mark.asyncio
    async def test_ssr_missing_encrypt_job_id_kept(self):
        """SSR 缺 encryptJobId（构造不出详情 URL）时仍保留该岗位，url 置空不丢岗位。"""
        ssr = [{"jobName": "环保工程师", "brandName": "某环保公司",
                "salaryDesc": "面议", "cityName": "成都", "areaDistrict": "双流区"}]
        out = await _extract_boss_from_dom(_FakeBossPage(ssr_out=ssr))
        assert len(out) == 1
        assert out[0]["url"] == ""
        assert out[0]["company"] == "某环保公司"

    @pytest.mark.asyncio
    async def test_evaluate_exception_returns_empty(self):
        """page.evaluate 抛异常（风控/改版）-> 返回空，上游安全回退 OCR。"""
        out = await _extract_boss_from_dom(_FakeBossPage(raises=True))
        assert out == []

    @pytest.mark.asyncio
    async def test_extract_jobs_from_page_boss_dom_first(self):
        """集成：BOSS直聘走 DOM 优先，直接产出 Job（不经 OCR/标题匹配回填）。"""
        jobs = await extract_jobs_from_page(_FakeBossPage(ssr_out=BOSS_SSR), "BOSS直聘", "12")
        assert len(jobs) == 2
        assert all(j.platform == "BOSS直聘" for j in jobs)
        assert jobs[0].company == "甲智能科技有限公司"
        assert jobs[0].url == "https://www.zhipin.com/job_detail/abc123.html"
        assert jobs[1].url == "https://www.zhipin.com/job_detail/xyz789.html"
        assert jobs[0].location == "成都高新区"
        assert jobs[0].hr_active is True
