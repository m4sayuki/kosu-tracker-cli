"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    command_is_monitor_process,
    is_monitor_process,
    is_pid_running,
    monitor_loop,
    read_pid,
    require_not_running,
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
    def test_python_module_run_monitor_command(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert command_is_monitor_process(command) is True

    def test_console_script_run_monitor_command(self):
        command = "/tmp/venv/bin/kosu run-monitor --interval 60"
        assert command_is_monitor_process(command) is True

    def test_unrelated_process_with_run_monitor_argument_is_not_monitor(self):
        command = "/usr/bin/python3 worker.py run-monitor"
        assert command_is_monitor_process(command) is False

    def test_is_monitor_process_uses_command_lookup(self, monkeypatch):
        monkeypatch.setattr(
            cli_module,
            "process_command",
            lambda pid: "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )
        assert is_monitor_process(12345) is True


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_live_non_monitor_pid_is_deleted_as_stale(self, monkeypatch):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: False)

        def fail_if_called(candidate: int, sig: int) -> None:
            raise AssertionError("os.kill should not be called for non-monitor processes")

        monkeypatch.setattr(cli_module.os, "kill", fail_if_called)

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        assert not cli_module.PID_FILE.exists()

    def test_pid_file_kept_when_process_survives_sigterm(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda candidate: True)
        monkeypatch.setattr(cli_module.os, "kill", lambda candidate, sig: None)
        monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: None)

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)

    def test_replaced_pid_file_is_not_unlinked_after_stop(self, monkeypatch, capsys):
        original_pid = 12345
        replacement_pid = 67890
        cli_module.PID_FILE.write_text(str(original_pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == original_pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda candidate: False)

        def replace_pid_file(candidate: int, sig: int) -> None:
            assert candidate == original_pid
            assert sig == signal.SIGTERM
            cli_module.PID_FILE.write_text(str(replacement_pid), encoding="utf-8")

        monkeypatch.setattr(cli_module.os, "kill", replace_pid_file)

        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(replacement_pid)
        assert f"stopped monitor (pid={original_pid})" in capsys.readouterr().out


class TestMonitorLoop:
    def test_non_positive_interval_is_rejected_before_pid_file_write(self):
        with pytest.raises(SystemExit, match="positive"):
            monitor_loop(0)

        assert not cli_module.PID_FILE.exists()
