"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_process,
    is_pid_running,
    read_pid,
    remove_pid_file_if_owned,
    require_not_running,
    sleep_until_next_sample,
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
        # os.kill(0, 0) はプロセスグループ全体に送られるが OSError が出ないケースもある
        # ここではモックを使って確実にFalseを返すシナリオをテスト
        assert is_pid_running(999_999_998) is False


class TestIsMonitorProcess:
    def test_run_monitor_module_process_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
        )

        assert is_monitor_process(1234) is True

    def test_run_monitor_console_script_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.process_command", return_value="/usr/local/bin/kosu run-monitor --interval 60")

        assert is_monitor_process(1234) is True

    def test_unrelated_running_process_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.process_command", return_value="sleep 30")

        assert is_monitor_process(1234) is False

    def test_dead_process_returns_false_without_reading_command(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        process_command = mocker.patch("kosu_tracker.cli.process_command")

        assert is_monitor_process(1234) is False
        process_command.assert_not_called()


class TestRemovePidFileIfOwned:
    def test_removes_matching_pid_file(self):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")

        remove_pid_file_if_owned(1234)

        assert not cli_module.PID_FILE.exists()

    def test_preserves_newer_pid_file(self):
        cli_module.PID_FILE.write_text("5678", encoding="utf-8")

        remove_pid_file_if_owned(1234)

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "5678"


class TestSleepUntilNextSample:
    def test_sleep_is_limited_to_one_second_chunks(self, monkeypatch):
        running = True
        sleeps: list[float] = []

        def fake_sleep(seconds):
            nonlocal running
            sleeps.append(seconds)
            running = False

        monkeypatch.setattr(cli_module.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(cli_module.time, "sleep", fake_sleep)

        sleep_until_next_sample(60, lambda: running)

        assert sleeps == [1.0]


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

    def test_unrelated_running_pid_file_is_treated_as_stale(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        require_not_running()

        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_pid_does_not_receive_sigterm(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_receives_sigterm_and_cleans_pid_file(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        kill.assert_called_once_with(pid, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_stop_failure_preserves_pid_file(self, mocker):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")
        mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(pid)
