"""Command-line entry point: `app daily`, `app monthly`, `app backfill`, `app costs`."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from app.budget import costs_report
from app.config import PROJECT_ROOT, load_settings
from app.db import connect

log = logging.getLogger("app")


def month_arg(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError("use the format YYYY-MM, for example 2026-10")
    return value


def date_arg(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", value):
        raise argparse.ArgumentTypeError("use the format YYYY-MM-DD, for example 2026-10-01")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app", description="AI funding intelligence: daily brief and monthly report."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="Run the daily pipeline and email.")
    daily.add_argument("--dry-run", action="store_true", help="Save the email preview to out/; send and save nothing else.")

    monthly = sub.add_parser("monthly", help="Build the monthly report and email.")
    monthly.add_argument("--month", type=month_arg, help="Month as YYYY-MM (default: current month).")
    monthly.add_argument("--dry-run", action="store_true", help="Save the email to out/ instead of sending.")

    backfill = sub.add_parser("backfill", help="Load past days into the database.")
    backfill.add_argument("--from", dest="from_date", type=date_arg, required=True, help="Start date YYYY-MM-DD.")
    backfill.add_argument("--dry-run", action="store_true", help="Only show how much there is to read and the estimated cost.")
    backfill.add_argument("--yes", action="store_true", help="Approve an estimated cost above $5 without asking.")

    alert = sub.add_parser("alert", help="Email a short failure notice (used by the scheduled runs).")
    alert.add_argument("--job", required=True, help="Name of the job that failed, for example daily.")
    alert.add_argument("--link", default="", help="Link to the full log.")
    alert.add_argument("--log", type=Path, help="Log file whose last lines are included.")

    costs = sub.add_parser("costs", help="Show AI spending for a month.")
    costs.add_argument("--month", type=month_arg, required=True, help="Month as YYYY-MM.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # never crash on characters the terminal can't show
    args = build_parser().parse_args(argv)
    settings = load_settings()

    if args.command == "costs":
        conn = connect(settings.db_path)
        print(costs_report(conn, args.month, settings.max_monthly_llm_usd))
        conn.close()
        return 0

    if args.command == "daily":
        from app.pipeline import run_daily  # imported here so `app costs` starts fast
        from app.report import format_report

        try:
            result = run_daily(settings, dry_run=args.dry_run)
        except RuntimeError as exc:  # for example: a key is missing, or the email could not be sent
            print(f"Stopped: {exc}")
            return 1
        if result.email is None:  # already sent today
            print(result.status)
            return 0
        report = format_report(result)
        print(report)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_dir = PROJECT_ROOT / "out"
        out_dir.mkdir(exist_ok=True)
        base = out_dir / f"daily_{stamp}{'_dry_run' if args.dry_run else ''}"
        base.with_suffix(".txt").write_text(report, encoding="utf-8")
        base.with_suffix(".html").write_text(result.email.html, encoding="utf-8")
        base.with_name(base.name + "_email.txt").write_text(result.email.text, encoding="utf-8")
        print(f"\nEmail preview saved to: {base.with_suffix('.html')}")
        return 0

    if args.command == "monthly":
        from app.monthly import run_monthly

        try:
            monthly = run_monthly(settings, args.month, dry_run=args.dry_run)
        except (RuntimeError, ValueError) as exc:
            print(f"Stopped: {exc}")
            return 1
        print(monthly.status)
        if monthly.insights and monthly.narrative:
            i, n = monthly.insights, monthly.narrative
            print(f"{i.month_label}: {i.deal_count} deals, ${i.total_usd / 1e6:,.1f}M raised. Commentary source: {n.source}.")
            if n.problems:
                print("Checks that rejected the AI draft: " + "; ".join(n.problems[:5]))
            print(f"AI cost (paid-plan estimate): ${n.usage.cost_usd:.4f}")
            print("Files:\n  " + "\n  ".join(str(f) for f in monthly.files))
        return 0

    if args.command == "alert":
        from app.alert import send_failure_alert

        try:
            print(f"Alert sent: {send_failure_alert(settings, args.job, args.link, args.log)}")
        except RuntimeError as exc:
            print(f"Could not send the alert: {exc}")
            return 1
        return 0

    from app.backfill import run_backfill

    def approve(estimate: float) -> bool:
        if args.yes:
            return True
        if not sys.stdin.isatty():
            return False
        return input(f"Estimated cost ${estimate:.2f} is above $5. Continue? [y/N] ").strip().lower() == "y"

    try:
        done = run_backfill(settings, date.fromisoformat(args.from_date), approve, dry_run=args.dry_run)
    except (RuntimeError, ValueError) as exc:
        print(f"Stopped: {exc}")
        return 1
    print(f"Articles found from {done.start}: {done.articles_found} (oldest: {done.oldest_article or 'none'}). Feeds only list recent articles, so this may be later than your start date.")
    print(f"Events to read: {done.events}. Estimated cost: up to ${done.estimated_usd:.2f} (paid-plan prices; $0 on the free tier). About {done.estimated_minutes} min.")
    print(done.status)
    if done.processed:
        p = done.processed
        print(f"Saved: {dict(p.counts)}; already known: {len(p.known)}; not read (cap): {len(p.deferred)}; needing review: {len(p.review)}.")
        print(f"Actual AI cost (paid-plan estimate): ${p.usage.cost_usd:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
