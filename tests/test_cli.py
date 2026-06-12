"""Tests for build_parser and main() command dispatch."""
from __future__ import annotations

import pytest

from kosu_tracker.cli import build_parser, main


class TestBuildParser:
    def _parse(self, args: list[str]):
        return build_parser().parse_args(args)

    # ── start ──────────────────────────────────────────────────────────
    def test_start_default_interval(self):
        args = self._parse(["start"])
        assert args.command == "start"
        assert args.interval == 60

    def test_start_custom_interval(self):
        args = self._parse(["start", "--interval", "30"])
        assert args.interval == 30

    def test_start_zero_interval_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["start", "--interval", "0"])

    def test_start_negative_interval_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["start", "--interval", "-1"])

    # ── stop ───────────────────────────────────────────────────────────
    def test_stop_command(self):
        args = self._parse(["stop"])
        assert args.command == "stop"

    # ── status ─────────────────────────────────────────────────────────
    def test_status_command(self):
        args = self._parse(["status"])
        assert args.command == "status"

    # ── sample ─────────────────────────────────────────────────────────
    def test_sample_default(self):
        args = self._parse(["sample"])
        assert args.command == "sample"
        assert args.json is False

    def test_sample_with_json_flag(self):
        args = self._parse(["sample", "--json"])
        assert args.json is True

    # ── report ─────────────────────────────────────────────────────────
    def test_report_defaults(self):
        args = self._parse(["report"])
        assert args.command == "report"
        assert args.target_date == "today"
        assert args.with_ai is False
        assert args.model == "gpt-5-mini"
        assert args.interval_minutes == 1

    def test_report_yesterday(self):
        args = self._parse(["report", "yesterday"])
        assert args.target_date == "yesterday"

    def test_report_iso_date(self):
        args = self._parse(["report", "2025-01-15"])
        assert args.target_date == "2025-01-15"

    def test_report_with_ai_flag(self):
        args = self._parse(["report", "--with-ai"])
        assert args.with_ai is True

    def test_report_custom_model(self):
        args = self._parse(["report", "--model", "gpt-4o"])
        assert args.model == "gpt-4o"

    def test_report_custom_interval_minutes(self):
        args = self._parse(["report", "--interval-minutes", "5"])
        assert args.interval_minutes == 5

    # ── run-monitor ────────────────────────────────────────────────────
    def test_run_monitor_default_interval(self):
        args = self._parse(["run-monitor"])
        assert args.command == "run-monitor"
        assert args.interval == 60

    def test_run_monitor_custom_interval(self):
        args = self._parse(["run-monitor", "--interval", "10"])
        assert args.interval == 10

    def test_run_monitor_zero_interval_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["run-monitor", "--interval", "0"])

    def test_run_monitor_negative_interval_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["run-monitor", "--interval", "-1"])

    # ── 引数なし → エラー ──────────────────────────────────────────────
    def test_no_subcommand_exits(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


class TestMainDispatch:
    """main() が各コマンドを正しい関数へ委譲することを確認する。"""

    def test_start_dispatches_to_start_monitor(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.start_monitor")
        main(["start", "--interval", "30"])
        mock.assert_called_once_with(interval_seconds=30)

    def test_stop_dispatches_to_stop_monitor(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.stop_monitor")
        main(["stop"])
        mock.assert_called_once()

    def test_status_dispatches_to_print_status(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.print_status")
        main(["status"])
        mock.assert_called_once()

    def test_sample_dispatches_to_collect_sample(self, mocker):
        fake_sample = mocker.MagicMock()
        fake_sample.as_dict.return_value = {"app_name": "Cursor"}
        mocker.patch("kosu_tracker.cli.collect_sample", return_value=fake_sample)
        mock_print = mocker.patch("builtins.print")
        main(["sample"])
        fake_sample.as_dict.assert_called_once()

    def test_run_monitor_dispatches_to_monitor_loop(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.monitor_loop")
        main(["run-monitor", "--interval", "10"])
        mock.assert_called_once_with(interval_seconds=10)

    def test_report_dispatches_to_report_day(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.report_day")
        main(["report", "2025-01-15"])
        mock.assert_called_once()
        call_kwargs = mock.call_args.kwargs
        from datetime import date
        assert call_kwargs["target_date"] == date(2025, 1, 15)
        assert call_kwargs["with_ai"] is False

    def test_report_with_ai_flag_passed(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.report_day")
        main(["report", "--with-ai"])
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["with_ai"] is True

    def test_report_interval_minutes_passed(self, mocker):
        mock = mocker.patch("kosu_tracker.cli.report_day")
        main(["report", "--interval-minutes", "5"])
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["interval_minutes"] == 5
