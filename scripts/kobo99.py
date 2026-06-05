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
    today_str = datetime.now(TAIPEI).date().isoformat()
    today_events = [event for event in events if event.date == today_str]
    rows = "\n".join(
        f"""          <tr>
            <td class="date-cell"><time datetime="{html.escape(event.date)}">{html.escape(event.date)}</time></td>
            <td class="title-cell"><a href="{html.escape(event.book_url)}" target="_blank">{html.escape(event.title)}</a></td>
          </tr>"""
        for event in today_events
    )
    generated = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    path.write_text(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kobo 99 元選書行事曆訂閱</title>
  <meta name="description" content="錯過KOBO每日99元選書？讓它自動住進你的行事曆！無需每天手動檢查，一鍵訂閱 ICS 檔案，天天自動同步當日特價書單。">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #09090b;
      --card-bg: rgba(24, 24, 27, 0.65);
      --card-border: rgba(255, 255, 255, 0.08);
      --text-primary: #f4f4f5;
      --text-secondary: #a1a1aa;
      --text-muted: #71717a;
      --accent: #0070f3;
      --accent-gradient: linear-gradient(135deg, #0070f3 0%, #00dfd8 100%);
      --accent-glow: rgba(0, 112, 243, 0.15);
      --font: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      background-color: var(--bg);
      color: var(--text-primary);
      font-family: var(--font);
      line-height: 1.6;
      overflow-x: hidden;
      -webkit-font-smoothing: antialiased;
    }}

    /* Background Glow Effects */
    .glow-bg {{
      position: absolute;
      top: -10%;
      left: 50%;
      transform: translateX(-50%);
      width: min(100%, 800px);
      height: 600px;
      background: radial-gradient(circle, rgba(0, 112, 243, 0.15) 0%, rgba(0, 223, 216, 0.03) 70%, transparent 100%);
      z-index: -1;
      pointer-events: none;
      filter: blur(80px);
    }}

    /* Container */
    .container {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 0 24px;
    }}

    /* Navbar */
    header.nav {{
      border-bottom: 1px solid var(--card-border);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      background-color: rgba(9, 9, 11, 0.7);
      position: sticky;
      top: 0;
      z-index: 100;
    }}

    header.nav .nav-container {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      height: 64px;
    }}

    .logo-group {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .logo-icon {{
      width: 32px;
      height: 32px;
      background: var(--accent-gradient);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 1.1rem;
      color: #fff;
    }}

    .logo-text {{
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.5px;
      background: var(--accent-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}

    .github-link {{
      color: var(--text-secondary);
      text-decoration: none;
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.9rem;
      font-weight: 500;
      transition: color 0.2s;
    }}

    .github-link:hover {{
      color: var(--text-primary);
    }}

    /* Hero Section */
    .hero {{
      padding: 32px 0 64px;
      text-align: center;
      position: relative;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--card-border);
      border-radius: 99px;
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--text-secondary);
      margin-bottom: 24px;
    }}

    .badge-dot {{
      width: 6px;
      height: 6px;
      background-color: #00dfd8;
      border-radius: 50%;
      box-shadow: 0 0 8px #00dfd8;
    }}

    h1.hero-title {{
      font-size: clamp(2rem, 5vw, 3.5rem);
      font-weight: 800;
      line-height: 1.15;
      letter-spacing: -1px;
      margin-bottom: 20px;
      color: var(--text-primary);
    }}

    .gradient-text {{
      background: var(--accent-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      display: inline-block;
    }}

    p.hero-subtitle {{
      font-size: clamp(1rem, 2vw, 1.2rem);
      color: var(--text-secondary);
      max-width: 640px;
      margin: 0 auto 36px;
    }}

    .cta-group {{
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 16px;
    }}

    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 12px 24px;
      border-radius: 8px;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      text-decoration: none;
    }}

    .btn-primary {{
      background: var(--accent-gradient);
      color: #fff;
      border: none;
      box-shadow: 0 4px 20px var(--accent-glow);
    }}

    .btn-primary:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 30px rgba(0, 112, 243, 0.3);
    }}

    .btn-secondary {{
      background: rgba(255, 255, 255, 0.03);
      color: var(--text-primary);
      border: 1px solid var(--card-border);
    }}

    .btn-secondary:hover {{
      background: rgba(255, 255, 255, 0.06);
      border-color: rgba(255, 255, 255, 0.15);
      transform: translateY(-2px);
    }}

    /* Steps Section */
    .steps-section {{
      padding: 64px 0;
    }}

    .section-header {{
      text-align: center;
      margin-bottom: 48px;
    }}

    .section-header h2 {{
      font-size: 2rem;
      font-weight: 800;
      letter-spacing: -0.5px;
      margin-bottom: 12px;
    }}

    .section-header p {{
      color: var(--text-secondary);
    }}

    .steps-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 24px;
    }}

    .step-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 32px 24px;
      position: relative;
      overflow: hidden;
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      transition: border-color 0.3s;
    }}

    .step-card:hover {{
      border-color: rgba(0, 112, 243, 0.3);
    }}

    .step-num {{
      font-size: 3rem;
      font-weight: 800;
      background: linear-gradient(135deg, rgba(0, 112, 243, 0.2) 0%, rgba(0, 223, 216, 0.05) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      position: absolute;
      top: 10px;
      right: 20px;
      user-select: none;
    }}

    .step-title {{
      font-size: 1.15rem;
      font-weight: 700;
      margin-bottom: 12px;
      color: var(--text-primary);
    }}

    .step-desc {{
      font-size: 0.95rem;
      color: var(--text-secondary);
    }}

    /* Features Section */
    .features-section {{
      padding: 64px 0;
    }}

    .features-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 24px;
    }}

    .feature-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 32px 24px;
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      transition: all 0.3s;
    }}

    .feature-card:hover {{
      border-color: rgba(0, 223, 216, 0.3);
      transform: translateY(-2px);
    }}

    .feature-icon-wrapper {{
      width: 44px;
      height: 44px;
      border-radius: 8px;
      background: rgba(0, 112, 243, 0.1);
      border: 1px solid rgba(0, 112, 243, 0.2);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #0070f3;
      margin-bottom: 20px;
    }}

    .feature-card:nth-child(2) .feature-icon-wrapper {{
      background: rgba(0, 223, 216, 0.1);
      border: 1px solid rgba(0, 223, 216, 0.2);
      color: #00dfd8;
    }}

    .feature-title {{
      font-size: 1.15rem;
      font-weight: 700;
      margin-bottom: 12px;
    }}

    .feature-desc {{
      font-size: 0.95rem;
      color: var(--text-secondary);
    }}

    /* Table Section */
    .table-section {{
      padding: 64px 0 32px;
    }}

    .table-wrapper {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      overflow: hidden;
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
    }}

    th {{
      padding: 16px 24px;
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}

    td {{
      padding: 18px 24px;
      border-bottom: 1px solid var(--card-border);
      vertical-align: middle;
      font-size: 0.95rem;
    }}

    tr:last-child td {{
      border-bottom: none;
    }}

    tr {{
      transition: background-color 0.2s;
    }}

    tr:hover {{
      background-color: rgba(255, 255, 255, 0.015);
    }}

    .date-cell {{
      color: var(--text-secondary);
      font-weight: 500;
      white-space: nowrap;
    }}

    .title-cell a {{
      color: var(--text-primary);
      text-decoration: none;
      font-weight: 600;
      transition: color 0.2s;
    }}

    .title-cell a:hover {{
      color: #00dfd8;
    }}

    .source-cell a {{
      color: #0070f3;
      text-decoration: none;
      font-size: 0.85rem;
      font-weight: 500;
      transition: opacity 0.2s;
    }}

    .source-cell a:hover {{
      opacity: 0.8;
    }}

    /* Toast Notification */
    .toast {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: rgba(24, 24, 27, 0.85);
      border: 1px solid rgba(0, 223, 216, 0.3);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-radius: 8px;
      padding: 12px 20px;
      display: flex;
      align-items: center;
      gap: 10px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5), 0 0 15px rgba(0, 223, 216, 0.15);
      z-index: 1000;
      transform: translateY(100px);
      opacity: 0;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      pointer-events: none;
    }}

    .toast.show {{
      transform: translateY(0);
      opacity: 1;
    }}

    .toast-icon {{
      color: #00dfd8;
      display: flex;
      align-items: center;
    }}

    .toast-text {{
      font-size: 0.9rem;
      font-weight: 500;
      color: var(--text-primary);
    }}

    /* Footer */
    footer {{
      border-top: 1px solid var(--card-border);
      padding: 40px 0;
      color: var(--text-muted);
      font-size: 0.85rem;
      text-align: center;
    }}

    .footer-disclaimer {{
      max-width: 720px;
      margin: 0 auto 20px;
      line-height: 1.5;
    }}

    /* Responsive */
    @media (max-width: 768px) {{
      .hero {{
        padding: 24px 0 40px;
      }}
      .table-section {{
        padding: 32px 0 16px;
      }}
      .steps-grid, .features-grid {{
        grid-template-columns: 1fr;
      }}
      th {{
        display: none;
      }}
      td {{
        display: block;
        padding: 10px 24px;
        border-bottom: none;
      }}
      td:first-child {{
        padding-top: 20px;
      }}
      td:last-child {{
        padding-bottom: 20px;
        border-bottom: 1px solid var(--card-border);
      }}
      tr:last-child td:last-child {{
        border-bottom: none;
      }}
    }}
  </style>
</head>
<body>

  <div class="glow-bg"></div>

  <header class="nav">
    <div class="container nav-container">
      <div class="logo-group">
        <div class="logo-icon">K</div>
        <span class="logo-text">KOBO 99</span>
      </div>
      <a class="github-link" href="https://github.com/oshukezu/KOBO99-ics" target="_blank">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path></svg>
        GitHub
      </a>
    </div>
  </header>

  <main class="container">
    
    <!-- Table Section -->
    <section class="table-section">
      <div class="section-header">
        <h2>今日特價選書預覽</h2>
        <p>今日的折扣書籍，點擊連結詳閱書目介紹</p>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>特價日期</th>
              <th>今日特價書名 (附 Kobo 連結)</th>
            </tr>
          </thead>
          <tbody>
{rows or '            <tr><td colspan="2" style="text-align:center; color:var(--text-muted);">今日暫無特價選書。</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Hero Section -->
    <section class="hero">
      <div class="badge">
        <span class="badge-dot"></span>
        開源、安全、每週自動同步
      </div>
      <h1 class="hero-title">
        錯過KOBO每日99元活動？<br>
        <span class="gradient-text">讓它自動住進你的行事曆！</span>
      </h1>
      <p class="hero-subtitle">
        無需每天手動檢查。一鍵訂閱 ICS 檔案，自動同步當日特價書單，支援 Google｜Apple｜Outlook 行事曆。
      </p>
      <div class="cta-group">
        <button class="btn btn-primary" id="copy-btn">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
          一鍵複製訂閱連結
        </button>
        <a class="btn btn-secondary" href="https://github.com/oshukezu/KOBO99-ics" target="_blank">
          前往 GitHub 看原始碼
        </a>
      </div>
    </section>

    <!-- Steps Section -->
    <section class="steps-section">
      <div class="section-header">
        <h2>三步完成訂閱</h2>
        <p>簡單設定，讓日曆天天為你報到</p>
      </div>
      <div class="steps-grid">
        <div class="step-card">
          <span class="step-num">01</span>
          <h3 class="step-title">複製連結</h3>
          <p class="step-desc">點擊上方按鈕複製行事曆專屬 ICS 訂閱連結到剪貼簿。</p>
        </div>
        <div class="step-card">
          <span class="step-num">02</span>
          <h3 class="step-title">打開行事曆</h3>
          <p class="step-desc">打開 Google Calendar 或 Apple 日曆設定介面。</p>
        </div>
        <div class="step-card">
          <span class="step-num">03</span>
          <h3 class="step-title">加入網址</h3>
          <p class="step-desc">選擇「新增日曆」>「加入日曆網址」，貼上複製的連結並儲存即可！</p>
        </div>
      </div>
    </section>

    <!-- Features Section -->
    <section class="features-section">
      <div class="section-header">
        <h2>專為閱讀愛好者打造</h2>
        <p>極簡、輕量且完全自由的開源工具</p>
      </div>
      <div class="features-grid">
        <div class="feature-card">
          <div class="feature-icon-wrapper">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path></svg>
          </div>
          <h3 class="feature-title">每週自動更新</h3>
          <p class="feature-desc">每週自動爬取最新官網資訊，特價書單絕不漏接。</p>
        </div>
        <div class="feature-card">
          <div class="feature-icon-wrapper">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
          </div>
          <h3 class="feature-title">100% 隱私安全</h3>
          <p class="feature-desc">無須註冊、不留個資、完全開源。專案原始碼公開透明，所有行為皆可稽核。</p>
        </div>
        <div class="feature-card">
          <div class="feature-icon-wrapper">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
          </div>
          <h3 class="feature-title">輕量無感</h3>
          <p class="feature-desc">純 ICS 標準行事曆訂閱機制。不佔用手機本機儲存空間，不耗費額外網路與電力。</p>
        </div>
      </div>
    </section>



  </main>

  <!-- Toast Notification -->
  <div class="toast" id="toast">
    <div class="toast-icon">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
    </div>
    <div class="toast-text">已複製訂閱連結到剪貼簿！</div>
  </div>

  <footer>
    <div class="container">
      <p class="footer-disclaimer">
        免責宣告：本專案為個人非營利性質之開源自動化工具，所產生之行事曆僅供參考。書單資訊與特價時效以 Rakuten Kobo 官網公布為最終基準。
      </p>
      <p>© 2026 J.J. Wang. All rights reserved. | 最後產生時間：{html.escape(generated)}</p>
    </div>
  </footer>

  <script>
    // 動態更新當日特價書單（與實際日期同步）
    async function updateTodayBooks() {{
      try {{
        const todayStr = new Intl.DateTimeFormat('zh-TW', {{
          timeZone: 'Asia/Taipei',
          year: 'numeric',
          month: '2-digit',
          day: '2-digit'
        }}).format(new Date()).replace(/\//g, '-');
        
        const response = await fetch('public/events.json');
        if (!response.ok) return;
        const events = await response.json();
        
        const todayEvents = events.filter(e => e.date === todayStr);
        const tbody = document.querySelector('.table-section tbody');
        if (!tbody) return;
        
        if (todayEvents.length > 0) {{
          tbody.innerHTML = todayEvents.map(event => `
            <tr>
              <td class="date-cell"><time datetime="${{escapeHtml(event.date)}}">${{escapeHtml(event.date)}}</time></td>
              <td class="title-cell"><a href="${{escapeHtml(event.book_url)}}" target="_blank">${{escapeHtml(event.title)}}</a></td>
            </tr>
          `).join('');
        }} else {{
          tbody.innerHTML = '<tr><td colspan="2" style="text-align:center; color:var(--text-muted);">今日暫無特價選書。</td></tr>';
        }}
      }} catch (err) {{
        console.error('無法動態更新今日書單', err);
      }}
    }}

    function escapeHtml(str) {{
      return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }}

    window.addEventListener('DOMContentLoaded', updateTodayBooks);

    const copyBtn = document.getElementById('copy-btn');
    const toast = document.getElementById('toast');

    copyBtn.addEventListener('click', async () => {{
      let icsUrl = new URL("public/kobo99.ics", window.location.href).href;
      if (!icsUrl.startsWith('http')) {{
        icsUrl = "https://oshukezu.github.io/KOBO99-ics/public/kobo99.ics";
      }}

      try {{
        await navigator.clipboard.writeText(icsUrl);
        
        toast.classList.add('show');
        setTimeout(() => {{
          toast.classList.remove('show');
        }}, 3000);
      }} catch (err) {{
        console.error('無法複製連結', err);
        const el = document.createElement('textarea');
        el.value = icsUrl;
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);

        toast.classList.add('show');
        setTimeout(() => {{
          toast.classList.remove('show');
        }}, 3000);
      }}
    }});
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
    write_index(events, Path("index.html"))
    print(f"Wrote {len(events)} event(s) to {output_dir}")


if __name__ == "__main__":
    main()
