"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    command_for_pid,
    is_monitor_process,
    is_pid_running,
    read_pid,
    require_not_running,
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

    def test_zero_pid_does_not_call_kill(self, mocker):
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(0) is False
        mock_kill.assert_not_called()

    def test_negative_pid_returns_false_without_calling_kill(self, mocker):
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(-1) is False
        mock_kill.assert_not_called()


class TestCommandForPid:
    def test_non_running_pid_returns_none(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        assert command_for_pid(12345) is None
        mock_run.assert_not_called()

    def test_running_pid_returns_ps_command(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=mocker.Mock(returncode=0, stdout="python -m kosu_tracker.cli run-monitor --interval 60\n"),
        )
        assert command_for_pid(12345) == "python -m kosu_tracker.cli run-monitor --interval 60"

    def test_ps_failure_returns_none(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.subprocess.run", return_value=mocker.Mock(returncode=1, stdout=""))
        assert command_for_pid(12345) is None


class TestIsMonitorProcess:
    def test_module_invocation_is_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="python -m kosu_tracker.cli run-monitor --interval 60")
        assert is_monitor_process(12345) is True

    def test_console_script_invocation_is_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="/venv/bin/kosu run-monitor --interval 60")
        assert is_monitor_process(12345) is True

    def test_python_console_script_invocation_is_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="/venv/bin/python /venv/bin/kosu run-monitor --interval 60")
        assert is_monitor_process(12345) is True

    def test_unrelated_command_is_not_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="python worker.py")
        assert is_monitor_process(12345) is False

    def test_notes_file_mentioning_kosu_is_not_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="vim 'kosu run-monitor notes.md'")
        assert is_monitor_process(12345) is False

    def test_python_script_with_kosu_arguments_is_not_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="python -c 'import time' kosu run-monitor")
        assert is_monitor_process(12345) is False

    def test_malformed_command_is_not_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.command_for_pid", return_value="python 'unterminated")
        assert is_monitor_process(12345) is False


class TestUnlinkPidFile:
    def test_unlinks_matching_pid_file(self):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        unlink_pid_file(expected_pid=12345)
        assert not cli_module.PID_FILE.exists()

    def test_keeps_replaced_pid_file(self):
        cli_module.PID_FILE.write_text("22222", encoding="utf-8")
        unlink_pid_file(expected_pid=11111)
        assert cli_module.PID_FILE.exists()
        assert read_pid() == 22222


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

    def test_unrelated_live_pid_file_is_deleted(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_is_signalled(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        mock_kill.assert_called_once_with(pid, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_replaced_pid_file_is_not_unlinked_after_signal(self, mocker):
        pid = 12345
        replacement_pid = 54321
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)

        def replace_pid_file(*_args):
            cli_module.PID_FILE.write_text(str(replacement_pid), encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)

        stop_monitor()

        assert cli_module.PID_FILE.exists()
        assert read_pid() == replacement_pid

    def test_running_monitor_pid_file_is_kept_when_signal_does_not_stop_process(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        mock_kill.assert_called_once_with(pid, signal.SIGTERM)
        assert cli_module.PID_FILE.exists()
        assert read_pid() == pid
