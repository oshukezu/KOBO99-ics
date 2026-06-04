#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BASE_URL = "https://www.kobo.com/zh/blog/weekly-dd99-{year}-w{week}"
TAIPEI = ZoneInfo("Asia/Taipei")
HEADING_RE = re.compile(
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})\s*週[一二三四五六日天]\s*Kobo99選書\s*[：:]\s*(?P<title>.+)"
)
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


@dataclass(frozen=True)
class SaleEvent:
    date: str
    title: str
    book_url: str
    source_url: str

    @property
    def uid(self) -> str:
        raw = f"{self.date}|{self.title}|{self.book_url}"
        return f"{hashlib.sha1(raw.encode('utf-8')).hexdigest()}@kobo99"


class HeadingParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.headings: list[dict[str, object]] = []
        self._current: dict[str, object] | None = None
        self._tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if re.fullmatch(r"h[1-6]", tag):
            self._tag = tag
            self._current = {"text": [], "links": []}
            return

        if self._current is not None and tag == "a":
            attrs_map = dict(attrs)
            href = attrs_map.get("href")
            if href:
                self._current["links"].append(urljoin(self.page_url, href))

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and tag == self._tag:
            text = "".join(self._current["text"])
            text = re.sub(r"\s+", " ", text).strip()
            self.headings.append({"text": text, "links": list(self._current["links"])})
            self._current = None
            self._tag = None


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


def fetch_html_browser(url: str, timeout_seconds: int = 60) -> str | None:
    script_path = Path(__file__).parent / "fetch_page.js"
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
        raise RuntimeError(f"Browser fetch timed out for {url}") from exc
    except Exception as exc:
        raise RuntimeError(f"Browser fetch failed for {url}: {exc}") from exc


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


def clean_title(raw: str) -> str:
    title = html.unescape(raw).strip()
    if title.startswith("《") and title.endswith("》"):
        title = title[1:-1]
    return title.strip()


def infer_sale_date(article_year: int, article_week: int, month: int, day: int) -> date:
    candidates = []
    for candidate_year in (article_year - 1, article_year, article_year + 1):
        try:
            candidate = date(candidate_year, month, day)
        except ValueError:
            continue
        candidates.append(candidate)

    try:
        anchor = date.fromisocalendar(article_year, min(article_week, 53), 3)
    except ValueError:
        anchor = date(article_year, 1, 1) + timedelta(weeks=article_week - 1)

    return min(candidates, key=lambda item: abs((item - anchor).days))


def parse_events(page_url: str, article_year: int, article_week: int, html_text: str) -> list[SaleEvent]:
    parser = HeadingParser(page_url)
    parser.feed(html_text)
    events: list[SaleEvent] = []

    for heading in parser.headings:
        text = str(heading["text"])
        match = HEADING_RE.search(text)
        if not match:
            continue

        links = [link for link in heading["links"] if "/ebook/" in link]
        if not links:
            continue

        event_date = infer_sale_date(
            article_year,
            article_week,
            int(match.group("month")),
            int(match.group("day")),
        )
        events.append(
            SaleEvent(
                date=event_date.isoformat(),
                title=clean_title(match.group("title")),
                book_url=links[0],
                source_url=page_url,
            )
        )

    return events


def week_targets(now: date, previous_weeks: int, next_weeks: int) -> list[tuple[int, int]]:
    monday = now - timedelta(days=now.weekday())
    targets = []
    for offset in range(-previous_weeks, next_weeks + 1):
        target = monday + timedelta(weeks=offset)
        iso = target.isocalendar()
        targets.append((iso.year, iso.week))
    return sorted(set(targets))


def scrape_targets(
    targets: Iterable[tuple[int, int]],
    delay_seconds: float,
    fetch_mode: str,
    browser_timeout_seconds: int,
    strict: bool,
) -> list[SaleEvent]:
    found: list[SaleEvent] = []
    for index, (year, week) in enumerate(targets):
        if index:
            time.sleep(delay_seconds)
        url = BASE_URL.format(year=year, week=week)
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


def load_existing(path: Path) -> list[SaleEvent]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SaleEvent(**item) for item in data]


def merge_events(events: Iterable[SaleEvent]) -> list[SaleEvent]:
    by_key: dict[tuple[str, str], SaleEvent] = {}
    for event in events:
        by_key[(event.date, event.title)] = event
    return sorted(by_key.values(), key=lambda item: (item.date, item.title))


def escape_ics(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_ics_line(line: str) -> list[str]:
    lines: list[str] = []
    current = ""
    size = 0
    for char in line:
        char_size = len(char.encode("utf-8"))
        if current and size + char_size > 75:
            lines.append(current)
            current = " " + char
            size = 1 + char_size
        else:
            current += char
            size += char_size
    lines.append(current)
    return lines


def write_ics(events: list[SaleEvent], path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Kobo99 Calendar//KOBO99//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Kobo 99 元選書",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]

    for event in events:
        start = date.fromisoformat(event.date)
        end = start + timedelta(days=1)
        description = "\n".join(
            [
                f"書名：{event.title}",
                f"來源文章：{event.source_url}",
            ]
        )
        raw_lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.uid}",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
                f"SUMMARY:{escape_ics(event.title)}",
                f"DESCRIPTION:{escape_ics(description)}",
                f"URL:{escape_ics(event.book_url)}",
                "END:VEVENT",
            ]
        )

    raw_lines.append("END:VCALENDAR")
    folded = [part for line in raw_lines for part in fold_ics_line(line)]
    path.write_text("\r\n".join(folded) + "\r\n", encoding="utf-8")


def write_json(events: list[SaleEvent], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(event) for event in events], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_index(events: list[SaleEvent], path: Path) -> None:
    latest = [event for event in events if date.fromisoformat(event.date) >= datetime.now(TAIPEI).date()]
    if not latest:
        latest = events[-12:]
    latest = latest[:24]
    rows = "\n".join(
        f"""          <tr>
            <td><time datetime="{html.escape(event.date)}">{html.escape(event.date)}</time></td>
            <td><a href="{html.escape(event.book_url)}">{html.escape(event.title)}</a></td>
            <td><a href="{html.escape(event.source_url)}">來源文章</a></td>
          </tr>"""
        for event in latest
    )
    generated = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    path.write_text(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kobo 99 元選書行事曆</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f5ef;
      --panel: #ffffff;
      --text: #232323;
      --muted: #64605a;
      --line: #ddd7cc;
      --accent: #0f6c5a;
      --accent-dark: #0a4f42;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }}
    main {{
      width: min(960px, calc(100% - 32px));
      margin: 48px auto;
    }}
    header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 24px;
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(1.9rem, 4vw, 3rem);
      letter-spacing: 0;
    }}
    p {{ margin: 0; color: var(--muted); }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
      min-width: 240px;
    }}
    .button {{
      display: inline-flex;
      min-height: 42px;
      align-items: center;
      justify-content: center;
      border-radius: 8px;
      padding: 10px 14px;
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }}
    .button.secondary {{
      background: transparent;
      color: var(--accent-dark);
      border: 1px solid var(--line);
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 700;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    td:first-child {{ width: 132px; color: var(--muted); }}
    a {{ color: var(--accent-dark); }}
    footer {{
      margin-top: 16px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    @media (max-width: 720px) {{
      main {{ margin: 28px auto; }}
      header {{ display: block; }}
      .actions {{ justify-content: flex-start; margin-top: 18px; }}
      table, tbody, tr, td {{ display: block; width: 100%; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); padding: 12px 0; }}
      tr:last-child {{ border-bottom: 0; }}
      td {{ border: 0; padding: 4px 16px; }}
      td:first-child {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Kobo 99 元選書行事曆</h1>
        <p>每週三 22:00（台北時間）更新，來源為 Kobo 公開部落格文章。</p>
      </div>
      <nav class="actions" aria-label="訂閱連結">
        <a class="button" id="google-link" href="https://calendar.google.com/calendar/render">Google Calendar 訂閱</a>
        <a class="button secondary" href="kobo99.ics">下載 ICS</a>
        <a class="button secondary" href="events.json">查看 JSON</a>
      </nav>
    </header>
    <section aria-label="近期選書">
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>書名</th>
            <th>來源</th>
          </tr>
        </thead>
        <tbody>
{rows or '          <tr><td colspan="3">目前還沒有選書資料。</td></tr>'}
        </tbody>
      </table>
    </section>
    <footer>最後產生時間：{html.escape(generated)}</footer>
  </main>
  <script>
    const icsUrl = new URL("kobo99.ics", window.location.href).href;
    document.getElementById("google-link").href =
      "https://calendar.google.com/calendar/render?cid=" + encodeURIComponent(icsUrl);
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


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
    scraped = []
    if not args.render_only:
        scraped = scrape_targets(
            build_targets(args),
            delay_seconds=args.delay_seconds,
            fetch_mode=args.fetch_mode,
            browser_timeout_seconds=args.browser_timeout_seconds,
            strict=args.strict,
        )
    events = merge_events([*existing, *scraped])

    write_json(events, events_json)
    write_ics(events, output_dir / "kobo99.ics")
    write_index(events, output_dir / "index.html")
    print(f"Wrote {len(events)} event(s) to {output_dir}")


if __name__ == "__main__":
    main()
