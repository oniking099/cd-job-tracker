# -*- coding: utf-8 -*-
"""JD 要点总结（deepseek.summarize_jd / summarize_jobs_jd）单元测试。

Mock 掉网络调用，验证：围栏/纯 JSON 解析、条数与清洗、坏输出守卫、
已有总结的岗位跳过、无 JD 岗位不调用。
"""
from __future__ import annotations

import pytest

from src.llm import deepseek
from src.models import Job


def _job(resp: str = "负责环境监测。编制环评报告。", req: str = "本科及以上。3年经验。") -> Job:
    return Job(
        platform="智联招聘", job_id="t1", url="https://example.com/j/1",
        title="环境工程师", company="某环保公司",
        responsibilities=resp, requirements=req,
    )


class TestSummarizeJd:
    @pytest.mark.asyncio
    async def test_parse_plain_json(self, monkeypatch):
        async def fake_chat(prompt, system="", **kw):
            return '{"resp": ["负责环境监测数据分析", "编制环评报告"], "req": ["环境类本科", "3年监测经验"]}'

        monkeypatch.setattr(deepseek, "chat", fake_chat)
        j = _job()
        assert await deepseek.summarize_jd(j) is True
        assert j.resp_summary == ["负责环境监测数据分析", "编制环评报告"]
        assert j.req_summary == ["环境类本科", "3年监测经验"]

    @pytest.mark.asyncio
    async def test_parse_fenced_json_and_trim(self, monkeypatch):
        async def fake_chat(prompt, system="", **kw):
            return '```json\n{"resp": [" ' + "a" * 40 + '"], "req": ["b", "", "c", "d"]}\n```'

        monkeypatch.setattr(deepseek, "chat", fake_chat)
        j = _job()
        assert await deepseek.summarize_jd(j) is True
        # 超过3条截断 + 空串清洗；长条保留原文（截断由 prompt 约束，代码只管条数）
        assert len(j.req_summary) == 3
        assert all(j.req_summary)

    @pytest.mark.asyncio
    async def test_bad_json_returns_false(self, monkeypatch):
        async def fake_chat(prompt, system="", **kw):
            return "这不是 JSON"

        monkeypatch.setattr(deepseek, "chat", fake_chat)
        j = _job()
        assert await deepseek.summarize_jd(j) is False
        assert j.resp_summary == [] and j.req_summary == []

    @pytest.mark.asyncio
    async def test_empty_jd_skips_call(self, monkeypatch):
        async def boom(*a, **kw):
            raise AssertionError("无 JD 不应调用 LLM")

        monkeypatch.setattr(deepseek, "chat", boom)
        j = _job(resp="", req="")
        assert await deepseek.summarize_jd(j) is False


class TestSummarizeJobsJd:
    @pytest.mark.asyncio
    async def test_skips_already_summarized_and_empty(self, monkeypatch):
        async def boom(*a, **kw):
            raise AssertionError("不应触发 LLM 调用")

        monkeypatch.setattr(deepseek, "chat", boom)
        done = _job()
        done.resp_summary = ["已总结"]
        empty = _job(resp="", req="")
        assert await deepseek.summarize_jobs_jd([done, empty]) == 0

    @pytest.mark.asyncio
    async def test_partial_failure_counts_success_only(self, monkeypatch):
        calls = {"n": 0}

        async def fake_chat(prompt, system="", **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("API 超时")
            return '{"resp": ["负责X"], "req": ["本科"]}'

        monkeypatch.setattr(deepseek, "chat", fake_chat)
        jobs = [_job(), _job()]
        assert await deepseek.summarize_jobs_jd(jobs) == 1
        assert jobs[1].resp_summary == ["负责X"]
        assert jobs[0].resp_summary == []
