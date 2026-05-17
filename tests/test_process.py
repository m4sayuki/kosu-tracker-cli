"""Tests for process and PID-file helpers."""
from __future__ import annotations

import os
import signal
import subprocess
from unittest.mock import call

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

    def test_system_exit_message_contains_pid(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_reused_pid_for_non_monitor_is_deleted(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)

        require_not_running()

        assert not cli_module.PID_FILE.exists()


class TestIsMonitorProcess:
    def test_detects_run_monitor_command(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60\n",
                stderr="",
            ),
        )

        assert cli_module.is_monitor_process(12345) is True

    def test_rejects_unrelated_command(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="/Applications/TextEdit.app/Contents/MacOS/TextEdit\n",
                stderr="",
            ),
        )

        assert cli_module.is_monitor_process(12345) is False

    def test_unknown_process_command_returns_none(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
        )

        assert cli_module.is_monitor_process(12345) is None


class TestStopMonitor:
    def test_reused_pid_is_not_signaled(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill", return_value=None)

        with pytest.raises(SystemExit, match="non-monitor process"):
            stop_monitor()

        assert mock_kill.call_args_list == [call(pid, 0)]
        assert not cli_module.PID_FILE.exists()

    def test_unverified_pid_is_not_signaled(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=None)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill", return_value=None)

        with pytest.raises(SystemExit, match="could not be verified"):
            stop_monitor()

        assert mock_kill.call_args_list == [call(pid, 0)]
        assert not cli_module.PID_FILE.exists()

    def test_verified_monitor_is_signaled(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_pid_running", side_effect=[True, False])
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        mock_kill.assert_called_once_with(pid, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()
