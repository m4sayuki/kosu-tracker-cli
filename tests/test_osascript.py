"""Tests for run_osascript, collect_frontmost_app, collect_browser_context."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    collect_browser_context,
    collect_frontmost_app,
    run_osascript,
)


# ── run_osascript ─────────────────────────────────────────────────────────────

class TestRunOsascript:
    def test_success_returns_stripped_stdout(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0, stdout="result\n", stderr="")
        assert run_osascript("dummy script") == "result"

    def test_nonzero_returncode_raises_runtime_error(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="some error")
        with pytest.raises(RuntimeError, match="some error"):
            run_osascript("dummy script")

    def test_nonzero_empty_stderr_uses_fallback_message(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        with pytest.raises(RuntimeError, match="unknown osascript error"):
            run_osascript("dummy script")

    def test_missing_osascript_binary_raises_runtime_error(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.side_effect = FileNotFoundError("osascript")
        with pytest.raises(RuntimeError, match="osascript"):
            run_osascript("dummy script")

    def test_correct_command_passed_to_subprocess(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_osascript("my script")
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "osascript"
        assert call_args[1] == "-e"
        assert call_args[2] == "my script"


# ── collect_frontmost_app ─────────────────────────────────────────────────────

class TestCollectFrontmostApp:
    def test_app_name_and_window_title(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", return_value="Chrome\nDocument.pdf")
        app, title = collect_frontmost_app()
        assert app == "Chrome"
        assert title == "Document.pdf"

    def test_empty_second_line_returns_none_title(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", return_value="Terminal\n")
        app, title = collect_frontmost_app()
        assert app == "Terminal"
        assert title is None

    def test_single_line_output_returns_none_title(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", return_value="Finder")
        app, title = collect_frontmost_app()
        assert app == "Finder"
        assert title is None

    def test_osascript_error_raises_runtime_error(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", side_effect=RuntimeError("accessibility denied"))
        with pytest.raises(RuntimeError):
            collect_frontmost_app()


# ── collect_browser_context ───────────────────────────────────────────────────

class TestCollectBrowserContext:
    def test_non_browser_returns_none_none(self):
        title, url = collect_browser_context("Finder")
        assert title is None
        assert url is None

    def test_firefox_returns_none_none(self):
        title, url = collect_browser_context("Firefox")
        assert title is None
        assert url is None

    def test_safari_returns_title_and_url(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", return_value="Apple\nhttps://apple.com")
        title, url = collect_browser_context("Safari")
        assert title == "Apple"
        assert url == "https://apple.com"

    def test_chrome_returns_title_and_url(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.run_osascript",
            return_value="GitHub\nhttps://github.com",
        )
        title, url = collect_browser_context("Google Chrome")
        assert title == "GitHub"
        assert url == "https://github.com"

    def test_arc_returns_title_and_url(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.run_osascript",
            return_value="Some Page\nhttps://example.com",
        )
        title, url = collect_browser_context("Arc")
        assert title == "Some Page"
        assert url == "https://example.com"

    def test_osascript_error_returns_none_none(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", side_effect=RuntimeError("error"))
        title, url = collect_browser_context("Google Chrome")
        assert title is None
        assert url is None

    def test_empty_output_returns_none_none(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", return_value="")
        title, url = collect_browser_context("Safari")
        assert title is None
        assert url is None

    def test_url_missing_in_output_returns_none_url(self, mocker):
        mocker.patch("kosu_tracker.cli.run_osascript", return_value="Only Title")
        title, url = collect_browser_context("Safari")
        assert title == "Only Title"
        assert url is None

    def test_brave_browser_supported(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.run_osascript",
            return_value="Brave\nhttps://brave.com",
        )
        title, url = collect_browser_context("Brave Browser")
        assert title == "Brave"
        assert url == "https://brave.com"
