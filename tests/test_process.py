"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal
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
    def test_python_module_command_is_monitor(self):
        tokens = ["python3.12", "-m", "kosu_tracker.cli", "run-monitor", "--interval", "60"]
        assert is_monitor_command(tokens) is True

    def test_console_script_command_is_monitor(self):
        tokens = ["/tmp/.venv/bin/kosu", "run-monitor", "--interval", "60"]
        assert is_monitor_command(tokens) is True

    def test_python_launched_console_script_command_is_monitor(self):
        tokens = ["python3", "/tmp/.venv/bin/kosu", "run-monitor", "--interval", "60"]
        assert is_monitor_command(tokens) is True

    def test_unrelated_command_is_not_monitor(self):
        assert is_monitor_command(["sleep", "999"]) is False

    def test_command_with_run_monitor_text_is_not_monitor(self):
        assert is_monitor_command(["python3", "-c", "print('run-monitor')"]) is False

    def test_is_monitor_process_uses_ps_command(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=MagicMock(
                returncode=0,
                stdout="python3 -m kosu_tracker.cli run-monitor --interval 60\n",
            ),
        )

        assert is_monitor_process(12345) is True

    def test_is_monitor_process_rejects_unrelated_live_pid(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="sleep 999\n"),
        )

        assert is_monitor_process(12345) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_unrelated_live_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, mocker):
        pid = 12345
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_replaced_pid_file_is_preserved(self, mocker):
        old_pid = 12345
        new_pid = 67890
        cli_module.PID_FILE.write_text(str(old_pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", side_effect=[True, False, False])

        def replace_pid_file(pid, sig):
            assert pid == old_pid
            assert sig == signal.SIGTERM
            cli_module.PID_FILE.write_text(str(new_pid), encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)

        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(new_pid)

    def test_pid_file_preserved_when_monitor_does_not_stop(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill")
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)


class TestMonitorIntervals:
    def test_start_monitor_rejects_zero_interval(self, mocker):
        popen = mocker.patch("kosu_tracker.cli.subprocess.Popen")

        with pytest.raises(SystemExit, match=r"interval must be a positive integer"):
            start_monitor(0)

        popen.assert_not_called()

    def test_monitor_loop_rejects_zero_interval(self, mocker):
        collect = mocker.patch("kosu_tracker.cli.collect_sample")

        with pytest.raises(SystemExit, match=r"interval must be a positive integer"):
            monitor_loop(0)

        collect.assert_not_called()
