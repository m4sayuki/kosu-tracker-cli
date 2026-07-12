from __future__ import annotations

import argparse
import json
import os
import signal
import shlex
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


APP_DIR = Path(os.environ.get("KOSU_TRACKER_HOME", Path.home() / ".local" / "share" / "kosu-tracker")).expanduser()
LOG_DIR = APP_DIR / "logs"
STATE_DIR = APP_DIR / "state"
PID_FILE = STATE_DIR / "monitor.pid"
LATEST_FILE = STATE_DIR / "latest.json"
KNOWN_BROWSERS = {
    "Google Chrome",
    "Safari",
    "Arc",
    "Brave Browser",
    "Microsoft Edge",
    "Firefox",
}


@dataclass
class ActivitySample:
    timestamp: str
    app_name: str
    window_title: str | None
    category: str
    browser_title: str | None
    browser_url: str | None
    browser_domain: str | None
    collection_error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "app_name": self.app_name,
            "window_title": self.window_title,
            "category": self.category,
            "browser_title": self.browser_title,
            "browser_url": self.browser_url,
            "browser_domain": self.browser_domain,
            "collection_error": self.collection_error,
        }


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def run_osascript(script: str) -> str:
    completed = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or "unknown osascript error"
        raise RuntimeError(stderr)
    return completed.stdout.strip()


def collect_frontmost_app() -> tuple[str, str | None]:
    script = textwrap.dedent(
        """
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set windowTitle to ""
            try
                set windowTitle to name of front window of frontApp
            end try
            return appName & linefeed & windowTitle
        end tell
        """
    ).strip()
    output = run_osascript(script)
    lines = output.splitlines()
    app_name = lines[0] if lines else "Unknown"
    window_title = lines[1] if len(lines) > 1 and lines[1] else None
    return app_name, window_title


def collect_browser_context(app_name: str) -> tuple[str | None, str | None]:
    if app_name not in KNOWN_BROWSERS:
        return None, None

    if app_name == "Safari":
        script = textwrap.dedent(
            """
            tell application "Safari"
                if not (exists front window) then
                    return ""
                end if
                set currentTab to current tab of front window
                return (name of currentTab) & linefeed & (URL of currentTab)
            end tell
            """
        ).strip()
    elif app_name == "Firefox":
        # Firefox does not expose tab URLs to AppleScript reliably.
        return None, None
    else:
        script = textwrap.dedent(
            f'''
            tell application "{app_name}"
                if (count of windows) = 0 then
                    return ""
                end if
                set currentTab to active tab of front window
                return (title of currentTab) & linefeed & (URL of currentTab)
            end tell
            '''
        ).strip()

    try:
        output = run_osascript(script)
    except RuntimeError:
        return None, None

    lines = output.splitlines()
    title = lines[0] if lines else None
    url = lines[1] if len(lines) > 1 and lines[1] else None
    return title, url


def classify_activity(app_name: str, window_title: str | None, browser_url: str | None) -> str:
    lower_app = app_name.lower()
    title = (window_title or "").lower()
    url = (browser_url or "").lower()

    if app_name in KNOWN_BROWSERS:
        if any(domain in url for domain in ("slack.com", "discord.com", "chat.openai.com", "claude.ai")):
            return "communication"
        if any(domain in url for domain in ("github.com", "gitlab.com", "docs.", "developer.", "stackoverflow.com")):
            return "research"
        return "browser"

    if any(keyword in lower_app for keyword in ("cursor", "code", "vim", "emacs", "xcode", "android studio", "pycharm", "intellij")):
        return "development"
    if any(keyword in lower_app for keyword in ("terminal", "iterm", "warp")):
        return "terminal"
    if any(keyword in lower_app for keyword in ("slack", "discord", "teams", "zoom")):
        return "communication"
    if any(keyword in lower_app for keyword in ("figma", "sketch", "photoshop")):
        return "design"
    if "meeting" in title:
        return "meeting"
    return "other"


def extract_domain(url: str | None) -> str | None:
    if not url or "://" not in url:
        return None
    return url.split("://", 1)[1].split("/", 1)[0]


def today_log_path(target_date: date | None = None) -> Path:
    current = target_date or datetime.now().date()
    return LOG_DIR / f"{current.isoformat()}.jsonl"


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_latest(payload: dict[str, Any]) -> None:
    LATEST_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_sample() -> ActivitySample:
    collection_error = None
    try:
        app_name, window_title = collect_frontmost_app()
    except RuntimeError as exc:
        app_name, window_title = "Unknown", None
        collection_error = str(exc)

    browser_title, browser_url = collect_browser_context(app_name)
    return ActivitySample(
        timestamp=datetime.now().astimezone().isoformat(),
        app_name=app_name,
        window_title=window_title,
        category=classify_activity(app_name, window_title, browser_url),
        browser_title=browser_title,
        browser_url=browser_url,
        browser_domain=extract_domain(browser_url),
        collection_error=collection_error,
    )


def monitor_loop(interval_seconds: int) -> None:
    validate_interval(interval_seconds)
    ensure_dirs()
    pid = os.getpid()
    PID_FILE.write_text(str(pid), encoding="utf-8")
    keep_running = True

    def handle_term(signum: int, frame: Any) -> None:
        nonlocal keep_running
        keep_running = False

    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGINT, handle_term)
    try:
        while keep_running:
            sample = collect_sample().as_dict()
            write_jsonl(today_log_path(), sample)
            write_latest(sample)
            sleep_until = time.monotonic() + interval_seconds
            while keep_running:
                remaining = sleep_until - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.2))
    finally:
        if pid_file_matches(pid):
            PID_FILE.unlink()


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_command(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        completed = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    command = completed.stdout.strip()
    return command or None


def is_monitor_command(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if "run-monitor" not in tokens:
        return False

    for index, token in enumerate(tokens):
        if token == "-m" and index + 1 < len(tokens) and tokens[index + 1] == "kosu_tracker.cli":
            return "run-monitor" in tokens[index + 2 :]
        if Path(token).name == "kosu" and index + 1 < len(tokens) and tokens[index + 1] == "run-monitor":
            return True
    return False


def is_monitor_process(pid: int) -> bool:
    if not is_pid_running(pid):
        return False
    command = process_command(pid)
    return bool(command and is_monitor_command(command))


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def pid_file_matches(pid: int) -> bool:
    return read_pid() == pid


def validate_interval(interval_seconds: int) -> None:
    if interval_seconds <= 0:
        raise SystemExit("interval must be greater than 0 seconds")


def require_not_running() -> None:
    pid = read_pid()
    if pid and is_monitor_process(pid):
        raise SystemExit(f"monitor is already running (pid={pid})")
    if PID_FILE.exists():
        PID_FILE.unlink()


def start_monitor(interval_seconds: int) -> None:
    validate_interval(interval_seconds)
    ensure_dirs()
    require_not_running()
    env = os.environ.copy()
    subprocess.Popen(
        [sys.executable, "-m", "kosu_tracker.cli", "run-monitor", "--interval", str(interval_seconds)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    time.sleep(1)
    pid = read_pid()
    if not pid:
        raise SystemExit("failed to start monitor; check macOS Automation/Accessibility permissions")
    print(f"started monitor (pid={pid})")
    print(f"log directory: {LOG_DIR}")


def stop_monitor() -> None:
    pid = read_pid()
    if not pid or not is_monitor_process(pid):
        if PID_FILE.exists():
            PID_FILE.unlink()
        raise SystemExit("monitor is not running")
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not is_pid_running(pid):
            break
        time.sleep(0.2)
    if is_pid_running(pid):
        raise SystemExit(f"failed to stop monitor (pid={pid})")
    if pid_file_matches(pid):
        PID_FILE.unlink()
    print(f"stopped monitor (pid={pid})")


def print_status() -> None:
    pid = read_pid()
    running = bool(pid and is_monitor_process(pid))
    print(f"running: {'yes' if running else 'no'}")
    if running:
        print(f"pid: {pid}")
    print(f"log directory: {LOG_DIR}")
    if LATEST_FILE.exists():
        latest = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
        print("latest sample:")
        print(json.dumps(latest, ensure_ascii=False, indent=2))


def iter_logs_for_date(target_date: date) -> list[dict[str, Any]]:
    path = today_log_path(target_date)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def summarize_rows(rows: list[dict[str, Any]], interval_minutes: int) -> dict[str, Any]:
    by_app: dict[str, int] = defaultdict(int)
    by_category: dict[str, int] = defaultdict(int)
    by_browser_title: dict[str, int] = defaultdict(int)

    for row in rows:
        minutes = interval_minutes
        by_app[row["app_name"]] += minutes
        by_category[row["category"]] += minutes
        title = row.get("browser_title") or row.get("window_title") or row["app_name"]
        by_browser_title[title] += minutes

    def sort_items(data: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"name": name, "minutes": minutes, "hours": round(minutes / 60, 2)}
            for name, minutes in sorted(data.items(), key=lambda item: (-item[1], item[0]))
        ]

    return {
        "total_samples": len(rows),
        "estimated_total_minutes": len(rows) * interval_minutes,
        "by_app": sort_items(by_app),
        "by_category": sort_items(by_category),
        "top_titles": sort_items(dict(list(sorted(by_browser_title.items(), key=lambda item: (-item[1], item[0])))[:10])),
    }


def build_local_narrative(summary: dict[str, Any], target_date: date) -> str:
    top_categories = ", ".join(
        f"{item['name']} {item['hours']}h" for item in summary["by_category"][:3]
    ) or "no activity"
    top_apps = ", ".join(
        f"{item['name']} {item['hours']}h" for item in summary["by_app"][:5]
    ) or "no activity"
    return (
        f"{target_date.isoformat()} の推定稼働時間は {summary['estimated_total_minutes']} 分です。"
        f" 主な内訳は {top_categories}。"
        f" よく使ったアプリは {top_apps}。"
    )


def ai_summarize(summary: dict[str, Any], target_date: date, model: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    prompt = {
        "date": target_date.isoformat(),
        "summary": summary,
        "instruction": (
            "You are an assistant that summarizes a user's workday from app usage logs. "
            "Respond in Japanese. Estimate what the user spent time on, mention uncertainty, "
            "and provide a concise bullet list with category totals and likely work themes."
        ),
    }
    body = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(prompt, ensure_ascii=False),
                    }
                ],
            }
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise SystemExit(f"OpenAI API error: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"failed to reach OpenAI API: {exc}") from exc

    return payload.get("output_text", "").strip() or json.dumps(payload, ensure_ascii=False, indent=2)


def report_day(target_date: date, with_ai: bool, model: str, interval_minutes: int) -> None:
    rows = iter_logs_for_date(target_date)
    if not rows:
        raise SystemExit(f"no log file found for {target_date.isoformat()}")

    summary = summarize_rows(rows, interval_minutes=interval_minutes)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    print(build_local_narrative(summary, target_date))

    if with_ai:
        print()
        print("AI summary:")
        print(ai_summarize(summary, target_date, model=model))


def parse_date(value: str) -> date:
    if value == "today":
        return datetime.now().date()
    if value == "yesterday":
        return datetime.now().date() - timedelta(days=1)
    return date.fromisoformat(value)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kosu", description="Background worklog tracker for macOS")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start the background monitor")
    start.add_argument("--interval", type=positive_int, default=60, help="Sampling interval in seconds")

    sub.add_parser("stop", help="Stop the background monitor")
    sub.add_parser("status", help="Show monitor status")

    sample = sub.add_parser("sample", help="Collect and print a single sample")
    sample.add_argument("--json", action="store_true", help="Print JSON only")

    run_monitor = sub.add_parser("run-monitor", help=argparse.SUPPRESS)
    run_monitor.add_argument("--interval", type=positive_int, default=60)

    report = sub.add_parser("report", help="Summarize one day of logs")
    report.add_argument("target_date", nargs="?", default="today", help="today, yesterday, or YYYY-MM-DD")
    report.add_argument("--with-ai", action="store_true", help="Request an OpenAI summary")
    report.add_argument("--model", default="gpt-5-mini", help="OpenAI model used for --with-ai")
    report.add_argument("--interval-minutes", type=int, default=1, help="Minutes per sample")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "start":
        start_monitor(interval_seconds=args.interval)
        return
    if args.command == "stop":
        stop_monitor()
        return
    if args.command == "status":
        print_status()
        return
    if args.command == "sample":
        sample = collect_sample().as_dict()
        if args.json:
            print(json.dumps(sample, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(sample, ensure_ascii=False, indent=2))
        return
    if args.command == "run-monitor":
        monitor_loop(interval_seconds=args.interval)
        return
    if args.command == "report":
        report_day(
            target_date=parse_date(args.target_date),
            with_ai=args.with_ai,
            model=args.model,
            interval_minutes=args.interval_minutes,
        )
        return

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
