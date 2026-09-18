#!/usr/bin/env python3
"""Dump Penn State LionPath public class search results to CSV.

    ./scrape.py --subject CMPSC --campus "University Park" -o cmpsc.csv
    ./scrape.py --term "Fall 2026" --subject MATH --instructors -o math.csv
    ./scrape.py --list-facets
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from lionpath import LionPathSession, Query, detail
from lionpath.csvout import write_csv
from lionpath.progress import Progress, ProgressHandler, human_time
from lionpath.parse import available_terms, current_term, facet_groups
from lionpath.query import FACET_FIELDS
from lionpath.search import FiltersNotApplied, UnknownFacetValue, run_queries

# CLI flag <- query field, for the multi-value facet options.
FACET_FLAGS = {
    "campus": "--campus",
    "career": "--career",
    "subject": "--subject",
    "course_level": "--course-level",
    "status": "--status",
    "attribute": "--attribute",
    "meeting_days": "--meeting-days",
    "start_time": "--start-time",
    "instruction_mode": "--instruction-mode",
    "academic_session": "--academic-session",
    "class_starts": "--class-starts",
    "units": "--units",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-o", "--output", default="classes.csv", help="CSV path (default: %(default)s)")
    parser.add_argument("--term", default="", help='e.g. "Fall 2026" (default: whatever the site preselects)')
    parser.add_argument("--keywords", default="", help="free-text keyword search")
    parser.add_argument("--open-only", action="store_true", help="only classes with seats available")
    parser.add_argument(
        "--instructors",
        action="store_true",
        help="also fetch instructor, status, session and meeting dates "
             "(one extra request per course; slower)",
    )
    parser.add_argument(
        "--related",
        action="store_true",
        help="also list recitations and labs as rows of their own; the class "
             "search does not, since you enrol in the lecture (implies --instructors)",
    )

    facets = parser.add_argument_group("filters (repeatable; use the site's own labels)")
    for field_name, flag in FACET_FLAGS.items():
        facets.add_argument(flag, dest=field_name, nargs="+", default=[], metavar="VALUE")

    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests (default: %(default)s)")
    parser.add_argument("--list-facets", action="store_true", help="print every filter value the site offers, then exit")
    parser.add_argument("--list-terms", action="store_true", help="print the available terms, then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="show per-sub-query progress")
    parser.add_argument("--no-progress", action="store_true", help="don't draw the progress line")
    return parser


def make_reporter(bar: Progress):
    """Turn the library's events into one live line."""
    state = {"slice": "", "index": 0, "slices": 0, "rows": 0}

    def report(kind, **data):
        if kind == "split":
            state["slices"] = data["total"]
            state["index"] = 0
            bar.start("searching", total=data["total"])
        elif kind == "slice_start":
            state["index"] = data["index"]
            state["slice"] = f"{data['value']}"
            bar.update(done=data["index"] - 1, note=f"{data['value']} · {state['rows']} rows so far")
        elif kind == "search":
            if not state["slices"]:
                bar.start("searching", total=None)
                bar.update(note=data["query"])
        elif kind == "page":
            note = f"{data['loaded']}"
            if data.get("total"):
                note += f"/{data['total']}"
            note += " rows"
            if state["slice"]:
                note = f"{state['slice']} · {note}"
            bar.update(note=note)
        elif kind == "slice":
            state["rows"] += data["rows"]
            if state["slices"]:
                bar.update(done=state["index"], note=f"{state['rows']} rows so far")
            else:
                bar.update(note=f"{data['rows']} rows")
        elif kind == "enrich_start":
            bar.start("instructors", total=data["total"])
        elif kind == "enrich":
            pages = data["pages"]
            bar.update(done=data["done"], note=f"{pages} course page" + ("" if pages == 1 else "s"))

    return report


def list_facets(session: LionPathSession, term: str) -> None:
    page = session.open_search()
    if term:
        from lionpath.search import apply_query
        page = apply_query(session, Query(term=term))
    print(f"Term: {current_term(page)}   (available: {', '.join(available_terms(page))})\n")
    for name, group in facet_groups(page).items():
        if not group.values:
            continue
        flag = next((f for k, f in FACET_FLAGS.items() if FACET_FIELDS[k] == name), "")
        print(f"{name}  {flag}")
        for value in group.values:
            print(f"    {value.base}")
        print()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bar = Progress(enabled=not args.no_progress)
    Progress.active = bar
    handler = ProgressHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        handlers=[handler],
    )
    # -v is for our own progress, not urllib3's connection chatter.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    started = time.monotonic()

    session = LionPathSession(delay=args.delay)

    if args.list_terms:
        print("\n".join(available_terms(session.open_search())))
        return 0
    if args.list_facets:
        list_facets(session, args.term)
        return 0

    query = Query(
        term=args.term,
        keywords=args.keywords,
        open_only=args.open_only,
        **{name: getattr(args, name) for name in FACET_FLAGS},
    )
    logging.info("searching: %s", query.describe())

    report = make_reporter(bar)
    try:
        result = run_queries(session, [query], report)
    except (UnknownFacetValue, FiltersNotApplied, ValueError) as error:
        bar.finish()
        print(f"error: {error}", file=sys.stderr)
        print("hint: run --list-facets to see the exact values the site offers.", file=sys.stderr)
        return 2

    if (args.instructors or args.related) and result.rows:
        enriched = detail.enrich(session, result.rows, report, include_related=args.related)
        if args.related and enriched.related:
            result.rows.extend(enriched.related)
    bar.finish()

    written = write_csv(result.rows, args.output)

    # The results list only prints seat counts under the Open Classes Only
    # filter; everywhere else they come from the detail pages.
    missing_seats = sum(1 for row in result.rows if not row.seats_total)
    if missing_seats and not (args.instructors or args.related):
        print(
            f"note: {missing_seats} of {written} rows have no seat counts. The class list only "
            "shows seats for open classes -- add --open-only, or --instructors to read seats "
            "(and instructors) from each course's detail page.",
            file=sys.stderr,
        )

    logging.info(
        "wrote %d classes to %s (%d searches, %d requests, %s)",
        written, args.output, result.searches_run, session.requests_made,
        human_time(time.monotonic() - started),
    )
    if result.truncated:
        print(
            f"warning: {len(result.truncated)} slice(s) still hit the server's row cap "
            "and are incomplete:", file=sys.stderr,
        )
        for description in result.truncated:
            print(f"  - {description}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
