"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal
import subprocess

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    get_process_command,
    is_monitor_process,
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
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False


class TestMonitorProcessDetection:
    def test_get_process_command_returns_command(self, mocker):
        completed = subprocess.CompletedProcess(
            args=["ps"],
            returncode=0,
            stdout="python -m kosu_tracker.cli run-monitor --interval 60\n",
            stderr="",
        )
        mocker.patch("subprocess.run", return_value=completed)

        assert get_process_command(123) == "python -m kosu_tracker.cli run-monitor --interval 60"

    def test_get_process_command_returns_none_on_ps_error(self, mocker):
        mocker.patch("subprocess.run", side_effect=OSError("ps unavailable"))

        assert get_process_command(123) is None

    def test_monitor_process_requires_run_monitor_command(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.get_process_command", return_value="python -m kosu_tracker.cli run-monitor")

        assert is_monitor_process(123) is True

    def test_running_unrelated_process_is_not_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.get_process_command", return_value="python important-job.py")

        assert is_monitor_process(123) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_running_unrelated_pid_is_deleted_as_stale(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)

        require_not_running()

        assert not cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_reused_pid_is_not_signaled(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill_mock = mocker.patch("os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_is_signaled(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", side_effect=[False])
        kill_mock = mocker.patch("os.kill")
        mocker.patch("builtins.print")

        stop_monitor()

        kill_mock.assert_called_once_with(12345, signal.SIGTERM)
