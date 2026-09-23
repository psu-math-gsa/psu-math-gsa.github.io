# LionPath class search scraper

Two pieces that work together:

- **`scrape.py`** pulls Penn State's public class search
  ([public.lionpath.psu.edu](https://public.lionpath.psu.edu)) into CSV.
- **`schedule_builder.html`** is a browser page for planning a term out of those CSVs — search,
  cart, week grid, calendar export.

Neither needs a server, a build step, or browser automation. The class search is PeopleSoft Fluid
Search and can be driven with plain HTTP requests.

## Typical use 

```bash 
.venv/bin/python mathscrape.py 
.venv/bin/python bundle_data.py
```

## Setup

The first time you run this, create a virtual environment:
```bash
python3 -m venv .venv
.venv/bin/pip install requests beautifulsoup4 lxml
```

Subsequent times, activate the virtual environment:
```bash
source .venv/bin/activate
```

With it active, the scripts can be run directly (`./scrape.py ...`); without it, call the venv's
interpreter explicitly, as the examples below do (`.venv/bin/python scrape.py ...`).

After running the program, deactivate the virtual environment:
```bash
deactivate
```

A venv records its own absolute path, so it breaks if the project folder is moved or renamed —
`activate` then puts a nonexistent directory on `PATH`, the system Python runs instead, and imports
fail with `No module named 'bs4'`. Delete `.venv` and create it again with the two commands above.

## What's here

| Path | What it does |
|---|---|
| `scrape.py` | the command-line scraper |
| `lionpath/` | the scraper as a library — `session`, `parse`, `query`, `search`, `detail`, `csvout`, `progress` |
| `schedule_builder.html` | the schedule builder, one self-contained file |
| `bundle_data.py` | indexes `data/*.csv` so the builder can offer them as bundled sets |
| `fetch_calendar.py` | pulls no-class days from the registrar into `data/academic-calendar.json` |
| `data/` | shipped CSVs, their index files, and the academic calendar |

---

# The scraper

## Usage

```bash
# every CMPSC class at University Park
.venv/bin/python scrape.py --subject CMPSC --campus "University Park" -o cmpsc.csv

# with instructors, meeting dates, status, and seats for wait-listed classes too
.venv/bin/python scrape.py --subject MATH --instructors -o math.csv

# only classes that still have seats
.venv/bin/python scrape.py --subject STAT --open-only -o stat.csv

# a range the site doesn't offer as one value: everything numbered 000-299
.venv/bin/python scrape.py --course-level 000-099 100-199 200-299 -o lower.csv

# what can I filter on?
.venv/bin/python scrape.py --list-facets
.venv/bin/python scrape.py --list-facets --term "Spring 2027"
.venv/bin/python scrape.py --list-terms
```

Filters take the site's own labels (case doesn't matter, and the `(count)` suffix can be left off).
Each takes one or more values after a single flag (see
[Two or more values for one field](#two-or-more-values-for-one-field)):

| Flag | Example |
|---|---|
| `--term` | `"Fall 2026"` — the site only offers a couple of terms at a time |
| `--campus` | `"University Park" "World Campus"` |
| `--subject` | `CMPSC MATH` (bare code or the full `"CMPSC / Computer Science"`) |
| `--career` | `Undergraduate` |
| `--course-level` | `400-499` |
| `--status` | `"Closed Classes Only"` |
| `--attribute` | `"GenEd: Quantification (GQ)"` |
| `--meeting-days` | `"T Th"` |
| `--start-time` | `"10:00 AM - 11:59 AM"` |
| `--instruction-mode` | `"Remote Asynchronous"` |
| `--academic-session` | `"Seven Week - First"` |
| `--class-starts`, `--units` | see `--list-facets` |
| `--keywords` | free-text search |

### Two or more values for one field

List them after the one flag, space separated, quoting any that contain spaces:

```bash
--course-level 000-099 100-199 200-299      # everything numbered under 300
--campus "University Park" "World Campus"
```

Values under the same flag are **OR**ed, exactly as ticking several boxes in one of the site's
filter groups does — the run above is a single search returning every 000-, 100- and 200-level
class. Different flags are **AND**ed, so `--subject CMPSC --course-level 400-499 500-599` means
CMPSC *and* (400-level *or* 500-level).

Two things to watch:

- Repeating the flag does **not** add a value. `--campus A --campus B` keeps only `B`; write
  `--campus A B`.
- The site only offers a facet value while something still matches it, and the filters are applied
  in the table's order (campus, career, subject, course level, …). So a value that is empty *given
  the filters before it* stops the run with exit code `2` —
  `--subject CMPSC --course-level 000-099 100-199` fails because CMPSC has no 000-level courses,
  even though `000-099` is a real site-wide value. Drop the empty value, or run it as its own
  search into a second CSV; the schedule builder loads several CSVs at once.

Other options: `--open-only`, `--instructors`, `--related`, `--delay` (default 0.5s),
`-o`/`--output`, `-v`/`--verbose`, `--no-progress`, and `--list-facets` / `--list-terms` to see
what the site currently offers (`--list-facets` shows the preselected term unless given `--term`).

A filter value or term the site doesn't offer stops the run with exit code `2` and a hint to check
`--list-facets`.

## Watching it run

Runs are slow on purpose, so the scraper reports what it is doing on stderr. In a terminal it
redraws one line in place:

```
  searching     ███░░░░░░░░░░░░░░░░░░░  1/7  100-199 · 220/222 rows  52s
  instructors   ████████████░░░░░░░░░░  27/49  8 course pages  35s  ~29s left
```

The searching line shows each search's pagination as it loads; once a query is split it also gets
a bar counting slices, with the current slice's own pagination in the note. The instructors line
has a real denominator, so it carries an estimate — held back until a few rows are done, since a
guess off the first two swings wildly. Log lines interleave without breaking the bar, and the closing line reports elapsed time.

Piped to a file there is no line to redraw, so it prints an update every 15 seconds instead.
`--no-progress` turns it off. The library never writes to the terminal itself: `run_queries` and
`enrich` take an optional `report(kind, **data)` callback, and `scrape.py` is what renders it.

## Output

One row per class section, 24 columns:

```
term, campus, subject, catalog_number, section, class_number, course_title, topic, component,
career, meeting_days, meeting_times, meeting_location, instructor, seats_available, seats_total,
waitlist_available, status, academic_session, meeting_dates, notes, detail_url, retrieved_at,
related_to
```

A section with several meeting patterns joins them with ` | ` inside `meeting_days`,
`meeting_times` and `meeting_location`, so those three columns stay aligned with each other — the
*n*th day pattern goes with the *n*th time and the *n*th room.

`instructor`, `status`, `academic_session` and `meeting_dates` are filled in only with
`--instructors`. `topic` is blank for ordinary courses and carries the section's own subject for
Special Topics ones. `retrieved_at` records when each row was read, so a stale sheet can say so. `related_to` is blank
except on the recitation and lab rows `--related` adds, where it holds the class number of the
lecture you actually enrol in.

## Recitations and labs

The class search lists only the sections you *enrol* in. For a course like MATH 22 at University Park
that means its four lectures, with the recitations nowhere to be seen — you pick one of those at
enrolment time, so the search treats them as part of the lecture rather than as classes.

They are on the course-detail page, though, bundled into the same option rows the parser already
reads. `--related` turns each one into a row of its own:

```bash
.venv/bin/python scrape.py --subject MATH --campus "University Park" --related -o math.csv
```

```
MATH 22  016   LEC  #26832  M W  9:05 AM-9:55 AM    Wartik Lab 110    Michael Foster
MATH 22  001R  REC  #21397  F    8:00 AM-8:50 AM    Willard Bldg 165  Yue Tian    -> lecture 26832
MATH 22  007R  REC  #21464  F    11:15 AM-12:05 PM  Wagner Bldg 305   Ruixin Li   -> lecture 26832
```

Each keeps its own meeting time, room, instructor and seats; term, campus, subject and title are
inherited from the lecture it is paired with, since the detail page never says which campus a
component belongs to but the lecture does. Times are rewritten into the same form the results list
uses, so nothing downstream can tell a related row from a scraped one. `--related` implies
`--instructors`, because the data comes from the same page — and inherits its 50-row limit (see
below).

## Four things worth knowing

**Seat counts.** The results list only prints "Available Seats" when the *Open Classes Only*
filter is on. So use `--open-only` (free) or `--instructors` (reads seats off each course's detail
page, and covers wait-listed classes too). Without either, the `seats_available` /
`seats_total` columns come out blank and the tool says so. Classes the site reports as `Closed`
have no seat numbers anywhere — their `status` column says `Closed`.

**The 401-row cap.** The server truncates any single search at 401 rows — all of `MATH` in one
query silently returns 401 of ~860. The scraper detects this and re-runs the query split across
another facet, preferring one whose own counts prove every slice will fit (course level or career)
and falling back to subject → campus → course level → career, recursing until nothing is capped,
then merging and de-duplicating on class number. Broad searches therefore cost a lot of requests.
If a slice is still capped after every dimension is exhausted, the file is still written, but the
run warns, names each incomplete slice, and exits `1`, so the gap is never silent.

**Instructors on very large courses.** LionPath's course-detail page renders at most **50 option
rows**, even for a course that lists more — MATH 140 reports 106 options and shows 50, MATH 22
reports 65 and shows 50 — and it returns the same 50 whichever class number you ask it for.
Sections in the rows past that have no instructor available anywhere in the public search, so
`--instructors` leaves their instructor, status, session and dates blank and warns how many. The
rest of each row comes from the search itself and is unaffected. With `--related`, the recitations
and labs of those sections are missing too, since they come from the same page. Narrowing the
search does not help: the limit is on the course page, not on your query.

An option row is not the same as a section. Where a course pairs a lecture with a recitation or
lab, one row carries both, and the detail page numbers them within the row — the lecture's
instructor, room and seats sit in `..._INSTR_LONG_1`, the recitation's in `..._2`. The scraper
reads each component separately and keys the row under every class number in it, so a lecture is
found by its own number and keeps its own instructor rather than its recitation's.

**Special Topics sections each teach something different.** MATH 597 is one catalog entry called
"Special Topics", but its four sections are Arithmetic Statistics, Lyapunov exponents, Quantum &
Semiclassical Analysis and Diff topology. LionPath puts that in the section's title, so the scraper
splits it into the `topic` column and leaves `course_title` as the full string.

Two rules decide when a title is a container rather than a real title, because neither alone is
enough. A **vocabulary rule** matches the head before the colon (`Special`/`Selected Topics`,
`Independent Study`/`Studies`, `Special Studies`, `Research Topics`), which catches a course even
when only one section is offered. A **variance rule** then splits any course whose sections simply
disagree about their title, whatever it is called. Both are deliberate: a plain split on `:` would
mangle real titles like `Artificial Intelligence: Automated Thinking to Augment Human Intellect`.

## Politeness

`public.lionpath.psu.edu/robots.txt` is `Disallow: /`. This is public data you can read in a
browser, but the scraper is deliberately serial, with a delay between requests and no parallelism.
Please leave `--delay` alone unless you have a reason.

## Using it as a library

The CLI is a thin wrapper. To drive several searches into one file, build `Query` objects and merge:

```python
from lionpath import LionPathSession, Query, run_queries
from lionpath.csvout import write_csv

session = LionPathSession()
result = run_queries(session, [
    Query(term="Fall 2026", subject=["CMPSC"], campus=["University Park"]),
    Query(term="Fall 2026", subject=["MATH"], course_level=["400-499"]),
])
write_csv(result.rows, "combined.csv")
```

`run_queries` de-duplicates across queries, so overlapping searches are safe.

## How it works

- Post the search form back with `ICAction=<control id>` and *omit* `ICAJAX` — PeopleSoft then
  returns the whole HTML page instead of an XML partial update, so there is no DOM patching.
- Facet checkboxes (`PTS_SELECT$N`) share one flat index sequence that shifts after every postback,
  so the facet map is re-read from the current page before each apply.
- A fresh page arrives with *Open Classes Only* already applied; it gets removed unless asked for.
- Results load 20 at a time via the `PTS_RSLTS_LIST$hdown$0` control.
- **Every search starts a new session.** Re-entering the component restores whatever search you ran
  last, and the facets are multi-select — so without this, each query in a partitioned run stacks
  its filters onto the previous one and the row counts creep upward.
- After the filters are applied, the breadcrumb trail is checked against what was requested, in
  both directions. A missing filter means a click did not register; an unexpected one means state
  leaked in. Either way the search is retried once, and if it still doesn't match, the run stops
  with an error rather than trusting the results.
- A course-detail row can bundle several components, numbered `_1`, `_2` in the element ids. Each
  is parsed on its own and the row is registered under every class number it mentions; reading the
  cell as one blob keys it under the last component only, which silently loses the lecture.
- Instructor enrichment uses a **separate cookie jar**. Sharing the search session makes the
  course-detail page return only the one class you asked about; from an untouched session it
  returns every section of the course, which is what keeps enrichment to one fetch per course.
- LionPath lists the instructor once per meeting pattern and separates co-teachers with a comma, a
  line break, or both, so a class meeting in person plus online arrives as `Ada Lovelace Ada
  Lovelace`. The scraper splits on every separator and keeps first appearances, giving
  `Ada Lovelace` and `Ada Lovelace, Alan Turing`.

---

# The schedule builder

`schedule_builder.html` opens straight off disk — no server, no build step, nothing to install:

```bash
xdg-open schedule_builder.html
```

Load one or more scraper CSVs (button or drag-and-drop), search, and add sections to a cart. Load a
CSV produced with `--instructors` to get instructor names, seat counts and open/closed status;
without it those columns are blank, exactly as they are in the CSV.

## Searching

**One semester at a time.** Mixing terms in a single list or grid reads as nonsense, so the term is
a choice rather than a filter you can switch off. Search results and the week grid both show the
selected term only, and the grid carries tabs to switch between the terms you have loaded. The cart
is the exception: it keeps every term in separate blocks, so nothing you picked disappears when you
look at another semester — click a block's heading to jump the grid to it. Conflict detection and
*Hide conflicts with cart* stay inside one term, since a Summer class cannot clash with a Fall one.

Results group by course. Nothing opens by default: hovering a course draws all of its sections onto
the grid at once (the first 20, for something like MATH 140), which answers "does any of this fit
my week" faster than reading a list of times would. **Expand all** and **Collapse all** are there
when you want the detail, and a course you open by hand stays open.

Special Topics courses group under their catalog title with each section leading with its own
topic, so MATH 597's four sections read as four different subjects rather than four copies of the
first one. Recitations and labs get a listing of their own next to their course — `MATH 22` and
then `MATH 22 - REC` — so you can pick a lecture and a recitation as two separate choices. They
share the course's colour, since they are the same class, and recolouring either recolours both.

Not every lecture of such a course has them — the World Campus section of MATH 22 is online with
none — so the pairing is read off the rows rather than assumed for the course. A lecture that has
them says **plus recitation (3 options)** under its meeting time, one that doesn't says nothing,
and each recitation says which lecture it **goes with**, so you don't pick a pair that doesn't go
together. A lecture past LionPath's 50-row course-page limit also says nothing, because its
recitations could not be read: in the shipped Fall 2026 file, MATH 22 section 019 is one.

All of that needs a CSV scraped with `--related`. Without one the file has no recitation rows, so
there is no second listing and nothing is flagged — which is honest, since the file genuinely does
not know whether a lecture has one. Sections show when their seat counts were read, so you can tell a live number from a
stale one.

## The week grid

Runs 8 AM to 9 PM and stretches further if a class needs it. Overlapping sections are flagged on
the grid, in the cart, and in a note beneath the calendar.

Anything the grid cannot draw in full is listed under it: a fully online section, a class with no
posted meeting pattern, and also a class that meets in person but carries an online component with
no set time, which would otherwise be half-invisible.

Tiles say as much as they have room for and drop the least useful thing first. A tall tile carries
the course, the time, the room and the instructor; a shorter one drops the *time*, since the row it
sits in already tells you roughly when it is, whereas the room is something the grid cannot imply.
Sections sharing a column fall back to room plus surname, then surname alone, and past three
abreast to just the catalogue number, cut off mid-character rather than ellipsised — at that width
an ellipsis would be most of what you see, and a blank tile reads as a rendering fault rather than
a class. Full detail is on the tile's tooltip throughout.

**Colour means "this is mine".** Anything from the class you are hovering is drawn in neutral grey,
dashed, and labelled *preview* in the "no posted time" list — colour is reserved for what is
actually in your cart, so the two can never be confused.

Each course gets a stable colour, and clicking the colour bar on a cart entry opens a twelve-swatch
picker to change it, with **Reset** to go back to the default. The choice is remembered with the
rest of your cart and applies everywhere that course appears — results list, grid and cart.

## Exports

The cart and the loaded files live in the browser's local storage, so closing the tab doesn't lose
your work. Three ways out:

- **Copy** — a plain list for pasting into a message: `MATH 41 section 007, MoWeFr 9:05 AM-9:55 AM
  Boucke Bldg 214, Jane Doe`. No links, since nobody enrolls from the public site.
- **Save .csv** — the cart as `cart.csv`, in the scraper's column order but without `topic`,
  `retrieved_at` and `related_to`.
- **Save .ics** — a calendar file, one repeating event per meeting pattern, timed for
  `America/New_York`. Each section's own start and end dates come from the CSV's `meeting_dates`,
  so the recurrence stops at the right week; a section without them falls back to the most common
  date range for its term, and one with no dates to borrow is left out and named in the message.

The palette is built from CSS3 colour names rather than arbitrary hues, because that is the only
form iCalendar's `COLOR` property accepts, so **your colours travel into the `.ics`**. Per-event
colour is patchily supported, though — Google Calendar, Apple Calendar and Outlook all currently
ignore it in favour of the calendar's own colour. Nothing is lost either way; the events import
correctly regardless.

## Term breaks in the .ics

The class search knows when a section starts and ends but nothing about holidays, so a calendar
built from it alone would run straight through Thanksgiving. Those days come from a second source —
the registrar's academic calendar — via `fetch_calendar.py`:

```bash
.venv/bin/python fetch_calendar.py                  # terms found in data/*.csv
.venv/bin/python fetch_calendar.py "Spring 2027"    # or name the terms
.venv/bin/python bundle_data.py                     # fold the result into the page
```

It reads the per-year pages at registrar.psu.edu, keeps the entries marked *No Classes*, and writes
them into `data/academic-calendar.json`. Every date it records is stored next to the named event
and date range it came from, so nothing is unverifiable.

The file holds as many terms as you fetch. Each run replaces only the terms it was asked for (or
found in `data/*.csv`) and keeps the rest, so fetching Spring never drops Fall. If the registrar
turns up nothing for a term that already has dates, the old dates stay. To remove a term, delete it
from the JSON by hand.

What ships covers Summer 2026 (Memorial Day, Juneteenth, Independence Day observed), Fall 2026
(Labor Day, Thanksgiving week) and Spring 2027 (Martin Luther King Jr. Day, spring break).

Those days become `EXDATE` exclusions, matched per section: a Monday/Wednesday/Friday class loses
the Monday, Wednesday and Friday of Thanksgiving week, a Tuesday/Thursday class loses the Tuesday
and Thursday, and neither loses Labor Day unless it actually meets on a Monday. The recurrence
still runs to the real end of term — `EXDATE` removes meetings from the series without shortening
it — and the export reports how many it skipped. Without the file nothing is excluded and the
export says so, rather than quietly producing a wrong calendar.

## Bundled sets

CSVs in `data/` show up in a **Bundled sets** dropdown, so common searches are one tick away
instead of a file dialog. Tick to load, untick to remove; several can be on at once, and they merge
with anything you loaded by hand. Five ship with the repo:

| Set | Contents |
|---|---|
| Fall 2026 · MATH 000–299 | University Park + World Campus, 269 classes (33 recitations) |
| Fall 2026 · MATH 300–499 | University Park, 84 classes |
| Fall 2026 · MATH 500–699 | University Park, 95 classes |
| Summer 2026 · MATH 000–699 | University Park, 70 classes |
| Spring 2027 · MATH 500–599 | University Park, 43 classes |

The Fall and Summer 2026 sets are scraped with `--related`, so the lower-level set carries
recitations as well as lectures; the upper-level and summer courses have none. Fall and spring need
splitting by course level because a whole subject overruns the 401-row cap; summer is small enough
for one sheet. Each set is labelled with its term, campuses, class count and the date it was
pulled, all read from the file itself.

To add your own, scrape into `data/` and re-index:

```bash
.venv/bin/python scrape.py --subject STAT --campus "University Park" --instructors -o data/stat-up.csv
.venv/bin/python bundle_data.py
```

`bundle_data.py` reads every CSV in `data/` and writes two index files next to them, labelling each
set from its own contents, so there is no list to maintain by hand.

The page finds those sets three ways, in order: `data/bundled.js` (the index with the CSV text
inlined, loaded by a `<script>` tag — the only one that works when you open the page straight off
disk, since browsers block `fetch` on `file://` URLs); `data/manifest.json`; and failing that, a
plain directory listing of `data/`, which works on any server with autoindex on. Drop CSVs on a
static host with autoindex and they appear with no index files at all. If none of the three turn
anything up, the dropdown stays hidden.
