"""Tests for monitor process PID handling."""
from __future__ import annotations

import os

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_kosu_monitor_command, is_pid_running, read_pid, require_not_running


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

    def test_zero_pid_returns_false_without_group_probe(self, mocker):
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(0) is False
        kill_mock.assert_not_called()

    def test_negative_pid_returns_false_without_group_probe(self, mocker):
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(-1) is False
        kill_mock.assert_not_called()


class TestIsKosuMonitorCommand:
    def test_python_module_run_monitor_matches(self):
        command = "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60"
        assert is_kosu_monitor_command(command) is True

    def test_console_script_run_monitor_matches(self):
        command = "/workspace/.venv/bin/python /workspace/.venv/bin/kosu run-monitor --interval 60"
        assert is_kosu_monitor_command(command) is True

    def test_unrelated_python_process_does_not_match(self):
        command = "/usr/bin/python3 -m http.server 8000"
        assert is_kosu_monitor_command(command) is False

    def test_kosu_run_monitor_as_arguments_does_not_match(self):
        command = "/usr/bin/python script.py kosu run-monitor"
        assert is_kosu_monitor_command(command) is False

    def test_shell_command_with_kosu_arguments_does_not_match(self):
        command = "/bin/sh -c 'kosu run-monitor'"
        assert is_kosu_monitor_command(command) is False

    def test_unparseable_command_does_not_match(self):
        assert is_kosu_monitor_command("python -m 'unterminated") is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_live_pid_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
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


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
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

    def test_replaced_pid_file_is_preserved(self, mocker):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", side_effect=[True, False, False])

        def replace_pid_file(pid, signum):
            cli_module.PID_FILE.write_text("456", encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)

        cli_module.stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "456"

    def test_pid_file_preserved_when_monitor_does_not_stop(self, mocker):
        cli_module.PID_FILE.write_text("123", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill")
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            cli_module.stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "123"


class TestIntervalValidation:
    def test_start_rejects_zero_interval_before_spawning(self, mocker):
        popen_mock = mocker.patch("kosu_tracker.cli.subprocess.Popen")

        with pytest.raises(SystemExit, match="monitor interval must be greater than 0"):
            cli_module.start_monitor(0)

        popen_mock.assert_not_called()

    def test_monitor_loop_rejects_zero_interval_before_writing_pid(self):
        with pytest.raises(SystemExit, match="monitor interval must be greater than 0"):
            cli_module.monitor_loop(0)

        assert not cli_module.PID_FILE.exists()
