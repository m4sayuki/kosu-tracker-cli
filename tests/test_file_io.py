"""Tests for write_jsonl, write_latest, and iter_logs_for_date."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import iter_logs_for_date, write_jsonl, write_latest


@pytest.fixture(autouse=True)
def patch_dirs(tmp_path, monkeypatch):
    """LOG_DIR と STATE_DIR を一時ディレクトリに差し替える。"""
    log_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    log_dir.mkdir()
    state_dir.mkdir()
    monkeypatch.setattr(cli_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(cli_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(cli_module, "LATEST_FILE", state_dir / "latest.json")
    return log_dir, state_dir


class TestWriteJsonl:
    def test_creates_file_with_one_line(self, tmp_path):
        path = tmp_path / "test.jsonl"
        write_jsonl(path, {"key": "value"})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"key": "value"}

    def test_appends_to_existing_file(self, tmp_path):
        path = tmp_path / "test.jsonl"
        write_jsonl(path, {"a": 1})
        write_jsonl(path, {"b": 2})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"b": 2}

    def test_unicode_preserved(self, tmp_path):
        path = tmp_path / "test.jsonl"
        write_jsonl(path, {"msg": "日本語テスト"})
        data = json.loads(path.read_text(encoding="utf-8").strip())
        assert data["msg"] == "日本語テスト"

    def test_none_values_written(self, tmp_path):
        path = tmp_path / "test.jsonl"
        write_jsonl(path, {"key": None})
        data = json.loads(path.read_text(encoding="utf-8").strip())
        assert data["key"] is None


class TestWriteLatest:
    def test_creates_latest_json(self, monkeypatch, tmp_path):
        latest_file = tmp_path / "state" / "latest.json"
        monkeypatch.setattr(cli_module, "LATEST_FILE", latest_file)
        payload = {"app_name": "Cursor", "category": "development"}
        write_latest(payload)
        assert latest_file.exists()
        loaded = json.loads(latest_file.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_overwrites_existing_latest(self, monkeypatch, tmp_path):
        latest_file = tmp_path / "state" / "latest.json"
        monkeypatch.setattr(cli_module, "LATEST_FILE", latest_file)
        write_latest({"old": True})
        write_latest({"new": True})
        loaded = json.loads(latest_file.read_text(encoding="utf-8"))
        assert "new" in loaded
        assert "old" not in loaded


class TestIterLogsForDate:
    def test_no_log_file_returns_empty_list(self):
        result = iter_logs_for_date(date(2099, 1, 1))
        assert result == []

    def test_reads_valid_jsonl(self, monkeypatch):
        log_dir = cli_module.LOG_DIR
        log_file = log_dir / "2025-01-15.jsonl"
        rows = [{"app_name": "Chrome", "category": "browser"}, {"app_name": "Cursor", "category": "development"}]
        log_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        result = iter_logs_for_date(date(2025, 1, 15))
        assert len(result) == 2
        assert result[0]["app_name"] == "Chrome"
        assert result[1]["app_name"] == "Cursor"

    def test_empty_lines_skipped(self, monkeypatch):
        log_dir = cli_module.LOG_DIR
        log_file = log_dir / "2025-02-01.jsonl"
        log_file.write_text(
            '{"app_name": "Finder"}\n\n{"app_name": "Slack"}\n\n',
            encoding="utf-8",
        )
        result = iter_logs_for_date(date(2025, 2, 1))
        assert len(result) == 2

    def test_single_row(self):
        log_dir = cli_module.LOG_DIR
        log_file = log_dir / "2025-03-10.jsonl"
        log_file.write_text('{"app_name": "Terminal"}\n', encoding="utf-8")
        result = iter_logs_for_date(date(2025, 3, 10))
        assert len(result) == 1
        assert result[0]["app_name"] == "Terminal"
