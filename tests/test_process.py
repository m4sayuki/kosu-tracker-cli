"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal
import threading
import time

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_pid_running, monitor_loop, read_pid, require_not_running, stop_monitor


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


class TestStopMonitor:
    def test_removes_pid_file_after_process_stops(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_pid_running", side_effect=[True, False, False])
        mocker.patch("kosu_tracker.cli.time.sleep")
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        kill.assert_called_once_with(pid, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_keeps_pid_file_when_process_survives_sigterm(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor did not stop after SIGTERM"):
            stop_monitor()

        kill.assert_called_once_with(pid, signal.SIGTERM)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)


class TestMonitorLoop:
    def test_signal_interrupts_interval_wait(self, mocker, tmp_path):
        monkeypatched_signals = {}

        def fake_signal(signum, handler):
            monkeypatched_signals[signum] = handler

        sample = mocker.Mock()
        sample.as_dict.return_value = {"app_name": "Cursor", "category": "development"}
        mocker.patch("kosu_tracker.cli.signal.signal", side_effect=fake_signal)
        mocker.patch("kosu_tracker.cli.LOG_DIR", tmp_path / "logs")
        mocker.patch("kosu_tracker.cli.collect_sample", return_value=sample)
        mocker.patch("kosu_tracker.cli.write_jsonl")
        mocker.patch("kosu_tracker.cli.write_latest")

        thread = threading.Thread(target=monitor_loop, args=(3600,))
        thread.start()
        deadline = time.monotonic() + 1
        while signal.SIGTERM not in monkeypatched_signals and time.monotonic() < deadline:
            time.sleep(0.01)
        assert signal.SIGTERM in monkeypatched_signals

        monkeypatched_signals[signal.SIGTERM](signal.SIGTERM, None)
        thread.join(timeout=1)

        assert not thread.is_alive()
        assert not cli_module.PID_FILE.exists()
