from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

HEADING_RE = re.compile(
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})\s*週[一二三四五六日天]\s*Kobo99選書\s*[：:]\s*(?P<title>.+)"
)
PLACEHOLDER_TITLE = "目前尚未公布資料，請耐心等候"
BLOG_WEEK_RE = re.compile(r"weekly-dd99-(?P<year>\d{4})-w(?P<week>\d+)")


@dataclass(frozen=True)
class SaleEvent:
    date: str
    title: str
    book_url: str
    source_url: str
    placeholder: bool = False
    provisional: bool = False

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


def sale_week_dates(year: int, week: int) -> set[str]:
    thursday = date.fromisocalendar(year, week, 4)
    return {(thursday + timedelta(days=offset)).isoformat() for offset in range(7)}


def blog_sale_dates(source_url: str) -> set[str]:
    match = BLOG_WEEK_RE.search(source_url)
    if not match:
        return set()
    try:
        return sale_week_dates(int(match.group("year")), int(match.group("week")))
    except ValueError:
        return set()


def load_existing(path: Path) -> list[SaleEvent]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SaleEvent(**item) for item in data]


def merge_events(events: Iterable[SaleEvent]) -> list[SaleEvent]:
    by_key: dict[tuple[str, str], SaleEvent] = {}
    for event in events:
        by_key[(event.date, event.title)] = event
    merged = list(by_key.values())
    real_sources = {
        event.source_url
        for event in merged
        if not event.placeholder and not event.provisional
    }
    covered_dates: set[str] = set()
    for source_url in real_sources:
        covered_dates.update(blog_sale_dates(source_url))
    kept = [
        event
        for event in merged
        if not (event.placeholder and event.source_url in real_sources)
        and not (event.provisional and event.date in covered_dates)
    ]
    return sorted(kept, key=lambda item: (item.date, item.title))
