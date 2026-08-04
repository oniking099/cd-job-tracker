"""
点4 新增逻辑单元测试：
- extract.py: sanitize_url / _merge_card_urls（真实详情 URL 回填与占位符净化）
- generator.py: salary_months / strip_salary_suffix / safe_url（报告字段渲染）
"""
from __future__ import annotations

from src.agent.extract import sanitize_url, _merge_card_urls
from src.report.generator import salary_months, strip_salary_suffix, safe_url


class TestSanitizeUrl:
    def test_valid_url_kept(self):
        assert sanitize_url("https://jobs.51job.com/chengdu/168148901.html") == \
            "https://jobs.51job.com/chengdu/168148901.html"

    def test_placeholder_letters_dropped(self):
        assert sanitize_url("https://www.51job.com/job/xxxxx.html") == ""
        assert sanitize_url("https://www.51job.com/job/ddddd.html") == ""

    def test_non_http_dropped(self):
        assert sanitize_url("javascript:void(0)") == ""
        assert sanitize_url("/job/123.html") == ""


class TestMergeCardUrls:
    def test_exact_title_match(self):
        jobs = [{"title": "天线工程师", "company": "某公司"}]
        links = [{"text": "天线工程师", "href": "https://jobs.51job.com/chengdu/1.html"}]
        out = _merge_card_urls(jobs, links)
        assert out[0]["url"] == "https://jobs.51job.com/chengdu/1.html"

    def test_normalized_parenthesis_match(self):
        # DeepSeek 全角括号 vs DOM 半角括号，归一化后应匹配
        jobs = [{"title": "雷达应用产品算法(高级)工程师", "company": ""}]
        links = [{"text": "雷达应用产品算法（高级）工程师", "href": "https://jobs.51job.com/chengdu/2.html"}]
        out = _merge_card_urls(jobs, links)
        assert out[0]["url"] == "https://jobs.51job.com/chengdu/2.html"

    def test_existing_url_untouched(self):
        jobs = [{"title": "天线工程师", "url": "https://real.example.com/job/9.html"}]
        links = [{"text": "天线工程师", "href": "https://jobs.51job.com/chengdu/2.html"}]
        out = _merge_card_urls(jobs, links)
        assert out[0]["url"] == "https://real.example.com/job/9.html"

    def test_placeholder_link_not_used(self):
        jobs = [{"title": "天线工程师", "company": ""}]
        links = [{"text": "天线工程师", "href": "https://www.51job.com/job/xxxxx.html"}]
        out = _merge_card_urls(jobs, links)
        assert "url" not in out[0]

    def test_company_page_not_used_as_detail(self):
        jobs = [{"title": "天线工程师", "company": "中雷电科"}]
        links = [
            {"text": "中雷电科", "href": "https://jobs.51job.com/all/coXXX.html"},
            {"text": "天线工程师", "href": "https://jobs.51job.com/chengdu/3.html"},
        ]
        out = _merge_card_urls(jobs, links)
        assert out[0]["url"] == "https://jobs.51job.com/chengdu/3.html"


class TestSalaryFilters:
    def test_salary_months(self):
        assert salary_months("6千-1万·14薪") == "14薪"
        assert salary_months("8千-1.5万·15薪") == "15薪"
        assert salary_months("8千-1万") == ""

    def test_strip_salary_suffix(self):
        assert strip_salary_suffix("6千-1万·14薪") == "6千-1万"
        assert strip_salary_suffix("8千-1.5万·15薪") == "8千-1.5万"
        assert strip_salary_suffix("8千-1万") == "8千-1万"


class TestSafeUrl:
    def test_real_url_kept(self):
        assert safe_url("https://jobs.51job.com/chengdu/168148901.html") == \
            "https://jobs.51job.com/chengdu/168148901.html"

    def test_obvious_placeholder_dropped(self):
        # LLM 编造的字母占位符链接（老数据中存在，可可靠识别）
        assert safe_url("https://www.51job.com/job/xxxxx.html") == ""
        assert safe_url("https://www.51job.com/job/ddddd.html") == ""

    def test_non_http_dropped(self):
        assert safe_url("javascript:void(0)") == ""
        assert safe_url("/job/123.html") == ""

    def test_none_empty(self):
        assert safe_url(None) == ""
        assert safe_url("") == ""
