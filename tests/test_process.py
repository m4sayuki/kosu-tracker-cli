"""Tests for monitor process helpers."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_command,
    is_monitor_process,
    is_pid_running,
    monitor_loop,
    read_pid,
    require_not_running,
    start_monitor,
    stop_monitor,
    unlink_pid_file,
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


class TestIsMonitorCommand:
    def test_python_module_run_monitor_command_returns_true(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert is_monitor_command(command) is True

    def test_command_containing_words_but_not_module_invocation_returns_false(self):
        command = "sh -c 'echo python -m kosu_tracker.cli run-monitor'"
        assert is_monitor_command(command) is False


class TestIsMonitorProcess:
    def test_running_process_with_monitor_command_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )
        assert is_monitor_process(1234) is True

    def test_running_process_with_unrelated_command_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.process_command", return_value="/bin/sleep 60")
        assert is_monitor_process(1234) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_live_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_raises_system_exit(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestUnlinkPidFile:
    def test_deletes_matching_pid_file(self):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        unlink_pid_file(123)
        assert not cli_module.PID_FILE.exists()

    def test_preserves_replaced_pid_file(self):
        cli_module.PID_FILE.write_text("456", encoding="utf-8")
        unlink_pid_file(123)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "456"


class TestStartMonitor:
    def test_rejects_zero_interval_before_spawning(self, mocker):
        popen = mocker.patch("kosu_tracker.cli.subprocess.Popen")
        with pytest.raises(SystemExit, match="positive"):
            start_monitor(0)
        popen.assert_not_called()


class TestMonitorLoop:
    def test_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match="positive"):
            monitor_loop(0)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_preserves_pid_file_when_monitor_does_not_exit(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill")
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"
