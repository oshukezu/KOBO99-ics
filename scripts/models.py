from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

HEADING_RE = re.compile(
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})\s*週[一二三四五六日天]\s*Kobo99選書\s*[：:]\s*(?P<title>.+)"
)


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
