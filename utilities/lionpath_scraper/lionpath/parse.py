"""Parsers for LionPath (PeopleSoft Fluid Search) HTML pages.

Everything here is a pure function over already-fetched HTML; nothing performs I/O.
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# The server truncates any single search at this many rows. A result reporting
# this total is incomplete and has to be subdivided -- see search.partition().
RESULT_CAP = 401

_GROUP_ROW_RE = re.compile(r"^win0divPTS_FACETS_row\$(\d+)$")
_FACET_LABEL_RE = re.compile(r"^PTS_SELECT_LBL\$(\d+)$")
_BREADCRUMB_RE = re.compile(r"^PTS_BREADCRUMB_PTS_IMG\$\d+$")
_RESULT_ROW_RE = re.compile(r"^PTS_RSLTS_LIST\$0_row_\d+$")
_TITLE_DIV_RE = re.compile(r"^win0divPTS_LIST_TITLE\$\d+$")
_SUMMARY_DIV_RE = re.compile(r"^win0divPTS_LIST_SUMMARY\$\d+$")
_DETAIL_ROW_RE = re.compile(r"^SSR_CLS_DTLS_VW\$\d+_row_\d+$")

_COUNT_SUFFIX_RE = re.compile(r"^(.*?)\s*\((\d+)\)$")
_SHOW_DETAILS_RE = re.compile(r"showClassDetails\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_WINDOW_LOCATION_RE = re.compile(r"window\.location\s*=\s*'([^']+)'")
_ROW_COUNT_RE = re.compile(r"([\d,]+)\s+rows?")
_SEATS_RE = re.compile(r"(\d+)\s+of\s+(\d+)")
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)\s*$", re.I
)

# Courses whose title is a container for a per-section topic: the catalog title
# is fixed ("Special Topics") and each section appends its own subject after a
# colon. Matched against the part before the colon, so a genuine title that
# happens to contain one ("Artificial Intelligence: Automated Thinking...")
# is left alone.
TOPIC_HEAD_RE = re.compile(
    r"^(?:special|selected)\s+topics?\b"
    r"|^independent\s+stud(?:y|ies)\b"
    r"|^special\s+stud(?:y|ies)\b"
    r"|^research\s+topics?\b",
    re.I,
)

_SEATS_PREFIX = "Available Seats:"
_WAITLIST_TEXT = "Wait list option available"
_NOTE_PREFIXES = ("This is a combined section class:", "Click Enroll")


class Page:
    """A fetched page plus its lazily parsed soup."""

    def __init__(self, html: str, url: str = ""):
        self.html = html
        self.url = url
        self._soup: BeautifulSoup | None = None

    @property
    def soup(self) -> BeautifulSoup:
        if self._soup is None:
            self._soup = BeautifulSoup(self.html, "lxml")
        return self._soup

    @property
    def has_form(self) -> bool:
        """True if this looks like a live PeopleSoft page rather than an error/expiry stub."""
        return "name='win0'" in self.html or 'name="win0"' in self.html


def label_matches(label: str, wanted: str) -> bool:
    """Match a user-supplied value against a site label.

    Accepts the label as displayed, the label without its "(count)" suffix, or --
    for subjects, which render as "CMPSC / Computer Science" -- either half.
    """
    match = _COUNT_SUFFIX_RE.match(label)
    base = match.group(1) if match else label
    candidates = {label.casefold(), base.casefold()}
    if " / " in base:
        code, _, description = base.partition(" / ")
        candidates.add(code.strip().casefold())
        candidates.add(description.strip().casefold())
    return wanted.strip().casefold() in candidates


@dataclass(frozen=True)
class FacetValue:
    group: str
    group_index: str
    index: str
    label: str  # as displayed, e.g. "Undergraduate (15788)" or "CMPSC / Computer Science"
    base: str  # label with any trailing "(count)" stripped
    count: int | None

    def matches(self, wanted: str) -> bool:
        return label_matches(self.label, wanted)


@dataclass
class FacetGroup:
    name: str
    index: str
    values: list[FacetValue] = field(default_factory=list)

    def find(self, wanted: str) -> FacetValue | None:
        for value in self.values:
            if value.matches(wanted):
                return value
        return None


@dataclass
class Meeting:
    days: str = ""
    times: str = ""
    location: str = ""


@dataclass
class ClassRow:
    """One class section, as gathered from the search results list."""

    term: str = ""
    strm: str = ""
    campus: str = ""
    subject: str = ""
    catalog_number: str = ""
    section: str = ""
    class_number: str = ""
    subject_description: str = ""
    course_title: str = ""
    topic: str = ""       # per-section subject of a Special Topics course
    base_title: str = ""  # course_title with any per-section topic removed
    meetings: list[Meeting] = field(default_factory=list)
    seats_available: str = ""
    seats_total: str = ""
    waitlist_available: bool = False
    notes: list[str] = field(default_factory=list)

    # Filled in only by the --instructors enrichment pass.
    instructor: str = ""
    status: str = ""
    academic_session: str = ""
    meeting_dates: str = ""
    component_description: str = ""
    retrieved_at: str = ""  # ISO timestamp of the scrape that produced this row
    related_to: str = ""    # for a recitation or lab: the class number you enrol in

    # Parameters for the course-detail page this section belongs to.
    career: str = ""
    crse_id: str = ""
    crse_offer_nbr: str = ""
    institution: str = ""
    component: str = ""
    detail_url: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.strm, self.class_number)


@dataclass
class DetailSection:
    """One row of a course-detail page's section grid."""

    class_number: str = ""
    section: str = ""
    component_description: str = ""
    retrieved_at: str = ""  # ISO timestamp of the scrape that produced this row
    related_to: str = ""    # for a recitation or lab: the class number you enrol in
    status: str = ""
    academic_session: str = ""
    meeting_dates: str = ""
    days: str = ""
    times: str = ""
    room: str = ""
    instructor: str = ""
    seats_raw: str = ""
    seats_available: str = ""
    seats_total: str = ""
    related: list[str] = field(default_factory=list)  # other components in the same option row


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def _br_lines(node) -> list[str]:
    """Split an element's inner HTML on <br> into cleaned text lines."""
    inner = node.decode_contents()
    inner = re.sub(r"(?s)<!--.*?-->", "", inner)
    lines = []
    for part in re.split(r"(?i)<br\s*/?>", inner):
        text = _clean(re.sub(r"(?s)<[^>]+>", " ", part))
        if text:
            lines.append(text)
    return lines


def hidden_fields(page: Page) -> dict[str, str]:
    """Every named hidden input on the page -- these must be echoed back on each postback."""
    fields = {}
    for node in page.soup.find_all("input"):
        if (node.get("type") or "").lower() != "hidden":
            continue
        name = node.get("name")
        if name:
            fields[name] = node.get("value") or ""
    return fields


def current_term(page: Page) -> str:
    select = page.soup.find("select", id="PE_SR175_DRV_DESCR")
    if not select:
        return ""
    chosen = select.find("option", selected=True) or select.find("option")
    return _clean(chosen.get("value") or chosen.get_text()) if chosen else ""


def available_terms(page: Page) -> list[str]:
    select = page.soup.find("select", id="PE_SR175_DRV_DESCR")
    if not select:
        return []
    return [_clean(o.get("value") or o.get_text()) for o in select.find_all("option")]


def facet_groups(page: Page) -> dict[str, FacetGroup]:
    """Map group name -> FacetGroup.

    PTS_SELECT indices form one flat sequence across every group and shift after
    each postback, so this has to be re-read from the current page every time.
    """
    groups: dict[str, FacetGroup] = {}
    for div in page.soup.find_all("div", id=_GROUP_ROW_RE):
        group_index = _GROUP_ROW_RE.match(div["id"]).group(1)
        header = div.find("a", id=f"PTS_FACET_PTS_GROUP_BOX${group_index}")
        name = _clean(header.get_text()) if header else f"Group {group_index}"
        group = FacetGroup(name=name, index=group_index)
        for label_node in div.find_all("label", id=_FACET_LABEL_RE):
            index = _FACET_LABEL_RE.match(label_node["id"]).group(1)
            label = _clean(label_node.get_text())
            match = _COUNT_SUFFIX_RE.match(label)
            base, count = (match.group(1), int(match.group(2))) if match else (label, None)
            group.values.append(
                FacetValue(
                    group=name,
                    group_index=group_index,
                    index=index,
                    label=label,
                    base=base,
                    count=count,
                )
            )
        groups[name] = group
    return groups


def breadcrumbs(page: Page) -> dict[str, str]:
    """Applied filters -> the control id that removes them."""
    found = {}
    for node in page.soup.find_all("a", id=_BREADCRUMB_RE):
        label = node.get("aria-label") or ""
        match = re.match(r"^Remove (.*) filter$", _clean(label))
        if match:
            found[match.group(1)] = node["id"]
    return found


def row_count(page: Page) -> int | None:
    """Total rows the server says the current search has, or None if not rendered."""
    node = page.soup.find("div", id="win0divPTS_RSLTS_LISTrowcnt$0")
    if not node:
        return None
    match = _ROW_COUNT_RE.search(_clean(node.get_text()))
    return int(match.group(1).replace(",", "")) if match else None


def _parse_title(text: str) -> tuple[str, str, str, str]:
    """"A-I 100 - 001 Lehigh Valley" -> (subject, catalog_number, section, campus)."""
    left, sep, right = text.partition(" - ")
    if not sep:
        return "", "", "", ""
    subject, _, catalog = left.strip().rpartition(" ")
    section, _, campus = right.strip().partition(" ")
    return subject.strip(), catalog.strip(), section.strip(), campus.strip()


def _parse_meeting(line: str) -> Meeting:
    """"M W F 9:05 AM-9:55 AM - Boucke Bldg 214" -> days / times / location."""
    schedule, sep, location = line.rpartition(" - ")
    if not sep:
        schedule, location = line, ""
    schedule, location = schedule.strip(), location.strip()
    match = _TIME_RANGE_RE.search(schedule)
    if match:
        days = schedule[: match.start()].strip()
        times = f"{match.group(1)}-{match.group(2)}"
    else:
        days, times = schedule, ""
    return Meeting(days=days, times=times, location=location)


def _parse_summary(lines: list[str], row: ClassRow) -> None:
    """Fold a result row's <br>-separated summary block into `row`.

    Line shapes vary: closed classes omit the "Available Seats" line entirely,
    a class may have several meeting patterns, and notes can appear anywhere.
    So each line is classified rather than read positionally.
    """
    if not lines:
        return
    head = lines[0].split(" - ", 2)
    row.class_number = head[0].strip() if head else ""
    if len(head) > 1:
        row.subject_description = head[1].strip()
    if len(head) > 2:
        row.course_title = head[2].strip()

    for line in lines[1:]:
        if line.startswith(_SEATS_PREFIX):
            if line.endswith(_WAITLIST_TEXT):
                row.waitlist_available = True
                line = line[: -len(_WAITLIST_TEXT)].rstrip().rstrip("-").rstrip()
            match = _SEATS_RE.search(line)
            if match:
                row.seats_available, row.seats_total = match.group(1), match.group(2)
        elif line == _WAITLIST_TEXT:
            row.waitlist_available = True
        elif line.startswith(_NOTE_PREFIXES):
            row.notes.append(line)
        else:
            row.meetings.append(_parse_meeting(line))


def result_rows(page: Page, term: str = "") -> list[ClassRow]:
    """Every class section currently rendered in the results list."""
    rows = []
    for item in page.soup.find_all("li", id=_RESULT_ROW_RE):
        row = ClassRow(term=term)

        title_div = item.find("div", id=_TITLE_DIV_RE)
        anchor = title_div.find("a") if title_div else None
        if anchor is None:
            continue
        row.subject, row.catalog_number, row.section, row.campus = _parse_title(
            _clean(anchor.get_text())
        )
        details = _SHOW_DETAILS_RE.search(anchor.get("href") or "")
        if details:
            row.strm, row.class_number = details.group(1), details.group(2)

        summary_div = item.find("div", id=_SUMMARY_DIV_RE)
        if summary_div is not None:
            area = summary_div.find("div", class_="ps-htmlarea") or summary_div
            _parse_summary(_br_lines(area), row)

        enroll = _WINDOW_LOCATION_RE.search(str(item))
        if enroll:
            row.detail_url = html_mod.unescape(enroll.group(1))
            params = dict(re.findall(r"[?&]([A-Z_]+)=([^&']*)", row.detail_url))
            row.career = params.get("ACAD_CAREER", "")
            row.crse_id = params.get("CRSE_ID", "")
            row.crse_offer_nbr = params.get("CRSE_OFFER_NBR", "")
            row.institution = params.get("INSTITUTION", "")
            row.component = params.get("SSR_COMPONENT", "")
            row.strm = row.strm or params.get("STRM", "")

        rows.append(row)
    return rows


def _component_values(row, field: str) -> dict[int, list[str]]:
    """Values for one detail field, keyed by component number.

    An option row can bundle several components -- a lecture and its recitation
    -- and PeopleSoft numbers them in the element id: SSR_..._INSTR_LONG_1 holds
    the lecture's instructor, _2 the recitation's. Reading the cell as one blob
    instead runs them together.
    """
    pattern = re.compile(r"^SSR_CLSRCH_F_WK_SSR_" + field + r"_(\d+)\$")
    found: dict[int, list[str]] = {}
    for node in row.find_all(id=pattern):
        index = int(pattern.match(node["id"]).group(1))
        if index in found:
            continue  # a wrapper and its anchor share the id prefix
        lines = _br_lines(node)
        if lines:
            found[index] = lines
    return found


def detail_sections(page: Page) -> dict[str, DetailSection]:
    """Parse a course-detail page's grid into {class_number: DetailSection}.

    One entry per component, not per option row, so a lecture bundled with a
    recitation is reachable by either class number and each keeps its own
    instructor, room and seats.
    """
    sections: dict[str, DetailSection] = {}

    for row in page.soup.find_all("tr", id=_DETAIL_ROW_RE):
        status = ""
        status_node = row.find(id=re.compile(r"^SSR_DER_CS_GRP_SSR_DESCR\$"))
        if status_node is not None:
            status = _clean(status_node.get_text(" "))
        session_node = row.find(id=re.compile(r"^SSR_DER_CS_GRP_SESSION_CODE"))
        academic_session = _clean(session_node.get_text(" ")) if session_node is not None else ""

        labels = _component_values(row, "CMPNT_DESCR")
        instructors = _component_values(row, "INSTR_LONG")
        rooms = _component_values(row, "MTG_LOC_LONG")
        schedules = _component_values(row, "MTG_SCHED_L")
        dates = _component_values(row, "MTG_DT_LONG")
        seats = _component_values(row, "DESCR50")
        in_row: list[str] = []

        for index in sorted(labels):
            label = " ".join(labels[index])
            number = re.search(r"Class\s+(\d+)\s*$", label)
            if not number:
                continue
            section = DetailSection(
                class_number=number.group(1),
                status=status,
                academic_session=academic_session,
                meeting_dates=" ".join(dates.get(index, [])),
                room=" ".join(rooms.get(index, [])),
                instructor=normalize_instructors(instructors.get(index, [])),
                seats_raw=" ".join(seats.get(index, [])),
            )

            titled = re.match(r"Section\s+(\S+?)-(.*?)\s+Class\s+\d+\s*$", label)
            if titled:
                section.section = titled.group(1)
                section.component_description = titled.group(2).strip()

            when = schedules.get(index, [])
            section.days = when[0] if when else ""
            section.times = " ".join(when[1:]) if len(when) > 1 else ""

            measured = _SEATS_RE.search(section.seats_raw)
            if measured:
                section.seats_available, section.seats_total = measured.group(1), measured.group(2)

            # A class appears once per option row it belongs to -- keep the first
            # reading of its values and let the pairings accumulate across rows.
            sections.setdefault(section.class_number, section)
            in_row.append(section.class_number)

        # Components sharing an option row are the choices you pick together.
        for number in in_row:
            for other in in_row:
                if other != number and other not in sections[number].related:
                    sections[number].related.append(other)

    return sections


def split_topic(title: str) -> tuple[str, str]:
    """Split a variable-title course into (catalog title, this section's topic).

    "Special Topics: Math Lab" -> ("Special Topics", "Math Lab")
    "Artificial Intelligence: Automated Thinking" -> unchanged, no topic
    """
    head, separator, tail = title.partition(": ")
    if not separator or not tail.strip():
        return title, ""
    if TOPIC_HEAD_RE.search(head.strip()):
        return head.strip(), tail.strip()
    return title, ""


def annotate_topics(rows: list[ClassRow]) -> list[ClassRow]:
    """Fill in `topic` and `base_title` for every row, in place.

    Two rules, because neither alone is enough. The vocabulary rule catches a
    Special Topics course even when only one section is offered; the variance
    rule catches any course whose sections simply disagree about their title,
    whatever it is called.
    """
    by_course: dict[tuple[str, str, str], list[ClassRow]] = {}
    for row in rows:
        by_course.setdefault((row.term, row.subject, row.catalog_number), []).append(row)

    for members in by_course.values():
        varies = len({m.course_title for m in members}) > 1
        for row in members:
            base, topic = split_topic(row.course_title)
            if not topic and varies and ": " in row.course_title:
                head, _, tail = row.course_title.partition(": ")
                base, topic = head.strip(), tail.strip()
            row.topic = topic
            row.base_title = base if topic else row.course_title
    return rows


def normalize_instructors(parts: list[str]) -> str:
    """Tidy the instructor cell into one comma-separated list.

    LionPath repeats the whole name once per meeting pattern, so a class that
    meets in person and online lists its instructor twice; co-taught sections
    are separated by a comma, a line break, or both. Split on every separator,
    then keep first appearances in order.
    """
    names, seen = [], set()
    for part in parts:
        for name in part.split(","):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return ", ".join(names)


_DAY_NAMES = {
    "monday": "M", "tuesday": "T", "wednesday": "W", "thursday": "Th",
    "friday": "F", "saturday": "Sa", "sunday": "Su",
}
_DETAIL_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2})\s*([AP]M)\s*to\s*(\d{1,2}:\d{2})\s*([AP]M)", re.I
)
_COMPONENT_CODES = {
    "lecture": "LEC", "recitation": "REC", "laboratory": "LAB", "lab": "LAB",
    "discussion": "DIS", "activity": "ACT", "seminar": "SEM", "studio": "STU",
    "field studies": "FLD", "independent study": "IND", "research": "RSC",
    "examination": "EXM", "practicum": "PRC", "internship": "INT",
}


def compact_days(text: str) -> str:
    """"Monday Wednesday Friday" -> "M W F", the form the results list uses."""
    words = re.findall(r"[A-Za-z]+", text or "")
    return " ".join(_DAY_NAMES[w.lower()] for w in words if w.lower() in _DAY_NAMES)


def compact_times(text: str) -> str:
    """"9:05AM to 9:55AM" -> "9:05 AM-9:55 AM"."""
    match = _DETAIL_TIME_RE.search(text or "")
    if not match:
        return ""
    return (f"{match.group(1)} {match.group(2).upper()}"
            f"-{match.group(3)} {match.group(4).upper()}")


def component_code(description: str) -> str:
    """"Recitation" -> "REC", to match the codes the results list carries."""
    key = (description or "").strip().lower()
    if key in _COMPONENT_CODES:
        return _COMPONENT_CODES[key]
    return re.sub(r"[^A-Z]", "", (description or "").upper())[:3]
