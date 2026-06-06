"""Tests for monitor process PID handling."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
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

    def test_zero_pid_returns_false_without_signal(self, mocker):
        kill = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(0) is False
        kill.assert_not_called()

    def test_negative_pid_returns_false_without_signal(self, mocker):
        kill = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(-1) is False
        kill.assert_not_called()


class TestIsMonitorProcess:
    def test_module_run_monitor_process_returns_true(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(
            cli_module,
            "process_command",
            lambda pid: "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )
        assert is_monitor_process(12345) is True

    def test_installed_kosu_run_monitor_process_returns_true(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_module, "process_command", lambda pid: "/usr/local/bin/kosu run-monitor")
        assert is_monitor_process(12345) is True

    def test_running_non_monitor_process_returns_false(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_module, "process_command", lambda pid: "/usr/bin/python3 unrelated.py")
        assert is_monitor_process(12345) is False

    def test_dead_process_returns_false_without_command_lookup(self, mocker, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: False)
        process_command = mocker.patch("kosu_tracker.cli.process_command")
        assert is_monitor_process(12345) is False
        process_command.assert_not_called()


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_running_non_monitor_pid_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(
            cli_module,
            "process_command",
            lambda process_pid: "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()
        assert cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(
            cli_module,
            "process_command",
            lambda process_pid: "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestUnlinkPidFile:
    def test_unlinks_when_expected_pid_matches(self):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        unlink_pid_file(expected_pid=123)
        assert not cli_module.PID_FILE.exists()

    def test_keeps_file_when_expected_pid_differs(self):
        cli_module.PID_FILE.write_text("456", encoding="utf-8")
        unlink_pid_file(expected_pid=123)
        assert cli_module.PID_FILE.exists()
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "456"


class TestStopMonitor:
    def test_negative_pid_is_not_signaled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        kill = mocker.patch("kosu_tracker.cli.os.kill")
        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()
        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_running_non_monitor_pid_is_not_signaled(self, mocker, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "process_command", lambda process_pid: "/usr/bin/python3 unrelated.py")
        kill = mocker.patch("kosu_tracker.cli.os.kill")
        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()
        kill.assert_called_once_with(pid, 0)
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_receives_sigterm(self, mocker, monkeypatch, capsys):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        states = iter([True, False])
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda process_pid: next(states))
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        kill.assert_called_once_with(pid, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()
        assert f"stopped monitor (pid={pid})" in capsys.readouterr().out
