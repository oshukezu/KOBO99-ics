from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from models import SaleEvent, clean_title, merge_events, sale_week_dates
from scraper import TAIPEI, fetch_html, pending_sale_week, week_url

HOME_URL = "https://www.kobo.com/tw/zh"
TODAY_RE = re.compile(r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})(?:\s|<[^>]+>)*今日(?:\s|<[^>]+>)*99")
ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"']([^\"']*?/ebook/[^\"']*)[\"'][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
NEXT_HEADING_RE = re.compile(r"<h[12]\b", re.IGNORECASE)


class HomepageFetchError(RuntimeError):
    pass


@dataclass
class _Section:
    level: int
    text: str
    links: list[tuple[str, str]]


class _SectionParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.sections: list[_Section] = []
        self._level = 0
        self._in_heading = False
        self._text: list[str] = []
        self._links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def close(self) -> None:
        self._flush()
        super().close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._level = int(tag[1])
            self._in_heading = True
            self._text = []
            self._links = []
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = urljoin(self.page_url, href)
                self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._text.append(data)
        if self._href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_heading and tag == f"h{self._level}":
            self._in_heading = False
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._anchor_text)).strip()
            self._links.append((self._href, text))
            self._href = None
            self._anchor_text = []

    def _flush(self) -> None:
        if not self._level:
            return
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        self.sections.append(_Section(self._level, text, list(self._links)))
        self._level = 0
        self._in_heading = False
        self._text = []
        self._links = []


def _strip_ebook_suffix(title: str) -> str:
    return re.sub(r"電子書\s*$", "", title).strip()


def _resolve_date(month: int, day: int, today: date) -> date | None:
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs((item - today).days))


def _deal_from_parts(month: int, day: int, href: str, raw_title: str, today: date, sale_week: tuple[int, int]) -> SaleEvent | None:
    event_date = _resolve_date(month, day, today)
    if event_date is None or event_date.isoformat() not in sale_week_dates(*sale_week):
        return None
    title = _strip_ebook_suffix(clean_title(raw_title))
    if "/ebook/" not in href or not title:
        return None
    return SaleEvent(
        date=event_date.isoformat(),
        title=title,
        book_url=href,
        source_url=HOME_URL,
        provisional=True,
    )


def _from_headings(html_text: str, today: date, sale_week: tuple[int, int]) -> SaleEvent | None:
    parser = _SectionParser(HOME_URL)
    parser.feed(html_text)
    parser.close()
    for index, section in enumerate(parser.sections):
        match = TODAY_RE.search(section.text)
        if not match:
            continue
        links = list(section.links)
        for follower in parser.sections[index + 1 :]:
            if follower.level <= section.level:
                break
            links.extend(follower.links)
        for href, raw_title in links:
            if "/ebook/" not in href:
                continue
            deal = _deal_from_parts(
                int(match.group("month")),
                int(match.group("day")),
                href,
                raw_title,
                today,
                sale_week,
            )
            if deal is not None:
                return deal
    return None


def _from_markup(html_text: str, today: date, sale_week: tuple[int, int]) -> SaleEvent | None:
    marker = TODAY_RE.search(html_text)
    if not marker:
        return None
    window = html_text[marker.end() : marker.end() + 12000]
    stop = NEXT_HEADING_RE.search(window)
    if stop:
        window = window[: stop.start()]
    anchor = ANCHOR_RE.search(window)
    if not anchor:
        return None
    raw_title = re.sub(r"<[^>]+>", " ", anchor.group(2))
    raw_title = re.sub(r"\s+", " ", html.unescape(raw_title)).strip()
    return _deal_from_parts(
        int(marker.group("month")),
        int(marker.group("day")),
        urljoin(HOME_URL, html.unescape(anchor.group(1))),
        raw_title,
        today,
        sale_week,
    )


def parse_today_deal(html_text: str, today: date, sale_week: tuple[int, int]) -> SaleEvent | None:
    return _from_headings(html_text, today, sale_week) or _from_markup(html_text, today, sale_week)


def fetch_today_deal(
    today: date,
    sale_week: tuple[int, int],
    fetch_mode: str,
    delay_seconds: float,
    browser_timeout_seconds: int,
) -> SaleEvent | None:
    print(f"Fetching homepage deal: {HOME_URL}")
    try:
        html_text = fetch_html(
            HOME_URL,
            fetch_mode=fetch_mode,
            delay_seconds=delay_seconds,
            browser_timeout_seconds=browser_timeout_seconds,
        )
    except Exception as exc:
        raise HomepageFetchError(str(exc)) from exc
    if not html_text:
        raise HomepageFetchError(f"Empty homepage response for {HOME_URL}")
    return parse_today_deal(html_text, today, sale_week)


def absorb_homepage_deal(
    events: list[SaleEvent],
    deal: SaleEvent | None,
    blog_url: str,
    sale_week: tuple[int, int],
) -> list[SaleEvent]:
    week_dates = sale_week_dates(*sale_week)
    has_saved_deal = any(event.provisional and event.date in week_dates for event in events)
    if deal is None:
        if not has_saved_deal:
            return events
        return [event for event in events if not (event.placeholder and event.source_url == blog_url)]
    kept = [
        event
        for event in events
        if not (event.placeholder and event.source_url == blog_url)
        and not (event.provisional and event.date == deal.date)
    ]
    return merge_events([*kept, deal])


def apply_homepage_fallback(
    events: list[SaleEvent],
    not_found: set[str],
    failed: set[str],
    now: datetime,
    fetch_mode: str,
    delay_seconds: float,
    browser_timeout_seconds: int,
) -> tuple[list[SaleEvent], bool]:
    pending = pending_sale_week(now)
    if pending is None:
        return events, True
    blog_url = week_url(*pending)
    has_real = any(
        event.source_url == blog_url and not event.placeholder and not event.provisional
        for event in events
    )
    if has_real or blog_url in failed or blog_url not in not_found:
        return events, True
    try:
        deal = fetch_today_deal(
            now.astimezone(TAIPEI).date(),
            pending,
            fetch_mode,
            delay_seconds,
            browser_timeout_seconds,
        )
    except HomepageFetchError as exc:
        print(f"Homepage fetch failed, keeping previous events: {exc}")
        return events, False
    if deal is None:
        print("Homepage has no 今日 99 block")
    else:
        print(f"Homepage deal: {deal.date} {deal.title}")
    return absorb_homepage_deal(events, deal, blog_url, pending), True
