"""Tests for monitor PID file handling."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    _is_monitor_command,
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


class TestIsMonitorCommand:
    def test_python_module_run_monitor_matches(self):
        assert _is_monitor_command("/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60")

    def test_console_script_run_monitor_matches(self):
        assert _is_monitor_command("/usr/local/bin/kosu run-monitor --interval 60")

    def test_python_console_wrapper_matches(self):
        assert _is_monitor_command('/usr/bin/python3 "/tmp/venv/bin/kosu" run-monitor')

    def test_other_kosu_command_does_not_match(self):
        assert not _is_monitor_command("/usr/local/bin/kosu status")

    def test_unrelated_python_process_does_not_match(self):
        assert not _is_monitor_command("/usr/bin/python3 -m http.server")


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: pid == os.getpid())
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_unrelated_active_pid_file_is_deleted(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_refuses_to_signal_unrelated_live_pid(self, mocker, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="refusing to stop"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert cli_module.PID_FILE.exists()

    def test_negative_pid_file_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()


class TestIntervalValidation:
    def test_monitor_loop_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match="interval must be a positive"):
            monitor_loop(0)

    def test_start_monitor_rejects_negative_interval(self):
        with pytest.raises(SystemExit, match="interval must be a positive"):
            start_monitor(-1)
