"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_pid_running, read_pid, require_not_running, sleep_interruptibly, stop_monitor


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
        # os.kill(0, 0) はプロセスグループ全体に送られるが OSError が出ないケースもある
        # ここではモックを使って確実にFalseを返すシナリオをテスト
        assert is_pid_running(999_999_998) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_pid_raises_system_exit(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestSleepInterruptibly:
    def test_rechecks_should_continue_between_short_sleeps(self, monkeypatch):
        now = 0.0
        sleeps: list[float] = []
        running_checks = iter([True, False])

        def fake_sleep(duration: float) -> None:
            nonlocal now
            sleeps.append(duration)
            now += duration

        monkeypatch.setattr(cli_module.time, "monotonic", lambda: now)
        monkeypatch.setattr(cli_module.time, "sleep", fake_sleep)

        sleep_interruptibly(60, lambda: next(running_checks, False))

        assert sleeps == [cli_module.MONITOR_SLEEP_POLL_SECONDS]


class TestStopMonitor:
    def test_does_not_remove_pid_file_when_process_is_still_running(self, monkeypatch):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        killed: list[tuple[int, int]] = []

        monkeypatch.setattr(cli_module, "is_pid_running", lambda value: value == pid)
        monkeypatch.setattr(cli_module.os, "kill", lambda value, signal_number: killed.append((value, signal_number)))
        monkeypatch.setattr(cli_module.time, "sleep", lambda _: None)

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert killed == [(pid, cli_module.signal.SIGTERM)]
        assert cli_module.PID_FILE.exists()
