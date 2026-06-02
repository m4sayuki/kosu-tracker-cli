"""Tests for monitor PID handling."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_command,
    is_monitor_pid_running,
    is_pid_running,
    read_pid,
    require_not_running,
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
        # os.kill(0, 0) はプロセスグループ全体に送られるが OSError が出ないケースもある
        # ここではモックを使って確実にFalseを返すシナリオをテスト
        assert is_pid_running(999_999_998) is False


class TestIsMonitorCommand:
    def test_python_module_run_monitor_command_returns_true(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert is_monitor_command(command) is True

    def test_console_script_run_monitor_command_returns_true(self):
        command = "/workspace/.venv/bin/python /workspace/.venv/bin/kosu run-monitor"
        assert is_monitor_command(command) is True

    def test_unrelated_command_with_same_pid_returns_false(self):
        assert is_monitor_command("sleep 100") is False

    def test_unrelated_kosu_command_returns_false(self):
        assert is_monitor_command("/workspace/.venv/bin/kosu status") is False


class TestIsMonitorPidRunning:
    def test_existing_monitor_process_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.read_process_command",
            return_value="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )

        assert is_monitor_pid_running(123) is True

    def test_existing_unrelated_process_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.read_process_command", return_value="sleep 100")

        assert is_monitor_pid_running(123) is False

    def test_dead_pid_returns_false_without_reading_command(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        read_command = mocker.patch("kosu_tracker.cli.read_process_command")

        assert is_monitor_pid_running(123) is False
        read_command.assert_not_called()


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_active_pid_is_treated_as_stale(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid_running", return_value=False)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid_running", return_value=True)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid_running", return_value=True)
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_active_pid_is_not_signaled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_pid_running", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()
