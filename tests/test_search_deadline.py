"""检索 17:00 BJT 启动、跨午夜 02:30 截止，不能在启动时被误判为已截止。"""
from datetime import datetime, timezone, timedelta

import src.pipeline as pipeline


BJT = timezone(timedelta(hours=8))


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=BJT)


def test_cross_midnight_deadline_allows_evening_run(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "SEARCH_START_HOUR", 17)
    monkeypatch.setattr(pipeline, "SEARCH_DEADLINE_HOUR", 2)
    monkeypatch.setattr(pipeline, "SEARCH_DEADLINE_MINUTE", 30)

    monkeypatch.setattr(pipeline, "bjt_now", lambda: _at(17, 0))
    assert not pipeline._past_deadline()

    monkeypatch.setattr(pipeline, "bjt_now", lambda: _at(21, 0))
    assert not pipeline._past_deadline()

    monkeypatch.setattr(pipeline, "bjt_now", lambda: _at(2, 29))
    assert not pipeline._past_deadline()

    monkeypatch.setattr(pipeline, "bjt_now", lambda: _at(2, 30))
    assert pipeline._past_deadline()

    # 窗口外（未到 17:00）也视为已过截止，避免凌晨误启动
    monkeypatch.setattr(pipeline, "bjt_now", lambda: _at(16, 59))
    assert pipeline._past_deadline()
