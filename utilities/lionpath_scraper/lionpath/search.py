"""Drive the class search: apply filters, paginate, and subdivide capped searches."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

from .parse import (
    RESULT_CAP,
    ClassRow,
    Page,
    annotate_topics,
    available_terms,
    breadcrumbs,
    current_term,
    facet_groups,
    label_matches,
    result_rows,
    row_count,
)
from .query import FACET_FIELDS, PARTITION_ORDER, Query
from .session import LionPathSession

log = logging.getLogger(__name__)

# Applied for you by the site on every fresh page load.
DEFAULT_FILTER = "Open Classes Only"
KEYWORD_FIELD = "PTS_FILTER_NOOP_PTS_FILTER_VALUE$0"
PAGE_DOWN_ACTION = "PTS_RSLTS_LIST$hdown$0"
MAX_PAGE_DOWNS = 200


class UnknownFacetValue(ValueError):
    pass


class FiltersNotApplied(RuntimeError):
    """The server accepted the filters but did not actually apply them."""


@dataclass
class SearchResult:
    rows: list[ClassRow] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    searches_run: int = 0

    def merge(self, other: "SearchResult") -> None:
        self.rows.extend(other.rows)
        self.truncated.extend(other.truncated)
        self.searches_run += other.searches_run


def _normalized(query: Query) -> Query:
    """Fold the open_only convenience flag into the Class Status facet."""
    if query.open_only and not any(s.casefold() == DEFAULT_FILTER.casefold() for s in query.status):
        return replace(query, status=[*query.status, DEFAULT_FILTER])
    return query


def _find_facet(session: LionPathSession, page: Page, group_name: str, value: str):
    """Locate a facet value, expanding the group once if it isn't rendered yet."""
    groups = facet_groups(page)
    group = groups.get(group_name)
    if group is None:
        raise UnknownFacetValue(
            f"no {group_name!r} filter on this page (have: {', '.join(sorted(groups))})"
        )
    found = group.find(value)
    if found is not None:
        return page, found

    # All values are normally present in the DOM with More/Less handled client
    # side, but expand explicitly before giving up in case that ever changes.
    if page.soup.find("a", id=f"PTS_MORE${group.index}"):
        page = session.action(page, f"PTS_MORE${group.index}")
        group = facet_groups(page).get(group_name)
        found = group.find(value) if group else None
        if found is not None:
            return page, found

    options = ", ".join(v.base for v in group.values[:12])
    raise UnknownFacetValue(f"{group_name} has no value {value!r} (e.g. {options}, ...)")


def _filter_mismatch(page: Page, query: Query) -> list[str]:
    """Differences between the filters we asked for and the ones actually applied.

    Checks both directions. A missing filter means a click did not register; an
    unexpected one means state leaked in from a previous search, which would
    silently narrow or widen the results.
    """
    applied = list(breadcrumbs(page))
    wanted = [value for _, value in query.selections()]
    problems = [
        f"missing {group}={value}"
        for group, value in query.selections()
        if not any(label_matches(label, value) for label in applied)
    ]
    problems += [
        f"unexpected {label!r}"
        for label in applied
        if not any(label_matches(label, value) for value in wanted)
    ]
    return problems


def apply_query(session: LionPathSession, query: Query, retry: bool = True) -> Page:
    """Open a fresh search and apply every filter in `query`.

    Always starts a new session: re-entering the component restores whatever
    search you ran last, and facets are multi-select, so filters from a previous
    query would stack onto this one instead of replacing it.
    """
    query = _normalized(query)
    session.reset()
    page = session.open_search()

    if query.keywords:
        page = session.action(page, "PTS_SRCH_BTN", {KEYWORD_FIELD: query.keywords})

    if query.term:
        terms = available_terms(page)
        match = next((t for t in terms if t.casefold() == query.term.casefold()), None)
        if match is None:
            raise ValueError(f"term {query.term!r} not offered (available: {', '.join(terms)})")
        if match != current_term(page):
            page = session.action(page, "PE_SR175_DRV_DESCR", {"PE_SR175_DRV_DESCR": match})

    wants_default = any(s.casefold() == DEFAULT_FILTER.casefold() for s in query.status)
    applied = breadcrumbs(page)
    if DEFAULT_FILTER in applied and not wants_default:
        page = session.action(page, applied[DEFAULT_FILTER])

    for group_name, value in query.selections():
        # Already applied by default -- firing it again would toggle it off.
        if group_name == "Class Status" and value.casefold() == DEFAULT_FILTER.casefold():
            if DEFAULT_FILTER in breadcrumbs(page):
                continue
        page, facet = _find_facet(session, page, group_name, value)
        page = session.action(page, f"PTS_SELECT${facet.index}", {f"PTS_SELECT${facet.index}": "Y"})

    problems = _filter_mismatch(page, query)
    if problems:
        if not retry:
            raise FiltersNotApplied(f"{query.describe()}: {', '.join(problems)}")
        log.warning("filters did not apply cleanly (%s); retrying", ", ".join(problems))
        return apply_query(session, query, retry=False)

    return page


def collect(session: LionPathSession, page: Page, report=None) -> list[ClassRow]:
    """Page through the results list until every row is rendered."""
    term = current_term(page)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    total = row_count(page)
    previous = -1
    for _ in range(MAX_PAGE_DOWNS):
        rows = result_rows(page, term=term)
        if total is not None and len(rows) >= total:
            return _stamp(rows, stamp)
        if len(rows) == previous:
            return _stamp(rows, stamp)
        previous = len(rows)
        if report:
            report("page", loaded=len(rows), total=total)
        page = session.action(page, PAGE_DOWN_ACTION)
    log.warning("stopped paging after %d requests; results may be incomplete", MAX_PAGE_DOWNS)
    return _stamp(result_rows(page, term=term), stamp)


def _stamp(rows: list[ClassRow], when: str) -> list[ClassRow]:
    for row in rows:
        row.retrieved_at = when
    return rows


@dataclass
class _Split:
    """One candidate way to subdivide an over-capped search."""

    dimension: str
    values: list[str]
    worst_slice: int | None  # largest slice size, when the facet reports counts


def _partition_options(page: Page, query: Query) -> list[_Split]:
    """Every dimension this query could still be split along."""
    groups = facet_groups(page)
    options = []
    for dimension in PARTITION_ORDER:
        if len(getattr(query, dimension)) == 1:  # already pinned to one value
            continue

        # If the caller listed values, split across exactly those -- enumerating
        # the facet instead would widen the search past what was asked for.
        chosen = getattr(query, dimension)
        if chosen:
            options.append(_Split(dimension, list(chosen), None))
            continue

        group = groups.get(FACET_FIELDS[dimension])
        if group is None:
            continue
        values = [value for value in group.values if value.count != 0]
        if not values:
            continue
        counts = [value.count for value in values if value.count is not None]
        worst = max(counts) if len(counts) == len(values) else None
        options.append(_Split(dimension, [v.base for v in values], worst))
    return options


def _choose_split(options: list[_Split]) -> _Split | None:
    """Prefer a split whose facet counts prove every slice clears the cap.

    Those dimensions (course level, career) usually need far fewer sub-queries
    than campus or subject. Counts are only a hint -- each slice is re-checked
    against the cap after it runs, so a bad estimate just recurses again.
    """
    if not options:
        return None
    proven = [o for o in options if o.worst_slice is not None and o.worst_slice < RESULT_CAP]
    if proven:
        return min(proven, key=lambda o: len(o.values))
    return options[0]


def run_query(session: LionPathSession, query: Query, report=None) -> SearchResult:
    """Run one query, subdividing automatically if the server truncates it."""
    result = _run_query(session, query, report)
    annotate_topics(result.rows)
    return result


def _run_query(session: LionPathSession, query: Query, report=None) -> SearchResult:
    if report:
        report("search", query=query.describe())
    page = apply_query(session, query)
    total = row_count(page)
    result = SearchResult(searches_run=1)

    if total is None or total < RESULT_CAP:
        result.rows = collect(session, page, report)
        log.info("%-60s %4d rows", query.describe(), len(result.rows))
        if report:
            report("slice", query=query.describe(), rows=len(result.rows))
        return result

    split = _choose_split(_partition_options(page, query))
    if split is None:
        log.warning(
            "%s is capped at %d rows and cannot be subdivided further; results are incomplete",
            query.describe(), total,
        )
        result.rows = collect(session, page, report)
        result.truncated.append(query.describe())
        return result

    log.info(
        "%-60s capped at %d; splitting across %d %s values",
        query.describe(), total, len(split.values), split.dimension,
    )
    if report:
        report("split", dimension=split.dimension, total=len(split.values))
    for position, value in enumerate(split.values, start=1):
        log.debug("  [%d/%d] %s = %s", position, len(split.values), split.dimension, value)
        if report:
            report("slice_start", index=position, total=len(split.values),
                   dimension=split.dimension, value=value)
        try:
            result.merge(_run_query(session, query.narrowed(split.dimension, value), report))
        except UnknownFacetValue:
            # No classes at this intersection, so the site stops offering the value.
            log.debug("  no %s=%s classes in this slice", split.dimension, value)
            result.searches_run += 1
    result.rows = _deduplicate(result.rows)
    return result


def _deduplicate(rows: list[ClassRow]) -> list[ClassRow]:
    unique: dict[tuple[str, str], ClassRow] = {}
    for row in rows:
        unique.setdefault(row.key, row)
    return list(unique.values())


def run_queries(session: LionPathSession, queries: list[Query], report=None) -> SearchResult:
    """Run several queries and merge them, de-duplicating on (term, class number).

    `report(kind, **data)`, if given, is called at each step so a caller can show
    progress; the library itself never writes to the terminal.
    """
    combined = SearchResult()
    for query in queries:
        combined.merge(_run_query(session, query, report))
    combined.rows = _deduplicate(combined.rows)
    # Topics are decided across the whole result set, so do it once at the end:
    # sections of one course can arrive from different partition slices.
    annotate_topics(combined.rows)
    return combined
