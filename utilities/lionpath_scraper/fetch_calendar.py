#!/usr/bin/env python3
"""Pull no-class days from Penn State's academic calendar into data/academic-calendar.json.

The class search says when a section starts and ends but nothing about breaks, so a
calendar exported from it would run straight through Thanksgiving. The registrar
publishes those days per term, marked "No Classes"; this reads them and writes the
file the schedule builder turns into EXDATE exclusions.

    .venv/bin/python fetch_calendar.py             # terms found in data/*.csv
    .venv/bin/python fetch_calendar.py "Fall 2026" "Spring 2027"

Terms already in the file are kept; the ones fetched this run are replaced. A
term that turns up nothing keeps whatever dates it had. Re-run bundle_data.py
afterwards so the page picks the file up.
"""

from __future__ import annotations

import csv
import glob
import html
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

SOURCE = "https://www.registrar.psu.edu/academic-calendars/"
DATA = Path(__file__).parent / "data"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
TERM_RE = re.compile(r"^(Fall|Spring|Summer)\s+(\d{4})\b")
ITEM_RE = re.compile(r"(?is)<li[^>]*class=\"[^\"]*list-group-item[^\"]*\"[^>]*>(.*?)</li>")
HEADING_RE = re.compile(r"(?is)<h([234])[^>]*>(.*?)</h\1>")
DAY_RE = re.compile(r"(?:[A-Z][a-z]+day),?\s+([A-Z][a-z]+)\s+(\d{1,2})")


def academic_year(term: str) -> str:
    """'Fall 2026' -> '2026-27'; 'Spring 2027' and 'Summer 2027' -> '2026-27'."""
    match = TERM_RE.match(term)
    if not match:
        raise ValueError(f"unrecognised term {term!r}")
    season, year = match.group(1), int(match.group(2))
    start = year if season == "Fall" else year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"(?is)<[^>]+>", " ", fragment)).strip()


def parse_days(text: str, year: int) -> list[date]:
    """'Sunday, November 22 - Saturday, November 28' -> every date in that span."""
    found = DAY_RE.findall(text)
    if not found:
        return []
    points = []
    for month_name, day in found:
        month = MONTHS.get(month_name.lower())
        if month:
            points.append(date(year, month, int(day)))
    if not points:
        return []
    if len(points) == 1:
        return points
    start, end = min(points), max(points)
    if (end - start).days > 45:  # not a range; unrelated dates in one line
        return points
    span, cursor = [], start
    while cursor <= end:
        span.append(cursor)
        cursor += timedelta(days=1)
    return span


def scrape_year(session: requests.Session, year_slug: str) -> dict[str, list[dict]]:
    """{term: [{name, when, dates}]} for every 'No Classes' entry on one year's page."""
    url = f"{SOURCE}{year_slug}.cfm"
    response = session.get(url, timeout=60)
    response.raise_for_status()
    page = response.text

    # Walk headings and list items in document order so each item keeps its term.
    marks = [(m.start(), "h", strip_tags(m.group(2))) for m in HEADING_RE.finditer(page)]
    marks += [(m.start(), "li", m.group(1)) for m in ITEM_RE.finditer(page)]
    marks.sort(key=lambda m: m[0])

    out: dict[str, list[dict]] = {}
    current = None
    for _, kind, body in marks:
        if kind == "h":
            match = TERM_RE.match(body)
            if match:
                current = f"{match.group(1)} {match.group(2)}"
            continue
        if not current:
            continue
        heading = re.search(r"(?is)<h4[^>]*>(.*?)</h4>", body)
        detail = re.search(r"(?is)<p[^>]*>(.*?)</p>", body)
        if not heading or not detail:
            continue
        name = re.sub(r"\s+", " ", strip_tags(heading.group(1)))
        if "no classes" not in name.lower():
            continue
        when = re.sub(r"\s+", " ", strip_tags(detail.group(1)))
        year = int(TERM_RE.match(current).group(2))
        days = parse_days(when, year)
        if days:
            entry = {"name": re.sub(r"\s*-\s*No Classes$", "", name, flags=re.I),
                     "when": when,
                     "dates": [d.isoformat() for d in days]}
            # Each session repeats the same university holidays; list them once.
            events = out.setdefault(current, [])
            if not any(e["name"] == entry["name"] and e["when"] == entry["when"] for e in events):
                events.append(entry)
    return out


def terms_in_data() -> list[str]:
    terms = set()
    for path in glob.glob(str(DATA / "*.csv")):
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("term"):
                    terms.add(row["term"])
    return sorted(terms)


def load_terms(path: Path) -> dict[str, dict]:
    """Terms already in the calendar file, or {} if there is no usable file yet."""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise SystemExit(f"{path} is not valid JSON ({error}); fix or delete it first")
    return dict(existing.get("terms") or {})


def term_order(term: str) -> tuple:
    """Chronological: Spring, Summer, Fall within a year; unrecognised names last."""
    match = TERM_RE.match(term)
    if not match:
        return (1, 0, 0, term)
    season = {"Spring": 0, "Summer": 1, "Fall": 2}[match.group(1)]
    return (0, int(match.group(2)), season, term)


def main(argv: list[str]) -> int:
    terms = argv or terms_in_data()
    if not terms:
        print("no terms given and none found in data/*.csv", file=sys.stderr)
        return 1

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    wanted = {}
    for term in terms:
        wanted.setdefault(academic_year(term), []).append(term)

    scraped: dict[str, list[dict]] = {}
    for index, (year_slug, year_terms) in enumerate(sorted(wanted.items())):
        if index:
            time.sleep(1)
        print(f"reading {SOURCE}{year_slug}.cfm")
        found = scrape_year(session, year_slug)
        for term in year_terms:
            scraped[term] = found.get(term, [])

    # Merge into what is already there, so fetching one term never drops another.
    out = DATA / "academic-calendar.json"
    kept = load_terms(out)

    for term in terms:
        events = scraped.get(term, [])
        days = sorted({d for event in events for d in event["dates"]})
        print(f"\n{term}: {len(days)} no-class days")
        for event in events:
            print(f"    {event['name']}: {event['when']}")
            print(f"        {', '.join(event['dates'])}")
        if not events:
            print("    (nothing found -- check the term name against the registrar's page)")
            if kept.get(term, {}).get("events"):
                print("    keeping the dates already on file for this term")
                continue
        kept[term] = {"noClassDates": days, "events": events}

    payload = {
        "_readme": [
            "No-class days per term, used by schedule_builder.html to exclude breaks",
            "from the .ics export. Generated by fetch_calendar.py -- re-run that, then",
            "bundle_data.py. Every date is traceable to a named entry below.",
        ],
        "_source": SOURCE,
        "terms": {term: kept[term] for term in sorted(kept, key=term_order)},
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out} ({', '.join(payload['terms'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
