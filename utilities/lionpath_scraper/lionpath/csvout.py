"""Write ClassRow records to CSV."""

from __future__ import annotations

import csv
from pathlib import Path

from .parse import ClassRow

COLUMNS = [
    "term",
    "campus",
    "subject",
    "catalog_number",
    "section",
    "class_number",
    "course_title",
    "topic",
    "component",
    "career",
    "meeting_days",
    "meeting_times",
    "meeting_location",
    "instructor",
    "seats_available",
    "seats_total",
    "waitlist_available",
    "status",
    "academic_session",
    "meeting_dates",
    "notes",
    "detail_url",
    "retrieved_at",
    "related_to",
]

JOIN = " | "


def _sort_key(row: ClassRow):
    catalog = row.catalog_number
    digits = "".join(c for c in catalog if c.isdigit())
    return (row.subject, int(digits) if digits else 0, catalog, row.campus, row.section)


def to_record(row: ClassRow) -> dict[str, str]:
    return {
        "term": row.term,
        "campus": row.campus,
        "subject": row.subject,
        "catalog_number": row.catalog_number,
        "section": row.section,
        "class_number": row.class_number,
        "course_title": row.course_title,
        "topic": row.topic,
        # The code (LEC/LAB/...) rather than the detail page's "Lecture", so the
        # column reads the same with and without --instructors.
        "component": row.component,
        "career": row.career,
        "meeting_days": JOIN.join(m.days for m in row.meetings),
        "meeting_times": JOIN.join(m.times for m in row.meetings),
        "meeting_location": JOIN.join(m.location for m in row.meetings),
        "instructor": row.instructor,
        "seats_available": row.seats_available,
        "seats_total": row.seats_total,
        "waitlist_available": "Y" if row.waitlist_available else "",
        "status": row.status,
        "academic_session": row.academic_session,
        "meeting_dates": row.meeting_dates,
        "notes": JOIN.join(row.notes),
        "detail_url": row.detail_url,
        "retrieved_at": row.retrieved_at,
        "related_to": row.related_to,
    }


def write_csv(rows: list[ClassRow], path: str | Path) -> int:
    ordered = sorted(rows, key=_sort_key)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in ordered:
            writer.writerow(to_record(row))
    return len(ordered)
