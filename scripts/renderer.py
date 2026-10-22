from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from models import SaleEvent

TAIPEI = ZoneInfo("Asia/Taipei")


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


def event_description(event: SaleEvent) -> str:
    if event.placeholder:
        return "\n".join(
            [
                "本週 99 元書單尚未公布。",
                f"預期文章：{event.source_url}",
            ]
        )
    if event.provisional:
        return "\n".join(
            [
                f"書名：{event.title}",
                "來源：Kobo 首頁今日 99",
                f"頁面：{event.source_url}",
            ]
        )
    return "\n".join(
        [
            f"書名：{event.title}",
            f"來源文章：{event.source_url}",
        ]
    )


def event_link(event: SaleEvent) -> str:
    if event.placeholder:
        return event.source_url
    return event.book_url


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
        raw_lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.uid}",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
                f"SUMMARY:{escape_ics(event.title)}",
                f"DESCRIPTION:{escape_ics(event_description(event))}",
                f"URL:{escape_ics(event_link(event))}",
                "END:VEVENT",
            ]
        )

    raw_lines.append("END:VCALENDAR")
    folded = [part for line in raw_lines for part in fold_ics_line(line)]
    path.write_text("\r\n".join(folded) + "\r\n", encoding="utf-8")


def serialize_event(event: SaleEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "date": event.date,
        "title": event.title,
        "book_url": event.book_url,
        "source_url": event.source_url,
    }
    if event.placeholder:
        payload["placeholder"] = True
    if event.provisional:
        payload["provisional"] = True
    return payload


def write_json(events: list[SaleEvent], path: Path) -> None:
    path.write_text(
        json.dumps([serialize_event(event) for event in events], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_index(events: list[SaleEvent], path: Path) -> None:
    today_str = datetime.now(TAIPEI).date().isoformat()
    today_events = [event for event in events if event.date == today_str]
    rows = "\n".join(
        f"""          <tr>
            <td class="date-cell"><time datetime="{html.escape(event.date)}">{html.escape(event.date)}</time></td>
            <td class="title-cell"><a href="{html.escape(event_link(event))}" target="_blank">{html.escape(event.title)}</a></td>
          </tr>"""
        for event in today_events
    )
    generated = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    
    template_path = Path(__file__).parent / "templates" / "index_template.html"
    template = template_path.read_text(encoding="utf-8")
    
    rows_val = rows or '            <tr><td colspan="2" style="text-align:center; color:var(--text-muted);">今日暫無特價選書。</td></tr>'
    html_content = template.replace("<!-- ROWS -->", rows_val)
    html_content = html_content.replace("<!-- GENERATED_TIME -->", html.escape(generated))
    
    path.write_text(html_content, encoding="utf-8")
