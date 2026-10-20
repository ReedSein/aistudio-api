"""A 429 is described and cooled down by the quota window the upstream names."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from aistudio_api.application import generation_errors
from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.domain.errors import UsageLimitExceeded, classify_error

MINUTE_BODY = (
    "[,[8,\"Quota exceeded for quota metric 'Generate requests' and limit "
    "'Generate requests per minute per user' of service "
    "'alkalimakersuite-pa.googleapis.com'\"]]"
)


def test_classify_error_reads_the_quota_window():
    assert classify_error(429, MINUTE_BODY).scope == "minute"  # type: ignore[attr-defined]
    day = classify_error(
        429, '{"error":{"code":429,"message":"limit per day reached"}}'
    )
    assert day.scope == "day"  # type: ignore[attr-defined]
    assert classify_error(429, "Resource exhausted").scope == "unknown"  # type: ignore[attr-defined]


def test_minute_limit_cools_down_briefly_without_escalation():
    stats = AccountStats(account_id="acc")
    for _ in range(5):
        stats.record_rate_limited(model="gemini-test", scope="minute")

    remaining = stats.model_cooldowns["gemini-test"] - time.time()
    assert 0 < remaining <= 60
    assert "gemini-test" not in stats.model_drip_mode
    assert "gemini-test" not in stats.model_rate_limited
    assert "gemini-test" not in stats.model_rate_limited_dates


def test_unknown_limit_keeps_existing_escalation():
    stats = AccountStats(account_id="acc")
    for _ in range(3):
        stats.record_rate_limited(model="gemini-test")
    assert stats.model_drip_mode["gemini-test"] is True


def test_rate_limit_message_names_the_window(monkeypatch):
    stats = AccountStats(account_id="acc")
    stats.record_rate_limited(model="gemini-test", scope="minute")
    rotator = MagicMock()
    rotator.get_account_stats.return_value = stats
    monkeypatch.setattr(generation_errors.runtime_state, "rotator", rotator)

    minute = generation_errors.rate_limit_message(
        UsageLimitExceeded("x", scope="minute"), "gemini-test", "acc"
    )
    assert minute.startswith("Requests-per-minute limit reached")
    assert "Retry in about" in minute
    assert "PST" not in minute

    day = generation_errors.rate_limit_message(
        UsageLimitExceeded("x", scope="day"), "gemini-test", "acc"
    )
    assert "daily quota" in day

    unknown = generation_errors.rate_limit_message(
        UsageLimitExceeded("配额用完: Resource exhausted", scope="unknown"),
        "gemini-test",
        "acc",
    )
    assert "Resource exhausted" in unknown
    assert "PST" not in unknown
