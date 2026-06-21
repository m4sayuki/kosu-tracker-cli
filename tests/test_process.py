"""Tests for monitor process and PID file helpers."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    interruptible_sleep,
    is_monitor_process,
    is_pid_running,
    monitor_loop,
    print_status,
    read_pid,
    require_not_running,
    start_monitor,
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


class TestIsPidRunning:
    def test_current_process_is_running(self):
        assert is_pid_running(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        assert is_pid_running(999_999_999) is False

    def test_zero_pid_returns_false(self):
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False

    def test_zombie_pid_returns_false(self, monkeypatch):
        monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(cli_module, "process_status", lambda pid: "Z+")

        assert is_pid_running(12345) is False


class TestIsMonitorProcess:
    def test_python_module_run_monitor_is_recognized(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_module, "process_args", lambda pid: "python -m kosu_tracker.cli run-monitor --interval 60")

        assert is_monitor_process(12345) is True

    def test_console_script_run_monitor_is_recognized(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_module, "process_args", lambda pid: "/usr/local/bin/kosu run-monitor --interval 60")

        assert is_monitor_process(12345) is True

    def test_unrelated_live_process_is_not_monitor(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)
        monkeypatch.setattr(cli_module, "process_args", lambda pid: "python -m pytest")

        assert is_monitor_process(12345) is False


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

    def test_active_unrelated_pid_is_deleted_as_stale(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        require_not_running()

        assert not cli_module.PID_FILE.exists()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)

        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_replaced_pid_file_is_not_deleted(self, monkeypatch):
        original_pid = 123
        replacement_pid = 456
        cli_module.PID_FILE.write_text(str(original_pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda pid: False)
        read_results = iter([original_pid, replacement_pid])
        monkeypatch.setattr(cli_module, "read_pid", lambda: next(read_results))

        require_not_running()

        assert cli_module.PID_FILE.exists()
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(original_pid)


class TestUnlinkPidFileIfMatches:
    def test_unlinks_matching_pid(self):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")

        unlink_pid_file_if_matches(123)

        assert not cli_module.PID_FILE.exists()

    def test_keeps_different_pid(self):
        cli_module.PID_FILE.write_text("456", encoding="utf-8")

        unlink_pid_file_if_matches(123)

        assert cli_module.PID_FILE.exists()

    def test_unlinks_invalid_pid_when_expected_none(self):
        cli_module.PID_FILE.write_text("invalid", encoding="utf-8")

        unlink_pid_file_if_matches(None)

        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        kill_calls: list[tuple[int, int]] = []
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: False)
        monkeypatch.setattr(cli_module.os, "kill", lambda candidate, sig: kill_calls.append((candidate, sig)))

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        assert kill_calls == []
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_is_signalled_and_unlinked(self, monkeypatch):
        pid = 12345
        running = {pid: True}
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda candidate: running.get(candidate, False))

        def fake_kill(candidate: int, sig: int) -> None:
            assert candidate == pid
            assert sig == signal.SIGTERM
            running[candidate] = False

        monkeypatch.setattr(cli_module.os, "kill", fake_kill)

        stop_monitor()

        assert not cli_module.PID_FILE.exists()

    def test_stop_timeout_preserves_pid_file(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        monkeypatch.setattr(cli_module, "is_pid_running", lambda candidate: True)
        monkeypatch.setattr(cli_module.os, "kill", lambda candidate, sig: None)
        monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: None)

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)


class TestPrintStatus:
    def test_unrelated_live_pid_reports_not_running(self, monkeypatch, capsys):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: False)

        print_status()

        assert "running: no" in capsys.readouterr().out


class TestIntervals:
    def test_start_rejects_zero_interval(self):
        with pytest.raises(SystemExit, match=r"interval must be a positive"):
            start_monitor(0)

    def test_monitor_loop_rejects_negative_interval_before_writing_pid_file(self):
        with pytest.raises(SystemExit, match=r"interval must be a positive"):
            monitor_loop(-1)

        assert not cli_module.PID_FILE.exists()

    def test_interruptible_sleep_uses_short_chunks(self, monkeypatch):
        current_time = [0.0]
        sleep_calls: list[float] = []
        monkeypatch.setattr(cli_module.time, "monotonic", lambda: current_time[0])

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            current_time[0] += seconds

        monkeypatch.setattr(cli_module.time, "sleep", fake_sleep)

        interruptible_sleep(2, lambda: True)

        assert sleep_calls == [1.0, 1.0]

    def test_interruptible_sleep_stops_after_continue_flag_clears(self, monkeypatch):
        current_time = [0.0]
        sleep_calls: list[float] = []
        monkeypatch.setattr(cli_module.time, "monotonic", lambda: current_time[0])

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            current_time[0] += seconds

        monkeypatch.setattr(cli_module.time, "sleep", fake_sleep)

        interruptible_sleep(60, lambda: not sleep_calls)

        assert sleep_calls == [1.0]
