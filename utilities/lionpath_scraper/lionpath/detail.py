"""Instructor enrichment via each course's detail page.

The search results list omits the instructor entirely. Every result row links to
a stateless course-detail page that carries it -- along with meeting dates,
academic session, and class status -- for *every* section of that course across
all campuses. So this costs one fetch per course, not per section.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field, replace
from datetime import datetime

from .parse import (
    ClassRow,
    DetailSection,
    Meeting,
    compact_days,
    compact_times,
    component_code,
    detail_sections,
)
from .session import LionPathSession

log = logging.getLogger(__name__)


class DetailFetcher:
    """Fetches and caches course-detail pages."""

    def __init__(self, session: LionPathSession):
        self.session = session
        self._cache: dict[tuple[str, ...], dict[str, DetailSection]] = {}
        self.pages_fetched = 0

    def _course_key(self, row: ClassRow) -> tuple[str, ...] | None:
        # Offer number is deliberately not part of the key: every offering of a
        # course returns the same page. A miss is caught by the refetch below.
        if not (row.crse_id and row.strm):
            return None
        return (row.strm, row.crse_id, row.career, row.institution)

    def _fetch(self, row: ClassRow) -> dict[str, DetailSection]:
        params = {
            "Page": "SSR_CRSE_INFO_FL",
            "Action": "U",
            "ACAD_CAREER": row.career,
            "CRSE_ID": row.crse_id,
            "CRSE_OFFER_NBR": row.crse_offer_nbr,
            "INSTITUTION": row.institution,
            "STRM": row.strm,
            "CLASS_NBR": row.class_number,
            "SSR_COMPONENT": row.component,
        }
        page = self.session.get_detail(params)
        self.pages_fetched += 1
        return detail_sections(page)

    def sections_for(self, row: ClassRow) -> dict[str, DetailSection]:
        key = self._course_key(row)
        if key is None:
            return {}

        known = self._cache.get(key)
        if known is None:
            known = self._cache[key] = self._fetch(row)
        if row.class_number in known:
            return known

        # The page normally lists every section of the course, but not always.
        # Re-ask for this specific class and merge, so later rows still benefit.
        known.update(self._fetch(row))
        return known


@dataclass
class EnrichResult:
    matched: int = 0
    related: list[ClassRow] = field(default_factory=list)


def _related_row(parent: ClassRow, section: DetailSection, stamp: str) -> ClassRow:
    """Turn a recitation or lab into a row of its own.

    Identity comes from the class you enrol in -- the detail page never says which
    campus or term a component belongs to, but the lecture it is paired with does.
    The times are rewritten into the form the results list uses, so downstream
    readers cannot tell a related row from a scraped one.
    """
    meeting = Meeting(
        days=compact_days(section.days),
        times=compact_times(section.times),
        location=section.room,
    )
    return replace(
        parent,
        section=section.section or "",
        class_number=section.class_number,
        component=component_code(section.component_description),
        component_description=section.component_description,
        instructor=section.instructor,
        status=section.status,
        academic_session=section.academic_session,
        meeting_dates=section.meeting_dates,
        seats_available=section.seats_available,
        seats_total=section.seats_total,
        waitlist_available=False,
        meetings=[meeting] if (meeting.days or meeting.times or meeting.location) else [],
        notes=[],
        retrieved_at=stamp,
        related_to=parent.class_number,
    )


def enrich(session: LionPathSession, rows: list[ClassRow], report=None,
           include_related: bool = False) -> EnrichResult:
    """Fill instructor/status/session/dates on `rows` in place.

    With `include_related`, also returns a row for every recitation or lab paired
    with one of them, which the class search itself never lists separately.
    """
    fetcher = DetailFetcher(session)
    matched = 0
    extras: dict[tuple[str, str], ClassRow] = {}
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")

    if report:
        report("enrich_start", total=len(rows))
    for position, row in enumerate(rows, start=1):
        try:
            sections = fetcher.sections_for(row)
        except Exception as error:  # one bad course shouldn't sink the whole run
            log.warning("detail lookup failed for %s %s: %s", row.subject, row.catalog_number, error)
            continue

        section = sections.get(row.class_number)
        if section is None:
            continue

        matched += 1
        row.instructor = section.instructor
        row.status = section.status
        row.academic_session = section.academic_session
        row.meeting_dates = section.meeting_dates
        row.component_description = section.component_description
        # Closed and wait-listed classes have no seat line in the results list,
        # but the detail grid reports seats for every status.
        if not row.seats_total:
            row.seats_available = section.seats_available
            row.seats_total = section.seats_total

        if report:
            report("enrich", done=position, total=len(rows), pages=fetcher.pages_fetched)
        if include_related:
            for number in section.related:
                other = sections.get(number)
                if other is None or other.class_number == row.class_number:
                    continue
                key = (row.term, other.class_number)
                if key not in extras:
                    extras[key] = _related_row(row, other, stamp)

        if position % 100 == 0:
            log.info("enriched %d/%d rows (%d course pages fetched)",
                     position, len(rows), fetcher.pages_fetched)

    log.info("instructor pass: %d/%d rows matched from %d course pages",
             matched, len(rows), fetcher.pages_fetched)
    if include_related:
        log.info("found %d related components (recitations, labs) on those courses", len(extras))
    if matched < len(rows):
        # The course-detail page renders at most 50 option rows even when it says
        # a course has more, and it shows the same 50 whichever class you ask for.
        # Sections below that cut simply have no instructor to read.
        log.warning(
            "%d of %d rows have no instructor: their course lists more options than "
            "LionPath's course page will show, and it only ever renders the first 50",
            len(rows) - matched, len(rows),
        )
    return EnrichResult(matched=matched, related=list(extras.values()))
