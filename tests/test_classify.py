"""Tests for classify_activity and extract_domain."""
from __future__ import annotations

import pytest

from kosu_tracker.cli import classify_activity, extract_domain, KNOWN_BROWSERS


class TestClassifyActivity:
    # ── ブラウザ × URLドメイン ──────────────────────────────────────────
    @pytest.mark.parametrize("browser", sorted(KNOWN_BROWSERS - {"Firefox"}))
    @pytest.mark.parametrize(
        "url",
        ["https://slack.com/messages", "https://discord.com/channels/1", "https://claude.ai/"],
    )
    def test_browser_communication_domains(self, browser, url):
        assert classify_activity(browser, None, url) == "communication"

    @pytest.mark.parametrize("browser", sorted(KNOWN_BROWSERS - {"Firefox"}))
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/org/repo",
            "https://gitlab.com/proj",
            "https://docs.python.org/3/",
            "https://developer.mozilla.org/en-US/",
            "https://stackoverflow.com/questions/1",
        ],
    )
    def test_browser_research_domains(self, browser, url):
        assert classify_activity(browser, None, url) == "research"

    @pytest.mark.parametrize("browser", sorted(KNOWN_BROWSERS - {"Firefox"}))
    def test_browser_unknown_url_returns_browser(self, browser):
        assert classify_activity(browser, None, "https://example.com") == "browser"

    @pytest.mark.parametrize("browser", sorted(KNOWN_BROWSERS - {"Firefox"}))
    def test_browser_no_url_returns_browser(self, browser):
        assert classify_activity(browser, "Some Title", None) == "browser"

    # ── 開発ツール ──────────────────────────────────────────────────────
    @pytest.mark.parametrize(
        "app_name",
        ["cursor", "Cursor", "Visual Studio Code", "code", "Vim", "Emacs", "Xcode", "Android Studio", "PyCharm", "IntelliJ IDEA"],
    )
    def test_development_apps(self, app_name):
        assert classify_activity(app_name, None, None) == "development"

    # ── ターミナル ──────────────────────────────────────────────────────
    @pytest.mark.parametrize("app_name", ["Terminal", "iTerm2", "Warp"])
    def test_terminal_apps(self, app_name):
        assert classify_activity(app_name, None, None) == "terminal"

    # ── コミュニケーション ──────────────────────────────────────────────
    @pytest.mark.parametrize("app_name", ["Slack", "Discord", "Microsoft Teams", "zoom"])
    def test_communication_apps(self, app_name):
        assert classify_activity(app_name, None, None) == "communication"

    # ── デザイン ───────────────────────────────────────────────────────
    @pytest.mark.parametrize("app_name", ["Figma", "Sketch", "Photoshop"])
    def test_design_apps(self, app_name):
        assert classify_activity(app_name, None, None) == "design"

    # ── ウィンドウタイトルによるミーティング検出 ─────────────────────────
    def test_meeting_in_window_title(self):
        assert classify_activity("UnknownApp", "Daily meeting", None) == "meeting"

    def test_meeting_keyword_case_insensitive(self):
        assert classify_activity("UnknownApp", "MEETING notes", None) == "meeting"

    # ── フォールバック ─────────────────────────────────────────────────
    def test_unknown_app_returns_other(self):
        assert classify_activity("SomeRandomApp", None, None) == "other"

    def test_unknown_app_with_title_no_meeting_returns_other(self):
        assert classify_activity("SomeRandomApp", "Ordinary Window", None) == "other"


class TestExtractDomain:
    def test_https_url(self):
        assert extract_domain("https://github.com/org/repo") == "github.com"

    def test_http_url_with_path(self):
        assert extract_domain("http://example.com/path/file") == "example.com"

    def test_url_without_path(self):
        assert extract_domain("https://example.com") == "example.com"

    def test_none_returns_none(self):
        assert extract_domain(None) is None

    def test_empty_string_returns_none(self):
        assert extract_domain("") is None

    def test_no_scheme_returns_none(self):
        assert extract_domain("no-scheme-at-all") is None

    def test_url_with_port(self):
        assert extract_domain("http://localhost:8080/api") == "localhost:8080"
