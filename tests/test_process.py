"""Tests for monitor process lifecycle helpers."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    claim_pid_file,
    is_monitor_command,
    is_monitor_process,
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

    @pytest.mark.parametrize("pid", ["0", "-1"])
    def test_non_positive_pid_returns_none(self, pid):
        cli_module.PID_FILE.write_text(pid, encoding="utf-8")
        assert read_pid() is None


class TestIsPidRunning:
    def test_current_process_is_running(self):
        assert is_pid_running(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        # PID 999999999 は通常存在しない
        assert is_pid_running(999_999_999) is False

    def test_non_positive_pid_returns_false_without_signalling(self, mocker):
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")
        assert is_pid_running(0) is False
        assert is_pid_running(-1) is False
        mock_kill.assert_not_called()


class TestMonitorProcessDetection:
    @pytest.mark.parametrize(
        "command",
        [
            "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
            "/usr/local/bin/kosu run-monitor --interval 60",
            '/usr/bin/python3 "/tmp/venv/bin/kosu" run-monitor',
        ],
    )
    def test_monitor_commands_are_recognized(self, command):
        assert is_monitor_command(command) is True

    @pytest.mark.parametrize(
        "command",
        [
            "/bin/sleep 60",
            "/usr/local/bin/kosu status",
            "/usr/bin/python3 -m http.server run-monitor",
            "/bin/echo -m kosu_tracker.cli run-monitor",
        ],
    )
    def test_unrelated_commands_are_rejected(self, command):
        assert is_monitor_command(command) is False

    def test_live_unrelated_pid_is_not_monitor_process(self, mocker):
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.process_command", return_value="/bin/sleep 60")
        assert is_monitor_process(1234) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_pid_raises_system_exit(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        pid = 1234
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)

    def test_live_unrelated_pid_file_is_deleted(self, mocker):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        require_not_running()
        assert not cli_module.PID_FILE.exists()


class TestClaimPidFile:
    def test_claim_writes_pid_atomically(self):
        claim_pid_file(1234)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "1234"

    def test_existing_monitor_is_not_replaced(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        with pytest.raises(SystemExit, match="monitor is already running"):
            claim_pid_file(5678)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "1234"

    def test_stale_pid_file_is_replaced(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        claim_pid_file(5678)
        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "5678"


class TestIntervalValidation:
    @pytest.mark.parametrize("interval", [0, -1])
    def test_monitor_loop_rejects_non_positive_interval(self, interval):
        with pytest.raises(SystemExit, match="interval must be greater than 0"):
            monitor_loop(interval)

    def test_start_rejects_non_positive_interval_before_spawn(self, mocker):
        mock_popen = mocker.patch("kosu_tracker.cli.subprocess.Popen")
        with pytest.raises(SystemExit, match="interval must be greater than 0"):
            start_monitor(0)
        mock_popen.assert_not_called()


class TestStopMonitor:
    def test_live_unrelated_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_negative_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            stop_monitor()

        mock_kill.assert_not_called()

    def test_monitor_pid_gets_sigterm(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)
        mock_kill = mocker.patch("kosu_tracker.cli.os.kill")

        stop_monitor()

        mock_kill.assert_called_once_with(1234, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_timeout_preserves_pid_file(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=True)
        mocker.patch("kosu_tracker.cli.time.sleep")
        mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "1234"

    def test_replacement_pid_file_is_not_deleted(self, mocker):
        cli_module.PID_FILE.write_text("1234", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.is_pid_running", return_value=False)

        def replace_pid_file(pid, sig):
            cli_module.PID_FILE.write_text("5678", encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)
        stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "5678"
