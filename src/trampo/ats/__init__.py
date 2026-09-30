"""ATS client protocol: fetch Postings from a public job board API.

One client per ATS behind a shared Protocol. Task 5 adds Greenhouse; Tasks 6
and 7 add Lever and Ashby, reusing html_to_text.
"""

import re
from html.parser import HTMLParser
from typing import Protocol

import httpx

from trampo.models import Ats, Posting


class BoardNotFound(Exception):
    """The ATS returned 404 for a Board's slug."""


class AtsClient(Protocol):
    ats: Ats

    def fetch_postings(self, slug: str, company: str) -> list[Posting]:
        """Fetch all Postings on a Board. Raises BoardNotFound on 404."""
        ...


def client_for(ats: Ats, http: httpx.Client) -> AtsClient:
    if ats == "greenhouse":
        from trampo.ats.greenhouse import GreenhouseClient

        return GreenhouseClient(http)
    if ats == "lever":
        from trampo.ats.lever import LeverClient

        return LeverClient(http)
    raise NotImplementedError(f"ATS client not implemented yet: {ats}")


_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"}
)

# HTML whitespace (space, tab, CR, LF, form feed) inside text runs collapses to one
# space, same as a browser would render it.
_WHITESPACE_RUN = re.compile(r"[ \t\r\n\f]+")

# Marks a block-tag boundary in the parser's output; split on it below instead of on
# "\n" so that real newlines inside text data (already collapsed to a space) never
# get mistaken for a block break.
_BREAK = "\x00"


class _BlockTextParser(HTMLParser):
    """Turns block elements into line breaks; everything else stays inline."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.chunks.append(_BREAK)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.chunks.append(_BREAK)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # A self-closed void tag (<br/>) is one event, not a start+end pair: without
        # this override the base class calls handle_starttag then handle_endtag,
        # producing two breaks where the open form (<br>) produces only one.
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.chunks.append(_WHITESPACE_RUN.sub(" ", data))


def html_to_text(html: str) -> str:
    """Strip HTML tags to plain text, keeping paragraph breaks.

    Entities are decoded (HTMLParser(convert_charrefs=True)). Whitespace runs
    inside text collapse to one space; each line is stripped and runs of
    blank lines collapse to one.
    """
    parser = _BlockTextParser()
    parser.feed(html)
    parser.close()
    lines = "".join(parser.chunks).split(_BREAK)

    result: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line:
            result.append(line)
        elif result and result[-1] != "":
            result.append("")
    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)
