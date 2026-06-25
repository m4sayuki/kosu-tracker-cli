"""Tests for read_pid, is_pid_running, require_not_running."""
from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

import kosu_tracker.cli as cli_module
from kosu_tracker.cli import is_monitor_process, is_pid_running, read_pid, require_not_running, stop_monitor


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
        assert is_pid_running(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_running(-1) is False


class TestIsMonitorProcess:
    def test_python_module_run_monitor_is_recognized(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)

        def fake_run(*args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="/usr/bin/python3 -m kosu_tracker.cli run-monitor --interval 60\n",
            )

        monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
        assert is_monitor_process(12345) is True

    def test_console_script_run_monitor_is_recognized(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)

        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="/venv/bin/kosu run-monitor --interval 60\n")

        monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
        assert is_monitor_process(12345) is True

    def test_unrelated_process_is_not_recognized(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)

        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="sleep 60\n")

        monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
        assert is_monitor_process(12345) is False

    def test_missing_process_is_not_recognized(self, monkeypatch):
        monkeypatch.setattr(cli_module, "is_pid_running", lambda pid: True)

        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="")

        monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
        assert is_monitor_process(12345) is False


class TestRequireNotRunning:
    def test_no_pid_file_passes_without_error(self):
        require_not_running()  # should not raise

    def test_stale_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text("999999999", encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_unrelated_active_pid_file_is_deleted(self):
        cli_module.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        require_not_running()
        assert not cli_module.PID_FILE.exists()

    def test_active_monitor_pid_raises_system_exit(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        with pytest.raises(SystemExit, match=r"monitor is already running"):
            require_not_running()

    def test_system_exit_message_contains_pid(self, monkeypatch):
        pid = os.getpid()
        cli_module.PID_FILE.write_text(str(pid), encoding="utf-8")
        monkeypatch.setattr(cli_module, "is_monitor_process", lambda candidate: candidate == pid)
        with pytest.raises(SystemExit) as exc_info:
            require_not_running()
        assert str(pid) in str(exc_info.value)


class TestStopMonitor:
    def test_unrelated_live_pid_is_not_signalled(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            cli_module.PID_FILE.write_text(str(sleeper.pid), encoding="utf-8")

            with pytest.raises(SystemExit, match=r"monitor is not running"):
                stop_monitor()

            assert sleeper.poll() is None
            assert not cli_module.PID_FILE.exists()
        finally:
            if sleeper.poll() is None:
                sleeper.terminate()
                sleeper.wait(timeout=5)
