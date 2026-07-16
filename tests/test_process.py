"""Tests for monitor process PID handling."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_pid_running,
    read_pid,
    require_not_running,
)


@pytest.fixture(autouse=True)
def patch_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pid_file = state_dir / "monitor.pid"
    monkeypatch.setattr(cli_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(cli_module, "PID_FILE", pid_file)
    monkeypatch.setattr(cli_module, "LOCK_FILE", state_dir / "monitor.lock")
    monkeypatch.setattr(cli_module, "STOP_FILE", state_dir / "monitor.stop")
    return pid_file


class TestReadPid:
    def test_no_pid_file_returns_none(self):
        assert read_pid() is None

    def test_valid_pid_file_returns_int(self, tmp_path):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        assert read_pid() == 12345

    def test_pid_with_instance_token_returns_int(self):
        cli_module.PID_FILE.write_text("12345 abcdef\n", encoding="utf-8")
        assert read_pid() == 12345
        assert cli_module.read_monitor_state() == (12345, "abcdef")

    def test_pid_file_with_whitespace(self):
        cli_module.PID_FILE.write_text("  99  \n", encoding="utf-8")
        assert read_pid() == 99

    def test_invalid_content_returns_none(self):
        cli_module.PID_FILE.write_text("not-a-number", encoding="utf-8")
        assert read_pid() is None

    def test_empty_file_returns_none(self):
        cli_module.PID_FILE.write_text("", encoding="utf-8")
        assert read_pid() is None

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_non_positive_pid_returns_none(self, value):
        cli_module.PID_FILE.write_text(value, encoding="utf-8")
        assert read_pid() is None


class TestIsPidRunning:
    def test_current_process_is_running(self):
        assert is_pid_running(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        # PID 999999999 は通常存在しない
        assert is_pid_running(999_999_999) is False

    @pytest.mark.parametrize("pid", [0, -1])
    def test_non_positive_pid_returns_false_without_probe(self, pid, mocker):
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(pid) is False
        kill_mock.assert_not_called()


class TestMonitorLock:
    def test_second_owner_cannot_acquire_lock(self):
        first = cli_module.try_acquire_monitor_lock()
        assert first is not None
        try:
            assert cli_module.try_acquire_monitor_lock() is None
        finally:
            cli_module.release_monitor_lock(first)

    def test_lock_is_available_after_release(self):
        first = cli_module.try_acquire_monitor_lock()
        assert first is not None
        cli_module.release_monitor_lock(first)

        second = cli_module.try_acquire_monitor_lock()
        assert second is not None
        cli_module.release_monitor_lock(second)

    def test_shared_status_locks_do_not_block_each_other(self):
        first = cli_module.try_acquire_monitor_lock(shared=True)
        second = cli_module.try_acquire_monitor_lock(shared=True)
        assert first is not None
        assert second is not None
        try:
            assert cli_module.try_acquire_monitor_lock() is None
        finally:
            cli_module.release_monitor_lock(second)
            cli_module.release_monitor_lock(first)

    def test_monitor_retries_transient_shared_lock(self, mocker):
        lock = mocker.MagicMock()
        acquire = mocker.patch(
            "kosu_tracker.cli.try_acquire_monitor_lock",
            side_effect=[None, lock],
        )
        sleep = mocker.patch("kosu_tracker.cli.time.sleep")
        mocker.patch("kosu_tracker.cli.signal.signal")
        mocker.patch("kosu_tracker.cli.stop_requested", return_value=True)
        release = mocker.patch("kosu_tracker.cli.release_monitor_lock")

        cli_module.monitor_loop(1)

        assert acquire.call_count == 2
        sleep.assert_called_once_with(0.05)
        release.assert_called_once_with(lock)

    def test_waiting_monitor_exits_after_observing_active_instance(self, mocker):
        cli_module.PID_FILE.write_text("12345 token-a\n", encoding="utf-8")
        acquire = mocker.patch("kosu_tracker.cli.try_acquire_monitor_lock", return_value=None)
        sleep = mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match=r"already running \(pid=12345\)"):
            cli_module.monitor_loop(1)

        acquire.assert_called_once_with()
        sleep.assert_not_called()


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_live_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        lock = cli_module.try_acquire_monitor_lock()
        assert lock is not None
        try:
            with pytest.raises(SystemExit, match=r"monitor is already running"):
                require_not_running()
        finally:
            cli_module.release_monitor_lock(lock)

    def test_system_exit_message_contains_pid(self):
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        lock = cli_module.try_acquire_monitor_lock()
        assert lock is not None
        try:
            with pytest.raises(SystemExit) as exc_info:
                require_not_running()
        finally:
            cli_module.release_monitor_lock(lock)
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            cli_module.stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_negative_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            cli_module.stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_receives_stop_request_without_signal(self, mocker):
        cli_module.PID_FILE.write_text("12345 token-a\n", encoding="utf-8")
        mocker.patch(
            "kosu_tracker.cli.monitor_is_running",
            side_effect=[True, False],
        )
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        cli_module.stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()
        assert not cli_module.STOP_FILE.exists()

    def test_replacement_monitor_state_is_preserved(self, mocker):
        cli_module.PID_FILE.write_text("12345 token-a\n", encoding="utf-8")
        checks = iter([True, False])

        def monitor_running():
            running = next(checks)
            if not running:
                cli_module.PID_FILE.write_text("67890 token-b\n", encoding="utf-8")
            return running

        mocker.patch("kosu_tracker.cli.monitor_is_running", side_effect=monitor_running)

        cli_module.stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "67890 token-b\n"

    def test_pid_file_is_preserved_if_monitor_does_not_stop(self, mocker):
        cli_module.PID_FILE.write_text("12345 token-a\n", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.monitor_is_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            cli_module.stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345 token-a\n"
        assert cli_module.STOP_FILE.read_text(encoding="utf-8").strip() == "token-a"


class TestIntervalValidation:
    def test_start_rejects_zero_before_spawning(self, mocker):
        popen_mock = mocker.patch("kosu_tracker.cli.subprocess.Popen")

        with pytest.raises(SystemExit, match="monitor interval must be greater than 0"):
            cli_module.start_monitor(0)

        popen_mock.assert_not_called()

    def test_monitor_loop_rejects_negative_before_writing_pid(self):
        with pytest.raises(SystemExit, match="monitor interval must be greater than 0"):
            cli_module.monitor_loop(-1)

        assert not cli_module.PID_FILE.exists()
