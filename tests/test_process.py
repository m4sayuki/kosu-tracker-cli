"""Tests for monitor PID handling and process lifecycle helpers."""
from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

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
    unlink_pid_file_if_owned,
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

    def test_zero_pid_returns_none(self):
        cli_module.PID_FILE.write_text("0", encoding="utf-8")
        assert read_pid() is None

    def test_negative_pid_returns_none(self):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
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
    def test_module_run_monitor_command_matches(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert is_monitor_command(command) is True

    def test_console_script_run_monitor_command_matches(self):
        command = "/tmp/.venv/bin/kosu run-monitor --interval 60"
        assert is_monitor_command(command) is True

    def test_unrelated_python_command_does_not_match(self):
        command = "/usr/bin/python3 -m http.server"
        assert is_monitor_command(command) is False

    def test_process_check_uses_ps_command(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60\n",
        )

        assert is_monitor_process(1234) is True
        mock_run.assert_called_once_with(
            ["ps", "-p", "1234", "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=cli_module.PROCESS_CHECK_TIMEOUT_SECONDS,
        )

    def test_process_check_failure_is_not_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 2))
        assert is_monitor_process(1234) is False


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

    def test_active_unrelated_pid_file_is_deleted(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
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


class TestPidFileOwnership:
    def test_unlink_pid_file_if_owned_removes_matching_pid(self):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        unlink_pid_file_if_owned(123)
        assert not cli_module.PID_FILE.exists()

    def test_unlink_pid_file_if_owned_preserves_replaced_pid(self):
        cli_module.PID_FILE.write_text("456", encoding="utf-8")
        unlink_pid_file_if_owned(123)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "456"


class TestStartMonitor:
    def test_non_positive_interval_is_rejected_before_spawn(self, mocker):
        popen = mocker.patch("kosu_tracker.cli.subprocess.Popen")
        with pytest.raises(SystemExit, match="positive integer"):
            start_monitor(0)
        popen.assert_not_called()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_negative_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_pid_file_is_preserved_when_monitor_does_not_exit(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            stop_monitor()

        kill.assert_called_once_with(1234, cli_module.signal.SIGTERM)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "1234"

    def test_stop_does_not_unlink_replaced_pid_file(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill")

        def mark_stopped(pid):
            cli_module.PID_FILE.write_text("5678", encoding="utf-8")
            return False

        mocker.patch("kosu_tracker.cli.is_pid_running", side_effect=mark_stopped)
        stop_monitor()
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "5678"


class TestMonitorLoop:
    def test_non_positive_interval_is_rejected(self):
        with pytest.raises(SystemExit, match="positive integer"):
            monitor_loop(0)

    def test_finally_does_not_unlink_replaced_pid_file(self, mocker):
        class FakeSample:
            def as_dict(self):
                return {"app_name": "Cursor", "category": "development"}

        def replace_pid_file():
            cli_module.PID_FILE.write_text("9999", encoding="utf-8")
            return FakeSample()

        mocker.patch("kosu_tracker.cli.collect_sample", side_effect=replace_pid_file)
        mocker.patch("kosu_tracker.cli.write_jsonl", side_effect=RuntimeError("disk full"))
        mocker.patch("kosu_tracker.cli.write_latest")

        with pytest.raises(RuntimeError, match="disk full"):
            monitor_loop(1)

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "9999"
