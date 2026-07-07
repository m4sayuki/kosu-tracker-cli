"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_pid_running, monitor_loop, read_pid, require_not_running, start_monitor, stop_monitor


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


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_active_non_monitor_pid_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_is_signalled(self, monkeypatch, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda actual_pid: False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        kill.assert_called_once_with(pid, signal.SIGTERM)

    def test_stop_preserves_replaced_pid_file(self, monkeypatch, mocker):
        pid = os.getpid()
        replacement_pid = pid + 1
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda actual_pid: False)

        def replace_pid_file(actual_pid, actual_signal):
            cli_module.PID_FILE.write_text(str(replacement_pid), encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)

        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(replacement_pid)

    def test_stop_failure_preserves_pid_file(self, monkeypatch, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda actual_pid: actual_pid == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda actual_pid: True)
        mocker.patch("kosu_tracker.cli.os.kill")
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)


class TestMonitorIntervalValidation:
    def test_start_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match=r"interval must be positive"):
            start_monitor(0)

    def test_run_monitor_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match=r"interval must be positive"):
            monitor_loop(0)

    def test_run_monitor_rejects_negative_interval(self):
        with pytest.raises(SystemExit, match=r"interval must be positive"):
            monitor_loop(-1)
