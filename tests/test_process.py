"""Tests for PID-file and monitor process handling."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_pid_running, read_pid, require_not_running, stop_monitor


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

    def test_non_positive_pid_returns_none(self):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        assert read_pid() is None
        cli_module.PID_FILE.write_text("0", encoding="utf-8")
        assert read_pid() is None


class TestIsPidRunning:
    def test_current_process_is_running(self):
        assert is_pid_running(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        # PID 999999999 は通常存在しない
        assert is_pid_running(999_999_999) is False

    def test_zero_pid_returns_false_without_signalling(self, monkeypatch):
        def fail_if_called(pid, sig):
            raise AssertionError("os.kill must not be called for pid 0")

        monkeypatch.setattr(cli_module.os, "kill", fail_if_called)
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false_without_signalling(self, monkeypatch):
        def fail_if_called(pid, sig):
            raise AssertionError("os.kill must not be called for negative pids")

        monkeypatch.setattr(cli_module.os, "kill", fail_if_called)
        assert is_pid_running(-1) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: pid == os.getpid())
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_live_unrelated_pid_file_is_deleted(self, monkeypatch):
        pid = os.getpid()
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: False)
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")

        require_not_running()

        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: False)

        kill_calls = []
        monkeypatch.setattr(cli_module.os, "kill", lambda candidate, sig: kill_calls.append((candidate, sig)))

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        assert kill_calls == []
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_gets_sigterm(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monitor_checks = iter([True, False, False])
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: next(monitor_checks))

        kill_calls = []
        monkeypatch.setattr(cli_module.os, "kill", lambda candidate, sig: kill_calls.append((candidate, sig)))

        stop_monitor()

        assert kill_calls == [(pid, signal.SIGTERM)]
        assert not cli_module.PID_FILE.exists()

    def test_replaced_pid_file_is_preserved(self, monkeypatch):
        pid = 12345
        replacement_pid = 67890
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monitor_checks = iter([True, False, False])
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: next(monitor_checks))

        def fake_kill(candidate, sig):
            cli_module.PID_FILE.write_text(str(replacement_pid), encoding="utf-8")

        monkeypatch.setattr(cli_module.os, "kill", fake_kill)

        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(replacement_pid)

    def test_pid_file_remains_when_stop_cannot_confirm_exit(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: True)
        monkeypatch.setattr(cli_module.os, "kill", lambda candidate, sig: None)
        monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: None)

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)
