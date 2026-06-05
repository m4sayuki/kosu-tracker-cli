"""Tests for monitor process and PID-file safety."""
from __future__ import annotations

import os
import signal
from unittest.mock import call

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    claim_monitor_pid,
    is_pid_running,
    is_monitor_process,
    monitor_loop,
    process_command,
    read_pid,
    require_not_running,
    stop_monitor,
    unlink_pid_file_if_matches,
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
        cli_module.PID_FILE.write_text("-42", encoding="utf-8")
        assert read_pid() is None


class TestIsPidRunning:
    def test_current_process_is_running(self):
        assert is_pid_running(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        # PID 999999999 は通常存在しない
        assert is_pid_running(999_999_999) is False

    def test_zero_pid_returns_false(self):
        # os.kill(0, 0) はプロセスグループ全体が対象になるため呼ばない。
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False


class TestProcessCommand:
    def test_returns_command_for_successful_ps(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="python -m kosu_tracker.cli run-monitor\n"),
        )
        assert process_command(123) == "python -m kosu_tracker.cli run-monitor"

    def test_returns_none_for_non_positive_pid(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        assert process_command(0) is None
        mock_run.assert_not_called()

    def test_returns_none_when_ps_fails(self, mocker):
        mocker.patch("kosu_tracker.cli.subprocess.run", return_value=mocker.MagicMock(returncode=1, stdout=""))
        assert process_command(123) is None


class TestIsMonitorProcess:
    def test_detects_module_run_monitor_command(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value="python -m kosu_tracker.cli run-monitor --interval 60")
        assert is_monitor_process(123) is True

    def test_detects_console_script_run_monitor_command(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value="/venv/bin/kosu run-monitor --interval 60")
        assert is_monitor_process(123) is True

    def test_rejects_unrelated_run_monitor_command(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value="/tmp/not-kosu-helper run-monitor")
        assert is_monitor_process(123) is False

    def test_rejects_kosu_command_without_run_monitor(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value="/venv/bin/kosu status")
        assert is_monitor_process(123) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_non_monitor_pid_is_treated_as_stale(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()
        assert cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestUnlinkPidFileIfMatches:
    def test_unlinks_matching_pid_file(self):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        unlink_pid_file_if_matches("123")
        assert not cli_module.PID_FILE.exists()

    def test_preserves_newer_pid_file(self):
        cli_module.PID_FILE.write_text("456", encoding="utf-8")
        unlink_pid_file_if_matches("123")
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "456"


class TestClaimMonitorPid:
    def test_creates_pid_file_atomically(self):
        claim_monitor_pid()
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(os.getpid())

    def test_existing_monitor_pid_raises(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            claim_monitor_pid()
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"

    def test_stale_pid_is_replaced(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        claim_monitor_pid()
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(os.getpid())


class TestStopMonitor:
    def test_no_pid_file_raises_not_running(self):
        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

    def test_active_non_monitor_pid_is_not_killed(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_sends_sigterm_and_removes_pid_after_stop(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", side_effect=[True, False])
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_timeout_does_not_remove_pid_file(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")
        mock_sleep = mocker.patch("kosu_tracker.cli.time.sleep")
        mocker.patch("kosu_tracker.cli.STOP_TIMEOUT_SECONDS", 0.4)
        mocker.patch("kosu_tracker.cli.STOP_POLL_SECONDS", 0.2)

        with pytest.raises(SystemExit, match=r"monitor did not stop"):
            stop_monitor()

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        assert mock_sleep.call_args_list == [call(0.2), call(0.2)]
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"

    def test_process_lookup_error_is_treated_as_stopped(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill", side_effect=ProcessLookupError)

        stop_monitor()

        assert not cli_module.PID_FILE.exists()


class TestMonitorLoop:
    def test_rejects_non_positive_interval(self):
        with pytest.raises(SystemExit, match=r"interval must be a positive integer"):
            monitor_loop(0)
        assert not cli_module.PID_FILE.exists()
