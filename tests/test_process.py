"""Tests for process and monitor PID handling."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_monitor_pid, is_pid_running, process_command, read_pid, require_not_running, stop_monitor


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
        # os.kill(0, 0) targets the process group, not a single monitor process.
        assert is_pid_running(0) is False


class TestProcessCommand:
    def test_returns_command_output(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=0, stdout="python -m kosu_tracker.cli run-monitor\n")

        assert process_command(123) == "python -m kosu_tracker.cli run-monitor"

    def test_nonzero_returncode_returns_none(self, mocker):
        mock_run = mocker.patch("kosu_tracker.cli.subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=1, stdout="")

        assert process_command(123) is None

    def test_os_error_returns_none(self, mocker):
        mocker.patch("kosu_tracker.cli.subprocess.run", side_effect=OSError("ps missing"))

        assert process_command(123) is None


class TestIsMonitorPid:
    def test_monitor_command_returns_true(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch(
            "kosu_tracker.cli.process_command",
            return_value="python -m kosu_tracker.cli run-monitor --interval 60",
        )

        assert is_monitor_pid(123) is True

    def test_unrelated_running_process_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.process_command", return_value="sleep 1000")

        assert is_monitor_pid(123) is False

    def test_missing_command_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.process_command", return_value=None)

        assert is_monitor_pid(123) is False

    def test_non_running_pid_returns_false(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)

        assert is_monitor_pid(123) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid", return_value=True)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid", return_value=True)
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_active_unrelated_pid_is_treated_as_stale(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid", return_value=False)
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        require_not_running()

        assert not cli_module.PID_FILE.exists()


class TestStopMonitor:
    def test_unrelated_running_pid_is_not_killed(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_pid", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        with pytest.raises(SystemExit, match=r"monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()
