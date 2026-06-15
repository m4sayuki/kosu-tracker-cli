"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    command_is_monitor,
    is_pid_running,
    print_status,
    read_pid,
    remove_pid_file,
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
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False


class TestCommandIsMonitor:
    def test_python_module_run_monitor_matches(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert command_is_monitor(command) is True

    def test_console_script_run_monitor_matches(self):
        command = "/venv/bin/kosu run-monitor --interval 60"
        assert command_is_monitor(command) is True

    def test_unrelated_run_monitor_does_not_match(self):
        assert command_is_monitor("/tmp/other run-monitor") is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_active_pid_file_is_deleted(self, monkeypatch):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_raises_system_exit(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestRemovePidFile:
    def test_expected_pid_mismatch_preserves_file(self):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        remove_pid_file(expected_pid=99999)
        assert cli_module.PID_FILE.exists()

    def test_expected_pid_match_deletes_file(self):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        remove_pid_file(expected_pid=12345)
        assert not cli_module.PID_FILE.exists()


class TestStartMonitor:
    def test_rejects_zero_interval_before_spawning(self, monkeypatch):
        popen = monkeypatch.setattr(cli_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("spawned"))
        with pytest.raises(SystemExit, match=r"interval must be greater than 0"):
            start_monitor(0)
        assert popen is None

    def test_rejects_negative_interval_before_spawning(self, monkeypatch):
        monkeypatch.setattr(cli_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("spawned"))
        with pytest.raises(SystemExit, match=r"interval must be greater than 0"):
            start_monitor(-1)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, monkeypatch):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)

        def fail_kill(pid, sig):
            pytest.fail("stop_monitor must not signal unrelated processes")

        monkeypatch.setattr(cli_module.os, "kill", fail_kill)
        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_that_does_not_exit_keeps_pid_file(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda actual_pid: actual_pid == pid)
        monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: None)
        signals: list[tuple[int, int]] = []

        def fake_kill(actual_pid, sig):
            signals.append((actual_pid, sig))

        monkeypatch.setattr(cli_module.os, "kill", fake_kill)
        with pytest.raises(SystemExit, match=r"monitor did not stop within timeout"):
            stop_monitor()
        assert signals == [(pid, signal.SIGTERM)]
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)

    def test_vanished_monitor_pid_file_is_deleted(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)

        def fake_kill(actual_pid, sig):
            raise ProcessLookupError

        monkeypatch.setattr(cli_module.os, "kill", fake_kill)
        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()
        assert not cli_module.PID_FILE.exists()


class TestPrintStatus:
    def test_unrelated_live_pid_is_not_reported_running(self, monkeypatch, capsys):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        print_status()
        captured = capsys.readouterr()
        assert "running: no" in captured.out
        assert "pid: 12345" not in captured.out
