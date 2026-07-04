"""Tests for monitor PID helpers and process lifecycle controls."""
from __future__ import annotations

import os
import signal

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
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false_without_signalling_process_group(self, mocker):
        mock_kill = mocker.patch("os.kill")

        assert is_pid_running(-1) is False
        mock_kill.assert_not_called()


class TestIsMonitorProcess:
    def test_python_module_run_monitor_command_is_recognized(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )

        assert cli_module.is_monitor_process(1234) is True

    def test_console_script_run_monitor_command_is_recognized(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="/usr/local/bin/kosu run-monitor --interval 60",
        )

        assert cli_module.is_monitor_process(1234) is True

    def test_unrelated_command_is_not_recognized(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value="/bin/sleep 60")

        assert cli_module.is_monitor_process(1234) is False

    def test_shell_command_containing_monitor_tokens_is_not_recognized(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="/bin/sh -c 'echo python -m kosu_tracker.cli run-monitor'",
        )

        assert cli_module.is_monitor_process(1234) is False


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

    def test_unrelated_live_pid_file_is_deleted(self, mocker):
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


class TestStopMonitor:
    def test_does_not_signal_unrelated_live_pid(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        mock_kill = mocker.patch("os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_negative_pid_file_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        mock_kill = mocker.patch("os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()

    def test_stop_sends_sigterm_to_verified_monitor(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mock_kill = mocker.patch("os.kill")
        mocker.patch("kosu_tracker.cli.is_pid_running", side_effect=[False, False])

        stop_monitor()

        mock_kill.assert_called_once_with(1234, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_pid_file_is_preserved_when_monitor_does_not_stop(self, monkeypatch, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        monkeypatch.setattr(cli_module, "MONITOR_STOP_TIMEOUT_SECONDS", 0)
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("os.kill")
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "1234"

    def test_replaced_pid_file_is_not_unlinked_after_stop(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)

        def replace_pid_file(pid, signum):
            cli_module.PID_FILE.write_text("5678", encoding="utf-8")

        mocker.patch("os.kill", side_effect=replace_pid_file)
        mocker.patch("kosu_tracker.cli.is_pid_running", side_effect=[False, False])

        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "5678"
