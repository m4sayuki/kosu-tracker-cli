"""Tests for summarize_rows and build_local_narrative."""
from __future__ import annotations

from datetime import date

import pytest

from kosu_tracker.cli import build_local_narrative, summarize_rows


def _make_row(
    app_name: str = "TestApp",
    category: str = "other",
    browser_title: str | None = None,
    window_title: str | None = None,
    sample_interval_seconds: int | None = None,
) -> dict:
    row = {
        "app_name": app_name,
        "category": category,
        "browser_title": browser_title,
        "window_title": window_title,
    }
    if sample_interval_seconds is not None:
        row["sample_interval_seconds"] = sample_interval_seconds
    return row


class TestSummarizeRows:
    def test_empty_rows(self):
        result = summarize_rows([], interval_minutes=1)
        assert result["total_samples"] == 0
        assert result["estimated_total_minutes"] == 0
        assert result["by_app"] == []
        assert result["by_category"] == []
        assert result["top_titles"] == []

    def test_single_row_accumulates_minutes(self):
        rows = [_make_row("Chrome", "browser")]
        result = summarize_rows(rows, interval_minutes=1)
        assert result["total_samples"] == 1
        assert result["estimated_total_minutes"] == 1
        assert result["by_app"][0]["name"] == "Chrome"
        assert result["by_app"][0]["minutes"] == 1

    def test_interval_minutes_applied(self):
        rows = [_make_row("Chrome", "browser")] * 3
        result = summarize_rows(rows, interval_minutes=5)
        assert result["estimated_total_minutes"] == 15
        assert result["by_app"][0]["minutes"] == 15

    def test_sample_interval_seconds_overrides_report_default(self):
        rows = [
            _make_row("Chrome", "browser", sample_interval_seconds=30),
            _make_row("Chrome", "browser", sample_interval_seconds=30),
        ]
        result = summarize_rows(rows, interval_minutes=1)
        assert result["estimated_total_minutes"] == 1
        assert result["by_app"][0]["minutes"] == 1

    def test_missing_sample_interval_uses_report_default(self):
        rows = [
            _make_row("Chrome", "browser", sample_interval_seconds=30),
            _make_row("Cursor", "development"),
        ]
        result = summarize_rows(rows, interval_minutes=5)
        by_app = {item["name"]: item["minutes"] for item in result["by_app"]}
        assert by_app["Chrome"] == 0.5
        assert by_app["Cursor"] == 5

    def test_rejects_zero_interval_minutes(self):
        with pytest.raises(SystemExit, match=r"interval-minutes must be greater than 0"):
            summarize_rows([_make_row("Chrome", "browser")], interval_minutes=0)

    def test_multiple_apps_accumulated(self):
        rows = [_make_row("Chrome", "browser")] * 2 + [_make_row("Cursor", "development")]
        result = summarize_rows(rows, interval_minutes=1)
        by_app = {item["name"]: item["minutes"] for item in result["by_app"]}
        assert by_app["Chrome"] == 2
        assert by_app["Cursor"] == 1

    def test_by_app_sorted_descending(self):
        rows = [_make_row("AppA", "other")] + [_make_row("AppB", "other")] * 3
        result = summarize_rows(rows, interval_minutes=1)
        assert result["by_app"][0]["name"] == "AppB"
        assert result["by_app"][1]["name"] == "AppA"

    def test_hours_field_calculated(self):
        rows = [_make_row("App", "other")] * 60
        result = summarize_rows(rows, interval_minutes=1)
        assert result["by_app"][0]["hours"] == 1.0

    def test_by_category_accumulated(self):
        rows = [_make_row("Chrome", "browser")] * 2 + [_make_row("Slack", "communication")]
        result = summarize_rows(rows, interval_minutes=1)
        by_cat = {item["name"]: item["minutes"] for item in result["by_category"]}
        assert by_cat["browser"] == 2
        assert by_cat["communication"] == 1

    def test_top_titles_uses_browser_title_first(self):
        rows = [_make_row("Chrome", "browser", browser_title="GitHub PR", window_title="Chrome")]
        result = summarize_rows(rows, interval_minutes=1)
        assert result["top_titles"][0]["name"] == "GitHub PR"

    def test_top_titles_falls_back_to_window_title(self):
        rows = [_make_row("Cursor", "development", browser_title=None, window_title="main.py")]
        result = summarize_rows(rows, interval_minutes=1)
        assert result["top_titles"][0]["name"] == "main.py"

    def test_top_titles_falls_back_to_app_name(self):
        rows = [_make_row("Finder", "other", browser_title=None, window_title=None)]
        result = summarize_rows(rows, interval_minutes=1)
        assert result["top_titles"][0]["name"] == "Finder"

    def test_top_titles_limited_to_10(self):
        rows = [_make_row(f"App{i}", "other", window_title=f"Title{i}") for i in range(15)]
        result = summarize_rows(rows, interval_minutes=1)
        assert len(result["top_titles"]) <= 10


class TestBuildLocalNarrative:
    def _summary(
        self,
        categories: list[tuple[str, int]] | None = None,
        apps: list[tuple[str, int]] | None = None,
        total_minutes: int = 60,
    ) -> dict:
        def make_items(pairs):
            return [
                {"name": name, "minutes": m, "hours": round(m / 60, 2)}
                for name, m in (pairs or [])
            ]
        return {
            "estimated_total_minutes": total_minutes,
            "by_category": make_items(categories or [("development", 60)]),
            "by_app": make_items(apps or [("Cursor", 60)]),
        }

    def test_contains_date(self):
        target = date(2025, 1, 15)
        text = build_local_narrative(self._summary(), target)
        assert "2025-01-15" in text

    def test_contains_total_minutes(self):
        text = build_local_narrative(self._summary(total_minutes=120), date(2025, 1, 1))
        assert "120" in text

    def test_contains_top_category(self):
        text = build_local_narrative(self._summary(categories=[("development", 60)]), date(2025, 1, 1))
        assert "development" in text

    def test_contains_top_app(self):
        text = build_local_narrative(self._summary(apps=[("Cursor", 60)]), date(2025, 1, 1))
        assert "Cursor" in text

    def test_empty_categories_shows_no_activity(self):
        summary = self._summary()
        summary["by_category"] = []
        text = build_local_narrative(summary, date(2025, 1, 1))
        assert "no activity" in text

    def test_empty_apps_shows_no_activity(self):
        summary = self._summary()
        summary["by_app"] = []
        text = build_local_narrative(summary, date(2025, 1, 1))
        assert "no activity" in text

    def test_only_top_3_categories_used(self):
        cats = [("a", 60), ("b", 50), ("c", 40), ("d", 30)]
        text = build_local_narrative(self._summary(categories=cats), date(2025, 1, 1))
        assert "d" not in text

    def test_only_top_5_apps_used(self):
        apps = [(f"App{i}", 60 - i * 5) for i in range(7)]
        text = build_local_narrative(self._summary(apps=apps), date(2025, 1, 1))
        assert "App5" not in text
        assert "App6" not in text
