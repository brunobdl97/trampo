"""Discovery: find new ATS Boards through Claude's server-side web search,
validate each against the ATS API, and add it to the Store, within the day's
search quota (config.toml: discovery_max_searches_per_day).
"""

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import anthropic
import httpx2
from anthropic.types import Message

from trampo.ats import AtsClient, BoardNotFound
from trampo.claude import create_message, load_prompt
from trampo.config import Config
from trampo.models import Ats, BoardRef
from trampo.store import Store

logger = logging.getLogger(__name__)

_RECENT_DAYS = 14  # a query used within this window is deprioritized, not skipped
_MAX_TOKENS = 16000

# Domains combined into search queries (Ruling: no boards.greenhouse.io here).
_QUERY_DOMAINS = ("jobs.ashbyhq.com", "job-boards.greenhouse.io", "jobs.lever.co")
_REMOTE_TERMS = ("remote", "LATAM", "Brazil")

# Domains the web_search tool itself is restricted to (both Greenhouse hosts).
_ALLOWED_DOMAINS = (
    "jobs.ashbyhq.com",
    "job-boards.greenhouse.io",
    "boards.greenhouse.io",
    "jobs.lever.co",
)

_HOST_ATS: dict[str, Ats] = {
    "jobs.ashbyhq.com": "ashby",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
}
_NON_SLUG_SEGMENTS = frozenset({"embed", "api", "v1"})


def board_refs_from_urls(urls: Iterable[str]) -> set[BoardRef]:
    """Parse Board references out of search-result URLs. Unrecognized hosts,
    empty paths and non-slug first segments (embed, api, v1) are skipped."""
    refs: set[BoardRef] = set()
    for url in urls:
        parsed = urlsplit(url)
        ats = _HOST_ATS.get(parsed.hostname or "")
        if ats is None:
            continue
        segments = [s for s in parsed.path.split("/") if s]
        if not segments or segments[0] in _NON_SLUG_SEGMENTS:
            continue
        refs.add(BoardRef(ats=ats, slug=segments[0]))
    return refs


def discovery_queries(config: Config) -> list[str]:
    """A deterministic, duplicate-free list of `site:` search queries: every
    combination of ATS domain, Track title keyword (config order) and remote
    term."""
    keywords = [kw for track in config.tracks for kw in track.title_keywords]
    queries: list[str] = []
    seen: set[str] = set()
    for term in _REMOTE_TERMS:
        for domain in _QUERY_DOMAINS:
            for keyword in keywords:
                query = f'site:{domain} "{keyword}" {term}'
                if query not in seen:
                    seen.add(query)
                    queries.append(query)
    return queries


def _company_from_slug(slug: str) -> str:
    words = [w for part in slug.split("-") for w in part.split("_")]
    return " ".join(word.capitalize() for word in words if word)


def _search_result_urls(message: Message) -> list[str]:
    """URLs from web_search_tool_result blocks whose content is a result list.
    A block whose content is an error object is skipped; model text and
    citations are ignored."""
    urls: list[str] = []
    for block in message.content:
        if block.type != "web_search_tool_result":
            continue
        content = block.content
        if isinstance(content, list):
            urls.extend(result.url for result in content)
    return urls


def discover(
    client: anthropic.Anthropic,
    store: Store,
    config: Config,
    ats_clients: dict[Ats, AtsClient],
    today: date,
    model: str,
) -> list[BoardRef]:
    """Run up to the day's remaining search quota, add each newly-found and
    validated Board, and return the ones added. A SpendLimitReached from
    create_message propagates to the caller."""
    quota = max(0, config.discovery_max_searches_per_day - store.discovery_searches_on(today))
    if quota == 0:
        return []

    recent = store.discovery_queries_since(today - timedelta(days=_RECENT_DAYS))
    queries = discovery_queries(config)
    ordered = [q for q in queries if q not in recent] + [q for q in queries if q in recent]

    prompt = load_prompt("discovery")
    known = store.known_boards()
    added: list[BoardRef] = []

    for query in ordered[:quota]:
        params = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "thinking": {"type": "adaptive"},
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 1,
                    "allowed_domains": list(_ALLOWED_DOMAINS),
                }
            ],
            "messages": [{"role": "user", "content": prompt.text.replace("{{query}}", query)}],
        }
        message = create_message(client, params)
        store.log_discovery_search(today, query)

        refs = board_refs_from_urls(_search_result_urls(message))
        for ref in sorted(refs, key=lambda r: (r.ats, r.slug)):
            if ref in known:
                continue
            company = _company_from_slug(ref.slug)
            try:
                ats_clients[ref.ats].fetch_postings(ref.slug, company)
            except BoardNotFound:
                continue
            except httpx2.HTTPError:
                logger.warning("Discovery: %s/%s failed validation", ref.ats, ref.slug)
                continue
            store.add_board(ref, company, datetime.now(UTC))
            known.add(ref)
            added.append(ref)

    return added
