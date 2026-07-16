"""Tests for monitor process PID handling."""
from __future__ import annotations

import os
import signal

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import (
    is_kosu_monitor_command,
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


class TestIsKosuMonitorCommand:
    @pytest.mark.parametrize(
        "command",
        [
            "/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60",
            "/usr/local/bin/kosu run-monitor --interval 60",
            "/workspace/.venv/bin/python /workspace/.venv/bin/kosu run-monitor",
        ],
    )
    def test_monitor_commands_match(self, command):
        assert is_kosu_monitor_command(command) is True

    @pytest.mark.parametrize(
        "command",
        [
            "/usr/bin/python3 -m http.server 8000",
            "/usr/bin/python3 script.py kosu run-monitor",
            "/usr/bin/python3 script.py -m kosu_tracker.cli run-monitor",
            "/bin/sh -c 'kosu run-monitor'",
            "python -m 'unterminated",
        ],
    )
    def test_unrelated_commands_do_not_match(self, command):
        assert is_kosu_monitor_command(command) is False


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

    def test_active_monitor_pid_raises_system_exit(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, mocker):
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        pid = 12345
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self, mocker):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill", wraps=os.kill)

        with pytest.raises(SystemExit, match="monitor is not running"):
            cli_module.stop_monitor()

        assert all(call.args[1] != signal.SIGTERM for call in kill_mock.call_args_list)
        assert not cli_module.PID_FILE.exists()

    def test_negative_pid_is_not_signalled(self, mocker):
        cli_module.PID_FILE.write_text("-1", encoding="utf-8")
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        with pytest.raises(SystemExit, match="monitor is not running"):
            cli_module.stop_monitor()

        kill_mock.assert_not_called()
        assert not cli_module.PID_FILE.exists()

    def test_monitor_pid_is_signalled_and_unlinked(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch(
            "kosu_tracker.cli.is_monitor_process",
            side_effect=[True, False],
        )
        kill_mock = mocker.patch("kosu_tracker.cli.os.kill")

        cli_module.stop_monitor()

        kill_mock.assert_called_once_with(12345, signal.SIGTERM)
        assert not cli_module.PID_FILE.exists()

    def test_replacement_pid_file_is_preserved(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch(
            "kosu_tracker.cli.is_monitor_process",
            side_effect=[True, False],
        )

        def replace_pid_file(pid, signum):
            cli_module.PID_FILE.write_text("67890", encoding="utf-8")

        mocker.patch("kosu_tracker.cli.os.kill", side_effect=replace_pid_file)

        cli_module.stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "67890"

    def test_pid_file_is_preserved_if_monitor_does_not_stop(self, mocker):
        cli_module.PID_FILE.write_text("12345", encoding="utf-8")
        mocker.patch("kosu_tracker.cli.is_monitor_process", return_value=True)
        mocker.patch("kosu_tracker.cli.os.kill")
        mocker.patch("kosu_tracker.cli.time.sleep")

        with pytest.raises(SystemExit, match="failed to stop monitor"):
            cli_module.stop_monitor()

        assert cli_module.PID_FILE.read_text(encoding="utf-8") == "12345"


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
