"""The search parameters for one class search.

This is the seam a future GUI sits on: build a list of Query objects and hand
them to search.run_queries(). Nothing in the core knows about argparse.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Query field -> the facet group name the site renders it under. The order here
# is also the order filters get applied in.
FACET_FIELDS: dict[str, str] = {
    "campus": "Campus",
    "career": "Course Career",
    "subject": "Subject",
    "course_level": "Course Level",
    "status": "Class Status",
    "attribute": "Course Attribute",
    "meeting_days": "Class Meeting Days",
    "start_time": "Class Start Time",
    "instruction_mode": "Instruction Mode",
    "academic_session": "Academic Session",
    "class_starts": "Class Starts",
    "units": "Units",
}

# Dimensions to subdivide along, cheapest-first, when a search hits the row cap.
# Subject leads because it splits the catalog most evenly for the fewest
# sub-queries; campus is the natural second cut for a single oversized subject.
PARTITION_ORDER: tuple[str, ...] = ("subject", "campus", "course_level", "career")


@dataclass
class Query:
    """One search. Empty list means "no filter on this dimension"."""

    term: str = ""
    keywords: str = ""
    open_only: bool = False

    campus: list[str] = field(default_factory=list)
    career: list[str] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)
    course_level: list[str] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    attribute: list[str] = field(default_factory=list)
    meeting_days: list[str] = field(default_factory=list)
    start_time: list[str] = field(default_factory=list)
    instruction_mode: list[str] = field(default_factory=list)
    academic_session: list[str] = field(default_factory=list)
    class_starts: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)

    def selections(self) -> list[tuple[str, str]]:
        """(facet group name, value) pairs to apply, in a stable order."""
        pairs = []
        for name, group in FACET_FIELDS.items():
            for value in getattr(self, name):
                pairs.append((group, value))
        return pairs

    def narrowed(self, dimension: str, value: str) -> "Query":
        return replace(self, **{dimension: [value]})

    def describe(self) -> str:
        parts = []
        if self.term:
            parts.append(self.term)
        for name in FACET_FIELDS:
            values = getattr(self, name)
            if values:
                parts.append(f"{name}={'+'.join(values)}")
        if self.keywords:
            parts.append(f"keywords={self.keywords!r}")
        if self.open_only:
            parts.append("open-only")
        return ", ".join(parts) or "all classes"
