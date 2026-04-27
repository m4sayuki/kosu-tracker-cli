"""Tests for parse_date, ActivitySample.as_dict, and today_log_path."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from kosu_tracker.cli import ActivitySample, parse_date, today_log_path


class TestParseDate:
    def test_today(self, monkeypatch):
        fixed = date(2025, 6, 15)
        monkeypatch.setattr("kosu_tracker.cli.datetime", _FakeDatetime(fixed))
        assert parse_date("today") == fixed

    def test_yesterday(self, monkeypatch):
        fixed = date(2025, 6, 15)
        monkeypatch.setattr("kosu_tracker.cli.datetime", _FakeDatetime(fixed))
        assert parse_date("yesterday") == fixed - timedelta(days=1)

    def test_iso_date_string(self):
        assert parse_date("2025-01-15") == date(2025, 1, 15)

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            parse_date("not-a-date")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_date("15/01/2025")


class _FakeDatetime:
    """datetime の now() だけを差し替えるための薄いラッパー。"""

    def __init__(self, fixed_date: date):
        self._fixed = fixed_date

    def now(self, *args, **kwargs):
        return datetime(self._fixed.year, self._fixed.month, self._fixed.day)

    # date.fromisoformat などの呼び出しは実物に委譲する
    @staticmethod
    def fromisoformat(s: str) -> date:
        return date.fromisoformat(s)


class TestActivitySampleAsDict:
    def _make_sample(self, **kwargs) -> ActivitySample:
        defaults = {
            "timestamp": "2025-01-15T09:00:00+09:00",
            "app_name": "Cursor",
            "window_title": "main.py",
            "category": "development",
            "browser_title": None,
            "browser_url": None,
            "browser_domain": None,
            "collection_error": None,
        }
        defaults.update(kwargs)
        return ActivitySample(**defaults)

    def test_all_keys_present(self):
        d = self._make_sample().as_dict()
        expected_keys = {
            "timestamp", "app_name", "window_title", "category",
            "browser_title", "browser_url", "browser_domain", "collection_error",
        }
        assert set(d.keys()) == expected_keys

    def test_values_match_fields(self):
        sample = self._make_sample(app_name="Chrome", category="browser")
        d = sample.as_dict()
        assert d["app_name"] == "Chrome"
        assert d["category"] == "browser"

    def test_none_fields_preserved(self):
        d = self._make_sample().as_dict()
        assert d["browser_title"] is None
        assert d["browser_url"] is None
        assert d["browser_domain"] is None
        assert d["collection_error"] is None

    def test_all_fields_populated(self):
        sample = self._make_sample(
            browser_title="GitHub",
            browser_url="https://github.com",
            browser_domain="github.com",
            collection_error="some error",
        )
        d = sample.as_dict()
        assert d["browser_title"] == "GitHub"
        assert d["browser_url"] == "https://github.com"
        assert d["browser_domain"] == "github.com"
        assert d["collection_error"] == "some error"


class TestTodayLogPath:
    def test_specific_date(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KOSU_TRACKER_HOME", str(tmp_path))
        # モジュール再読み込みなしにパスを確認するため LOG_DIR を直接参照
        import kosu_tracker.cli as cli_module
        monkeypatch.setattr(cli_module, "LOG_DIR", tmp_path / "logs")

        result = cli_module.today_log_path(date(2025, 1, 15))
        assert result.name == "2025-01-15.jsonl"
        assert result.parent == tmp_path / "logs"

    def test_no_date_uses_today(self, monkeypatch, tmp_path):
        import kosu_tracker.cli as cli_module
        monkeypatch.setattr(cli_module, "LOG_DIR", tmp_path / "logs")

        result = cli_module.today_log_path(None)
        expected_name = f"{datetime.now().date().isoformat()}.jsonl"
        assert result.name == expected_name
