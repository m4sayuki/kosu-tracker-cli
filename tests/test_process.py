"""Tests for PID file and monitor process handling."""
from __future__ import annotations

import os
import signal
import subprocess

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_process,
    is_pid_running,
    read_pid,
    require_not_running,
    stop_monitor,
    unlink_pid_file_if_owned,
    validate_interval_seconds,
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

    def test_zero_pid_returns_false_without_signal(self, monkeypatch):
        def fail_if_called(pid, sig):
            raise AssertionError("os.kill should not be called for pid 0")

        monkeypatch.setattr(cli_module.os, "kill", fail_if_called)
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false_without_signal(self, monkeypatch):
        def fail_if_called(pid, sig):
            raise AssertionError("os.kill should not be called for negative pids")

        monkeypatch.setattr(cli_module.os, "kill", fail_if_called)
        assert is_pid_running(-1) is False

    def test_permission_error_means_process_exists(self, monkeypatch):
        def raise_permission(pid, sig):
            raise PermissionError

        monkeypatch.setattr(cli_module.os, "kill", raise_permission)
        assert is_pid_running(123) is True


class TestIsMonitorProcess:
    def test_python_module_run_monitor_matches(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        completed = subprocess.CompletedProcess(
            args=["ps"],
            returncode=0,
            stdout="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60\n",
            stderr="",
        )
        monkeypatch.setattr(cli_module.subprocess, "run", lambda *args, **kwargs: completed)

        assert is_monitor_process(123) is True

    def test_console_script_run_monitor_matches(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        completed = subprocess.CompletedProcess(
            args=["ps"],
            returncode=0,
            stdout="/workspace/.venv/bin/kosu run-monitor --interval 60\n",
            stderr="",
        )
        monkeypatch.setattr(cli_module.subprocess, "run", lambda *args, **kwargs: completed)

        assert is_monitor_process(123) is True

    def test_unrelated_live_pid_is_not_monitor(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        completed = subprocess.CompletedProcess(
            args=["ps"],
            returncode=0,
            stdout="/usr/bin/python3 important_worker.py\n",
            stderr="",
        )
        monkeypatch.setattr(cli_module.subprocess, "run", lambda *args, **kwargs: completed)

        assert is_monitor_process(123) is False

    def test_missing_process_command_is_not_monitor(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        completed = subprocess.CompletedProcess(args=["ps"], returncode=1, stdout="", stderr="")
        monkeypatch.setattr(cli_module.subprocess, "run", lambda *args, **kwargs: completed)

        assert is_monitor_process(123) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_active_pid_is_deleted(self, monkeypatch):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)

        require_not_running()

        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: pid == os.getpid())
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, monkeypatch, mocker):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_receives_sigterm(self, monkeypatch, mocker):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: pid == 123)
        running_states = iter([True, False])
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: next(running_states))
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        kill_mock.assert_called_once_with(123, signal.SIGTERM)


class TestPidFileOwnership:
    def test_unlinks_when_owned_by_pid(self):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")

        unlink_pid_file_if_owned(123)

        assert not cli_module.PID_FILE.exists()

    def test_keeps_pid_file_owned_by_different_pid(self):
        cli_module.PID_FILE.write_text("456", encoding="utf-8")

        unlink_pid_file_if_owned(123)

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "456"


class TestValidateIntervalSeconds:
    def test_positive_interval_is_allowed(self):
        validate_interval_seconds(1)

    @pytest.mark.parametrize("interval", [0, -1])
    def test_non_positive_interval_exits(self, interval):
        with pytest.raises(SystemExit, match="interval must be greater than 0"):
            validate_interval_seconds(interval)
