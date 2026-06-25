from __future__ import annotations

import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from models import SaleEvent, parse_events

BASE_URL = "https://www.kobo.com/zh/blog/weekly-dd99-{year}-w{week}"
TAIPEI = ZoneInfo("Asia/Taipei")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36 Kobo99Calendar/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
}


class FetchBlocked(RuntimeError):
    pass


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    if value.isdigit():
        return min(float(value), 60.0)
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return min(max((parsed - datetime.now(timezone.utc)).total_seconds(), 0), 60.0)


def fetch_html_direct(url: str, retries: int = 2, delay_seconds: float = 1.5) -> str | None:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = Request(url, headers=HEADERS)
        try:
            with urlopen(request, timeout=25) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return body.decode(charset, errors="replace")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 403:
                raise FetchBlocked(f"HTTP 403 for {url}")
            if exc.code in {429, 500, 502, 503, 504}:
                retry_after = parse_retry_after(exc.headers.get("Retry-After"))
                time.sleep(retry_after or delay_seconds * (attempt + 1))
                last_error = exc
                continue
            raise
        except URLError as exc:
            last_error = exc
            time.sleep(delay_seconds * (attempt + 1))

    raise RuntimeError(f"Fetch failed for {url}: {last_error}")


def fetch_html_browser(url: str, timeout_seconds: int = 60, max_retries: int = 2) -> str | None:
    script_path = Path(__file__).parent / "fetch_page.js"
    last_error: Exception | None = None
    
    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"Browser fetch retry {attempt}/{max_retries} for {url}...")
            time.sleep(3.0 * attempt)
            
        try:
            result = subprocess.run(
                ["node", str(script_path), url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            if result.returncode == 44:
                return None
            if result.returncode != 0:
                print(f"Node 爬蟲腳本出錯 (Exit Code {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
                raise RuntimeError(f"Node script failed with code {result.returncode}")
            
            content = result.stdout
            if "Just a moment" in content and "Kobo99選書" not in content:
                raise FetchBlocked(f"Browser fetch still blocked for {url}")
            return content
        except subprocess.TimeoutExpired as exc:
            last_error = RuntimeError(f"Browser fetch timed out for {url}")
        except Exception as exc:
            last_error = exc
            
    raise RuntimeError(f"Browser fetch failed for {url} after {max_retries} retries: {last_error}")


def fetch_html(
    url: str,
    fetch_mode: str,
    delay_seconds: float = 1.5,
    browser_timeout_seconds: int = 60,
) -> str | None:
    if fetch_mode == "browser":
        return fetch_html_browser(url, timeout_seconds=browser_timeout_seconds)

    try:
        return fetch_html_direct(url, delay_seconds=delay_seconds)
    except FetchBlocked:
        if fetch_mode == "auto":
            print(f"Direct fetch blocked; retrying with browser: {url}")
            return fetch_html_browser(url, timeout_seconds=browser_timeout_seconds)
        raise


def week_targets(now: date, previous_weeks: int, next_weeks: int) -> list[tuple[int, int]]:
    monday = now - timedelta(days=now.weekday())
    targets = []
    for offset in range(-previous_weeks, next_weeks + 1):
        target = monday + timedelta(weeks=offset)
        iso = target.isocalendar()
        targets.append((iso.year, iso.week))
    return sorted(set(targets))


def should_skip_scrape(year: int, week: int, existing_urls: set[str], today: date) -> bool:
    try:
        sunday = date.fromisocalendar(year, min(week, 53), 7)
    except ValueError:
        sunday = date(year, 1, 1) + timedelta(weeks=week - 1) + timedelta(days=6)
    
    url = BASE_URL.format(year=year, week=week)
    if sunday < today and url in existing_urls:
        return True
    return False


def scrape_targets(
    targets: Iterable[tuple[int, int]],
    delay_seconds: float,
    fetch_mode: str,
    browser_timeout_seconds: int,
    strict: bool,
    existing_urls: set[str] | None = None,
) -> list[SaleEvent]:
    found: list[SaleEvent] = []
    today = datetime.now(TAIPEI).date()
    urls_in_cache = existing_urls or set()

    for index, (year, week) in enumerate(targets):
        url = BASE_URL.format(year=year, week=week)
        
        if should_skip_scrape(year, week, urls_in_cache, today):
            print(f"Smart Cache: Skipping scrape for {url} as it is in the past and already scraped.")
            continue

        if index:
            time.sleep(delay_seconds)
        print(f"Fetching {url}")
        try:
            html_text = fetch_html(
                url,
                fetch_mode=fetch_mode,
                delay_seconds=delay_seconds,
                browser_timeout_seconds=browser_timeout_seconds,
            )
        except Exception as exc:
            if strict:
                raise
            print(f"Fetch failed, skipping {url}: {exc}")
            continue
        if html_text is None:
            print(f"Not found: {url}")
            continue
        events = parse_events(url, year, week, html_text)
        print(f"Found {len(events)} event(s) in {url}")
        found.extend(events)
    return found
