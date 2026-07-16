from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import signal
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
LOCK_FILE = STATE_DIR / "monitor.lock"
CONTROL_LOCK_FILE = STATE_DIR / "control.lock"
GENERATION_FILE = STATE_DIR / "monitor.generation"
STOP_FILE = STATE_DIR / "monitor.stop"
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


def monitor_loop(
    interval_seconds: int,
    lock_fd: int | None = None,
    generation: str | None = None,
) -> None:
    if interval_seconds <= 0:
        raise SystemExit("monitor interval must be greater than 0")

    ensure_dirs()
    control_lock = acquire_control_lock()
    if lock_fd is None:
        lock = try_acquire_monitor_lock()
        if lock is None:
            release_monitor_lock(control_lock)
            raise SystemExit("monitor is already running")
        generation = advance_generation()
    else:
        try:
            lock = os.fdopen(lock_fd, "a+", encoding="utf-8")
        except OSError as exc:
            release_monitor_lock(control_lock)
            raise SystemExit("invalid inherited monitor lock") from exc

    pid = os.getpid()
    token = secrets.token_hex(16)
    keep_running = True

    def handle_term(signum: int, frame: Any) -> None:
        nonlocal keep_running
        keep_running = False

    try:
        if generation is None or read_generation() != generation:
            raise SystemExit("monitor start request is no longer current")
        clear_stop_request()
        write_monitor_state(pid, token)
        if control_lock is not None:
            release_monitor_lock(control_lock)
            control_lock = None

        signal.signal(signal.SIGTERM, handle_term)
        signal.signal(signal.SIGINT, handle_term)
        while keep_running and not stop_requested(token):
            sample = collect_sample().as_dict()
            write_jsonl(today_log_path(), sample)
            write_latest(sample)
            remaining = float(interval_seconds)
            while keep_running and remaining > 0 and not stop_requested(token):
                nap = min(0.2, remaining)
                time.sleep(nap)
                remaining -= nap
    finally:
        unlink_monitor_state_if_matches(pid, token)
        clear_stop_request(token)
        if control_lock is not None:
            release_monitor_lock(control_lock)
        release_monitor_lock(lock)


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def try_acquire_monitor_lock(shared: bool = False) -> Any | None:
    ensure_dirs()
    try:
        lock = LOCK_FILE.open("a+", encoding="utf-8")
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(lock.fileno(), operation | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        if "lock" in locals():
            lock.close()
        return None
    return lock


def release_monitor_lock(lock: Any) -> None:
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        lock.close()


def acquire_control_lock() -> Any:
    ensure_dirs()
    lock = CONTROL_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    except OSError:
        lock.close()
        raise
    return lock


def read_generation() -> str | None:
    try:
        generation = GENERATION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return generation or None


def advance_generation() -> str:
    generation = secrets.token_hex(16)
    temporary = GENERATION_FILE.with_name(f".{GENERATION_FILE.name}.{os.getpid()}")
    temporary.write_text(f"{generation}\n", encoding="utf-8")
    os.replace(temporary, GENERATION_FILE)
    return generation


def read_monitor_state() -> tuple[int, str | None] | None:
    if not PID_FILE.exists():
        return None
    try:
        parts = PID_FILE.read_text(encoding="utf-8").split()
        pid = int(parts[0])
    except (IndexError, OSError, ValueError):
        return None
    if pid <= 0:
        return None
    token = parts[1] if len(parts) == 2 else None
    return pid, token


def read_pid() -> int | None:
    state = read_monitor_state()
    return state[0] if state else None


def write_monitor_state(pid: int, token: str) -> None:
    temporary = PID_FILE.with_name(f".{PID_FILE.name}.{pid}")
    temporary.write_text(f"{pid} {token}\n", encoding="utf-8")
    os.replace(temporary, PID_FILE)


def unlink_monitor_state_if_matches(pid: int, token: str) -> None:
    if read_monitor_state() != (pid, token):
        return
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def stop_requested(token: str) -> bool:
    try:
        return STOP_FILE.read_text(encoding="utf-8").strip() == token
    except OSError:
        return False


def clear_stop_request(token: str | None = None) -> None:
    if token is not None and not stop_requested(token):
        return
    try:
        STOP_FILE.unlink()
    except FileNotFoundError:
        pass


def monitor_is_running() -> bool:
    lock = try_acquire_monitor_lock(shared=True)
    if lock is None:
        return True
    release_monitor_lock(lock)
    return False


def require_not_running() -> None:
    control_lock = acquire_control_lock()
    try:
        lock = try_acquire_monitor_lock(shared=True)
        if lock is None:
            pid = read_pid()
            raise SystemExit(f"monitor is already running (pid={pid})")
        try:
            PID_FILE.unlink(missing_ok=True)
            clear_stop_request()
        finally:
            release_monitor_lock(lock)
    finally:
        release_monitor_lock(control_lock)


def start_monitor(interval_seconds: int) -> None:
    if interval_seconds <= 0:
        raise SystemExit("monitor interval must be greater than 0")

    ensure_dirs()
    observed_generation = read_generation()
    control_lock = acquire_control_lock()
    monitor_lock = None
    try:
        if read_generation() != observed_generation:
            raise SystemExit("start request was superseded by another monitor command")

        monitor_lock = try_acquire_monitor_lock()
        if monitor_lock is None:
            raise SystemExit(f"monitor is already running (pid={read_pid()})")

        generation = advance_generation()
        PID_FILE.unlink(missing_ok=True)
        clear_stop_request()
        lock_fd = monitor_lock.fileno()
        env = os.environ.copy()
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "kosu_tracker.cli",
                "run-monitor",
                "--interval",
                str(interval_seconds),
                "--lock-fd",
                str(lock_fd),
                "--generation",
                generation,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            pass_fds=(lock_fd,),
        )
        monitor_lock.close()
        monitor_lock = None
        release_monitor_lock(control_lock)
        control_lock = None

        time.sleep(1)
        control_lock = acquire_control_lock()
        state = read_monitor_state()
        if state and state[1] and monitor_is_running():
            pid, _ = state
            print(f"started monitor (pid={pid})")
            print(f"log directory: {LOG_DIR}")
        else:
            if read_generation() == generation:
                advance_generation()
            raise SystemExit("failed to start monitor; check macOS Automation/Accessibility permissions")
    finally:
        if monitor_lock is not None:
            release_monitor_lock(monitor_lock)
        if control_lock is not None:
            release_monitor_lock(control_lock)


def _stop_monitor() -> None:
    state = read_monitor_state()
    if not state or not state[1] or not monitor_is_running():
        lock = try_acquire_monitor_lock(shared=True)
        if lock is not None:
            try:
                PID_FILE.unlink(missing_ok=True)
                clear_stop_request()
            finally:
                release_monitor_lock(lock)
        raise SystemExit("monitor is not running")

    pid, token = state
    STOP_FILE.write_text(f"{token}\n", encoding="utf-8")

    for _ in range(20):
        current = read_monitor_state()
        if current != state or not monitor_is_running():
            break
        time.sleep(0.2)
    else:
        raise SystemExit(f"failed to stop monitor (pid={pid})")

    cleanup_lock = try_acquire_monitor_lock(shared=True)
    if cleanup_lock is not None:
        try:
            unlink_monitor_state_if_matches(pid, token)
            clear_stop_request(token)
        finally:
            release_monitor_lock(cleanup_lock)
    print(f"stopped monitor (pid={pid})")


def stop_monitor() -> None:
    control_lock = acquire_control_lock()
    try:
        advance_generation()
        _stop_monitor()
    finally:
        release_monitor_lock(control_lock)


def print_status() -> None:
    control_lock = acquire_control_lock()
    try:
        pid = read_pid()
        running = monitor_is_running()
        print(f"running: {'yes' if running else 'no'}")
        if running:
            print(f"pid: {pid}")
        print(f"log directory: {LOG_DIR}")
        if LATEST_FILE.exists():
            latest = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
            print("latest sample:")
            print(json.dumps(latest, ensure_ascii=False, indent=2))
    finally:
        release_monitor_lock(control_lock)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kosu", description="Background worklog tracker for macOS")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start the background monitor")
    start.add_argument("--interval", type=int, default=60, help="Sampling interval in seconds")

    sub.add_parser("stop", help="Stop the background monitor")
    sub.add_parser("status", help="Show monitor status")

    sample = sub.add_parser("sample", help="Collect and print a single sample")
    sample.add_argument("--json", action="store_true", help="Print JSON only")

    run_monitor = sub.add_parser("run-monitor", help=argparse.SUPPRESS)
    run_monitor.add_argument("--interval", type=int, default=60)
    run_monitor.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    run_monitor.add_argument("--generation", help=argparse.SUPPRESS)

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
        if args.lock_fd is None:
            monitor_loop(interval_seconds=args.interval)
        else:
            monitor_loop(
                interval_seconds=args.interval,
                lock_fd=args.lock_fd,
                generation=args.generation,
            )
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
