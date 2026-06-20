"""Tests for monitor process state helpers."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    claim_pid_file,
    is_monitor_process,
    is_pid_running,
    read_pid,
    require_not_running,
    stop_monitor,
    validate_interval,
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


class TestIsMonitorProcess:
    def test_returns_true_for_run_monitor_command(self, mocker):
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="/usr/bin/python -m kosu_tracker.cli run-monitor --interval 60",
        )
        assert is_monitor_process(123) is True

    def test_returns_false_for_unrelated_command(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value="/bin/sleep 999")
        assert is_monitor_process(123) is False

    def test_returns_false_when_command_cannot_be_read(self, mocker):
        mocker.patch("kosu_tracker.cli.process_command", return_value=None)
        assert is_monitor_process(123) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_unrelated_live_pid_is_treated_as_stale(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()


class TestClaimPidFile:
    def test_claim_writes_pid_atomically(self):
        claim_pid_file(12345)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"

    def test_existing_monitor_pid_is_rejected(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            claim_pid_file(99999)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"


class TestValidateInterval:
    def test_positive_interval_is_allowed(self):
        validate_interval(1)

    @pytest.mark.parametrize("interval", [0, -1])
    def test_non_positive_interval_exits(self, interval):
        with pytest.raises(SystemExit, match=r"interval must be at least 1 second"):
            validate_interval(interval)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_timeout_preserves_pid_file_for_retry(self, mocker, monkeypatch):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        monkeypatch.setattr(cli_module, "STOP_TIMEOUT_SECONDS", 0)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        kill.assert_called_once_with(12345, signal.SIGTERM)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"
