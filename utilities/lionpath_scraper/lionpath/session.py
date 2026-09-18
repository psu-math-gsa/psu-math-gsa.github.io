"""HTTP plumbing for PeopleSoft Fluid postbacks.

The one non-obvious trick: omit the ICAJAX field and PeopleSoft returns the
complete HTML page instead of an XML partial-update document, so there is no
DOM patching to do -- every interaction is one POST in, one full page out.
"""

from __future__ import annotations

import logging
import time

import requests

from .parse import Page, hidden_fields

log = logging.getLogger(__name__)

SEARCH_URL = (
    "https://public.lionpath.psu.edu/psc/CSPRD/EMPLOYEE/SA/c/"
    "PE_SR175_PUBLIC.PE_SR175_CLS_SRCH.GBL"
)
DETAIL_URL = (
    "https://public.lionpath.psu.edu/psc/CSPRD_newwin/EMPLOYEE/PSFT_HR/c/"
    "PE_SR175_PUBLIC.SSR_CRSE_INFO_FL.GBL"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class SessionExpired(RuntimeError):
    """The server stopped returning a usable search page."""


class LionPathSession:
    """A single, strictly serial conversation with the class search.

    Server-side state is sequential, so actions must be issued one at a time
    against the most recent page. Requests are deliberately unparallelised and
    spaced by `delay` seconds -- the site's robots.txt disallows crawling, so
    the least this tool can do is behave like one slow human.
    """

    def __init__(self, delay: float = 0.5, timeout: float = 60.0, retries: int = 3):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.requests_made = 0
        self.resets = 0
        self._http = self._new_http()
        # Deliberately separate cookie jar. The course-detail component narrows
        # its section list to the active search when it shares a session with
        # the search page -- from an untouched session it returns every section
        # of the course, which is what makes one-fetch-per-course possible.
        self._detail_http = self._new_http()

    @staticmethod
    def _new_http() -> requests.Session:
        http = requests.Session()
        http.headers.update({"User-Agent": USER_AGENT})
        return http

    def reset(self) -> None:
        """Start a clean search session.

        A single PeopleSoft session degrades over a long run: after enough
        postbacks it stops honouring facet selections and quietly serves the
        unfiltered result set instead. A fresh cookie jar clears that.
        """
        self._http = self._new_http()
        self.resets += 1

    def _request(self, method: str, url: str, http=None, **kwargs) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            if self.delay:
                time.sleep(self.delay)
            try:
                response = (http or self._http).request(
                    method, url, timeout=self.timeout, allow_redirects=True, **kwargs
                )
            except requests.RequestException as error:
                last_error = error
            else:
                self.requests_made += 1
                if response.status_code < 500:
                    return response
                last_error = RuntimeError(f"HTTP {response.status_code} from {url}")
            backoff = 2.0 * attempt
            log.warning("request failed (attempt %d/%d): %s; retrying in %.0fs",
                        attempt, self.retries, last_error, backoff)
            time.sleep(backoff)
        raise RuntimeError(f"giving up on {url}: {last_error}")

    def open_search(self) -> Page:
        """Load a fresh search page (this also establishes the session cookie)."""
        response = self._request(
            "GET", SEARCH_URL, params={"Page": "PE_SR175_CLS_SRCH", "Action": "U"}
        )
        page = Page(response.text, response.url)
        if not page.has_form:
            raise SessionExpired("class search did not return a usable page")
        return page

    def action(self, page: Page, ic_action: str, extra: dict[str, str] | None = None) -> Page:
        """Fire one PeopleSoft control and return the resulting full page."""
        payload = hidden_fields(page)
        if not payload:
            raise SessionExpired("no hidden form fields on the current page")
        payload["ICAction"] = ic_action
        payload["ICNAVTYPEDROPDOWN"] = "0"
        if extra:
            payload.update(extra)

        response = self._request(
            "POST",
            SEARCH_URL,
            data=payload,
            headers={"Referer": page.url or SEARCH_URL},
        )
        result = Page(response.text, response.url)
        if not result.has_form:
            raise SessionExpired(f"session lost while firing {ic_action}")
        return result

    def get_detail(self, params: dict[str, str]) -> Page:
        """Fetch a course-detail page, on the search-free session (see __init__)."""
        response = self._request("GET", DETAIL_URL, params=params, http=self._detail_http)
        return Page(response.text, response.url)
