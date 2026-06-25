#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import sys
from pathlib import Path

# 將 scripts 目錄加入 sys.path 以利直接執行
sys.path.insert(0, str(Path(__file__).parent))

from models import load_existing, merge_events
from renderer import write_ics, write_index, write_json
from scraper import TAIPEI, scrape_targets, week_targets


def build_targets(args: argparse.Namespace) -> list[tuple[int, int]]:
    if args.history_start_year:
        today = datetime.now(TAIPEI).date()
        end_year = args.history_end_year or today.year
        if end_year < args.history_start_year:
            raise SystemExit("--history-end-year must be greater than or equal to --history-start-year")
        return [
            (year, week)
            for year in range(args.history_start_year, end_year + 1)
            for week in range(1, 55)
        ]
    if args.full_year:
        return [(year, week) for year in args.full_year for week in range(1, 55)]
    if args.year and args.week:
        return [(args.year, args.week)]
    today = datetime.now(TAIPEI).date()
    return week_targets(today, args.previous_weeks, args.next_weeks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Kobo 99 sale calendar files.")
    parser.add_argument("--out", default="public", help="Output directory.")
    parser.add_argument("--year", type=int, help="Fetch one year/week target.")
    parser.add_argument("--week", type=int, help="Fetch one year/week target.")
    parser.add_argument("--full-year", type=int, action="append", help="Fetch all weeks 1-54 for a year.")
    parser.add_argument("--history-start-year", type=int, help="Backfill every week from this year.")
    parser.add_argument("--history-end-year", type=int, help="Backfill through this year. Defaults to current year.")
    parser.add_argument("--previous-weeks", type=int, default=2, help="Weeks before current week to refresh.")
    parser.add_argument("--next-weeks", type=int, default=1, help="Weeks after current week to probe.")
    parser.add_argument("--delay-seconds", type=float, default=1.5, help="Delay between requests.")
    parser.add_argument(
        "--fetch-mode",
        choices=("auto", "direct", "browser"),
        default="auto",
        help="Fetch with urllib, Playwright browser, or direct first with browser fallback.",
    )
    parser.add_argument("--browser-timeout-seconds", type=int, default=60, help="Browser fetch timeout.")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Only rebuild ICS and HTML from existing events.json without fetching Kobo pages.",
    )
    history_group = parser.add_mutually_exclusive_group()
    history_group.add_argument(
        "--keep-existing",
        dest="keep_existing",
        action="store_true",
        default=True,
        help="Merge with existing events.json. This is the default.",
    )
    history_group.add_argument(
        "--replace-existing",
        dest="keep_existing",
        action="store_false",
        help="Discard existing events.json and rebuild only from fetched targets.",
    )
    parser.add_argument("--strict", action="store_true", help="Fail the run when a target URL cannot be fetched.")
    args = parser.parse_args()

    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_json = output_dir / "events.json"

    existing = load_existing(events_json) if args.keep_existing else []
    existing_urls = {event.source_url for event in existing}
    scraped = []
    if not args.render_only:
        scraped = scrape_targets(
            build_targets(args),
            delay_seconds=args.delay_seconds,
            fetch_mode=args.fetch_mode,
            browser_timeout_seconds=args.browser_timeout_seconds,
            strict=args.strict,
            existing_urls=existing_urls,
        )
    events = merge_events([*existing, *scraped])

    write_json(events, events_json)
    write_ics(events, output_dir / "kobo99.ics")
    write_index(events, Path("index.html"))
    print(f"Wrote {len(events)} event(s) to {output_dir}")


if __name__ == "__main__":
    main()
