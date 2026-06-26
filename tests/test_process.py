"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_process,
    is_pid_running,
    monitor_loop,
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
        # os.kill(0, 0) targets the process group, so PID files must reject it explicitly.
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False


class TestIsMonitorProcess:
    def test_run_monitor_command_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="/usr/bin/python -m kosu_tracker.cli run-monitor --interval 60\n"),
        )

        assert is_monitor_process(12345) is True

    def test_unrelated_live_process_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="sleep 999\n"),
        )

        assert is_monitor_process(12345) is False

    def test_missing_process_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)

        assert is_monitor_process(12345) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_unrelated_live_pid_is_deleted_without_error(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)

        require_not_running()

        assert not cli_module.PID_FILE.exists()


class TestStartMonitor:
    @pytest.mark.parametrize("interval", [0, -1])
    def test_rejects_non_positive_interval(self, interval, mocker):
        mock_popen = mocker.patch("kosu_tracker.cli.subprocess.Popen")

        with pytest.raises(SystemExit, match="interval must be greater than 0"):
            start_monitor(interval)

        mock_popen.assert_not_called()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_pid_file_preserved_when_monitor_does_not_exit(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill")
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"


class TestMonitorLoop:
    @pytest.mark.parametrize("interval", [0, -10])
    def test_rejects_non_positive_interval_before_writing_pid(self, interval):
        with pytest.raises(SystemExit, match="interval must be greater than 0"):
            monitor_loop(interval)

        assert not cli_module.PID_FILE.exists()
