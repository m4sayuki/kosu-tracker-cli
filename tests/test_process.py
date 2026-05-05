"""Tests for read_pid, is_pid_running, require_not_running, and stop_monitor."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_monitor_pid_running, is_pid_running, read_pid, require_not_running, stop_monitor


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
        # os.kill(0, 0) はプロセスグループ全体に送られるが OSError が出ないケースもある
        # ここではモックを使って確実にFalseを返すシナリオをテスト
        assert is_pid_running(999_999_998) is False


class TestIsMonitorPidRunning:
    def test_matching_monitor_command_returns_true(self, monkeypatch):
        pid = os.getpid()
        monkeypatch.setattr(
            cli_module,
            "get_pid_command",
            lambda checked_pid: "python -m kosu_tracker.cli run-monitor --interval 60",
        )

        assert is_monitor_pid_running(pid) is True

    def test_reused_pid_with_unrelated_command_returns_false(self, monkeypatch):
        pid = os.getpid()
        monkeypatch.setattr(cli_module, "get_pid_command", lambda checked_pid: "python unrelated_worker.py")

        assert is_monitor_pid_running(pid) is False

    def test_missing_command_returns_false(self, monkeypatch):
        pid = os.getpid()
        monkeypatch.setattr(cli_module, "get_pid_command", lambda checked_pid: None)

        assert is_monitor_pid_running(pid) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_reused_pid_file_is_deleted(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "get_pid_command", lambda pid: "python unrelated_worker.py")

        require_not_running()

        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(
            cli_module,
            "get_pid_command",
            lambda checked_pid: "python -m kosu_tracker.cli run-monitor --interval 60",
        )

        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(
            cli_module,
            "get_pid_command",
            lambda checked_pid: "python -m kosu_tracker.cli run-monitor --interval 60",
        )

        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_reused_pid_does_not_signal_unrelated_process(self, monkeypatch, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_pid_running", lambda checked_pid: True)
        monkeypatch.setattr(cli_module, "get_pid_command", lambda checked_pid: "python unrelated_worker.py")
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_gets_sigterm(self, monkeypatch, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(
            cli_module,
            "get_pid_command",
            lambda checked_pid: "python -m kosu_tracker.cli run-monitor --interval 60",
        )
        monkeypatch.setattr(cli_module, "is_pid_running", mocker.Mock(side_effect=[True, False]))
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")
        print_mock = mocker.patch("builtins.print")

        stop_monitor()

        kill_mock.assert_called_once_with(pid, signal.SIGTERM)
        print_mock.assert_called_once_with(f"stopped monitor (pid={pid})")
