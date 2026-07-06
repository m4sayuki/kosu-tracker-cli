"""Tests for monitor process and PID-file handling."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    command_is_monitor_process,
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


class TestCommandIsMonitorProcess:
    def test_python_module_run_monitor_matches(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert command_is_monitor_process(command) is True

    def test_console_script_run_monitor_matches(self):
        command = "/tmp/venv/bin/python /tmp/venv/bin/kosu run-monitor --interval 60"
        assert command_is_monitor_process(command) is True

    def test_unrelated_process_does_not_match(self):
        assert command_is_monitor_process("sleep 100") is False

    def test_text_mentioning_run_monitor_does_not_match(self):
        assert command_is_monitor_process("python -c 'print(\"kosu run-monitor\")'") is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_unrelated_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_raises_system_exit(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_pid_file_preserved_when_monitor_does_not_exit(self, mocker, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda candidate: candidate == pid)
        mocker.patch("kosu_tracker.cli.time.sleep")
        mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)

    def test_replaced_pid_file_is_not_deleted_after_stop(self, mocker, monkeypatch):
        pid = 12345
        replacement_pid = 67890
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda candidate: False)

        def replace_pid_file(_pid, _signal):
            cli_module.PID_FILE.write_text(str(replacement_pid), encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)
        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(replacement_pid)


class TestIntervalValidation:
    def test_start_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match="interval must be a positive"):
            start_monitor(0)

    def test_monitor_loop_rejects_negative_interval(self):
        with pytest.raises(SystemExit, match="interval must be a positive"):
            monitor_loop(-1)
