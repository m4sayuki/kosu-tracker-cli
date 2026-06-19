"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_monitor_process,
    is_pid_running,
    monitor_loop,
    read_pid,
    require_not_running,
    stop_monitor,
    validate_positive_interval,
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


class TestIsMonitorProcess:
    def test_monitor_command_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="/usr/bin/python -m kosu_tracker.cli run-monitor --interval 60\n",
            ),
        )

        assert is_monitor_process(12345) is True

    def test_unrelated_live_process_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="bash\n"),
        )

        assert is_monitor_process(12345) is False

    def test_dead_pid_returns_false_without_ps(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        ps = mocker.patch("kosu_tracker.cli.subprocess.run")

        assert is_monitor_process(12345) is False
        ps.assert_not_called()


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda value: value == pid)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda value: value == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_unrelated_live_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        require_not_running()

        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()


class TestMonitorLoop:
    def test_does_not_delete_replaced_pid_file_on_exit(self, monkeypatch):
        replacement_pid = os.getpid() + 1000

        def replace_pid_and_fail():
            cli_module.PID_FILE.write_text(str(replacement_pid), encoding="utf-8")
            raise RuntimeError("stop loop")

        monkeypatch.setattr(cli_module, "collect_sample", replace_pid_and_fail)

        with pytest.raises(RuntimeError, match=r"stop loop"):
            monitor_loop(1)

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == str(replacement_pid)


class TestValidatePositiveInterval:
    def test_positive_interval_passes(self):
        validate_positive_interval(1)

    @pytest.mark.parametrize("interval", [0, -1])
    def test_non_positive_interval_exits(self, interval):
        with pytest.raises(SystemExit, match=r"interval must be positive"):
            validate_positive_interval(interval)
