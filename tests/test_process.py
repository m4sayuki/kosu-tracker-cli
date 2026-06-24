"""Tests for monitor process bookkeeping helpers."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_process,
    is_pid_running,
    monitor_loop,
    print_status,
    read_pid,
    require_not_running,
    start_monitor,
    stop_monitor,
)


@pytest.fixture(autouse=True)
def patch_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pid_file = state_dir / "monitor.pid"
    monkeypatch.setattr(cli_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(cli_module, "PID_FILE", pid_file)
    return pid_file


class TestReadPid:
    def test_no_pid_file_returns_none(self):
        assert read_pid() is None

    def test_valid_pid_file_returns_int(self, tmp_path):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        assert read_pid() == 12345

    def test_pid_file_with_whitespace(self):
        cli_module.PID_FILE.write_text("  99  \n", encoding="utf-8")
        assert read_pid() == 99

    def test_invalid_content_returns_none(self):
        cli_module.PID_FILE.write_text("not-a-number", encoding="utf-8")
        assert read_pid() is None

    def test_empty_file_returns_none(self):
        cli_module.PID_FILE.write_text("", encoding="utf-8")
        assert read_pid() is None


class TestIsPidRunning:
    def test_current_process_is_running(self):
        assert is_pid_running(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        # PID 999999999 は通常存在しない
        assert is_pid_running(999_999_999) is False

    def test_zero_pid_returns_false(self):
        # os.kill(0, 0) はプロセスグループ全体を対象にするため PID としては拒否する
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False


class TestIsMonitorProcess:
    def test_matching_run_monitor_command_returns_true(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(
            cli_module.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="/usr/bin/python -m kosu_tracker.cli run-monitor --interval 60\n",
            ),
        )

        assert is_monitor_process(12345) is True

    def test_unrelated_live_process_returns_false(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(
            cli_module.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="sleep 600\n"),
        )

        assert is_monitor_process(12345) is False

    def test_dead_process_returns_false_without_ps(self, monkeypatch):
        called = False

        def fake_run(*args, **kwargs):
            nonlocal called
            called = True

        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: False)
        monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

        assert is_monitor_process(12345) is False
        assert called is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_live_pid_file_is_deleted(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda process_id: True)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStartMonitor:
    def test_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match="interval must be greater than 0 seconds"):
            start_monitor(0)

    def test_rejects_negative_interval(self):
        with pytest.raises(SystemExit, match="interval must be greater than 0 seconds"):
            start_monitor(-1)


class TestMonitorLoop:
    def test_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match="interval must be greater than 0 seconds"):
            monitor_loop(0)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signaled(self, monkeypatch):
        pid = os.getpid()
        signaled: list[tuple[int, int]] = []
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda process_id: False)
        monkeypatch.setattr(cli_module.os, "kill", lambda process_id, sig: signaled.append((process_id, sig)))

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        assert signaled == []
        assert not cli_module.PID_FILE.exists()

    def test_failed_stop_keeps_pid_file(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda process_id: True)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda process_id: True)
        monkeypatch.setattr(cli_module.os, "kill", lambda process_id, sig: None)
        monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: None)

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)


class TestPrintStatus:
    def test_unrelated_live_pid_reports_not_running(self, monkeypatch, capsys):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda process_id: False)

        print_status()

        output = capsys.readouterr().out
        assert "running: no" in output
        assert "pid:" not in output
