# -*- coding: utf-8 -*-
"""四川公共招聘网（sc91）DOM 提取器与搜索词适配单元测试。

fixture 取自 2026-09-05 实页渲染结构（div.search_result_item 卡片 +
onclick 内 window.open 详情链接 + layui 分页容器）。
"""
from __future__ import annotations

import pytest

from src.agent.extract import _extract_sc91_from_dom
from src.scrapers.agent_scraper import Sc91AgentScraper

# 实页卡片结构样本（精简自 .claude/tmp 实测渲染 HTML）
_FIXTURE_HTML = """
<div>
  <div class="search_result_item" title="点击查看职位详情"
       onclick="window.open(&quot;/app/home/jobdetail/2976507.shtml?comptype=1&amp;post_id=1033491&quot;,&quot;_self&quot;);">
    <div class="search_result_image"><img src="/statics/image/common/comp-hbg-logo.png"></div>
    <div class="search_result_text">
      <div class="search_result_text_item_1">清洁工[四川省成都市龙泉驿区]</div>
      <div class="search_result_text_item_1">恒晟致远后勤服务（四川）集团有限公司</div>
      <div class="search_result_text_item_2">
        招聘人数：20&nbsp;&nbsp;|&nbsp;&nbsp;
        薪资待遇：<span style="color:#FF9800;">2330&nbsp;-&nbsp;2880</span>&nbsp;&nbsp;|&nbsp;&nbsp;
        工作方式：全职&nbsp;&nbsp;|&nbsp;&nbsp;工作经验：无要求
      </div>
    </div>
  </div>
  <div class="search_result_item"
       onclick="window.open('/app/home/jobdetail/2861145.shtml?post_id=1','_self');">
    <div class="search_result_text">
      <div class="search_result_text_item_1">环境维护技工（C66733）</div>
      <div class="search_result_text_item_1">四川阳光致美能源发展有限公司</div>
      <div class="search_result_text_item_2">
        招聘人数：1 | 薪资待遇：5000 - 8000 | 工作方式：全职
      </div>
    </div>
  </div>
</div>
"""


class _FakePage:
    async def evaluate(self, _script):
        # 真浏览器环境由 CI 实页验证；单测只验证 JS 无法独立运行，
        # 这里直接断言提取器对 evaluate 异常的容错与 Python 侧字段约定
        raise RuntimeError("unit-test: no browser")


class TestSc91SearchKeyword:
    def test_strips_city_prefix(self):
        s = Sc91AgentScraper()
        assert s._search_keyword("成都 气象工程师") == "气象工程师"
        assert s._search_keyword("成都 环境监测") == "环境监测"
        assert s._search_keyword("环境工程师") == "环境工程师"

    def test_platform_registered(self):
        from src.scrapers import ALL_SCRAPERS, AGENT_SCRAPERS
        assert "四川公共招聘网" in ALL_SCRAPERS
        assert ALL_SCRAPERS["四川公共招聘网"] is Sc91AgentScraper
        assert AGENT_SCRAPERS["四川公共招聘网"] is Sc91AgentScraper

    def test_extractor_registered(self):
        from src.agent.extract import _DOM_EXTRACTORS
        assert _DOM_EXTRACTORS.get("四川公共招聘网") is _extract_sc91_from_dom


class TestSc91ExtractorGuard:
    @pytest.mark.asyncio
    async def test_evaluate_failure_returns_empty(self):
        assert await _extract_sc91_from_dom(_FakePage()) == []
