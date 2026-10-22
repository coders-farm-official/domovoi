"""Looking things up: the core's internet sources, and the one lookup every
caller uses (assistant plan part 5, milestone W1).

Three classes of source answer a lookup (:mod:`domovoi.lookup`), asked in
the core's order, local first:

* **local** — Domovoi's own help (``domovoi/help_desk``) and an offline
  library a plugin brings (Kiwix). Never leaves the house: no question
  asked, and it works while the internet answer is "never".
* **api** — :class:`WikipediaSource` ("core.wikipedia"): Wikipedia and
  Wikidata, through this server's own search helper (SearXNG, which
  ``domovoi/searxng/settings.yml`` sets to rank them first). When the
  helper doesn't answer, Wikipedia's own search API is asked directly, so
  a lookup still has a source that works when the scraped engines break.
* **web** — :class:`OpenWebSource` ("core.web"): the helper's other
  (scraped) engines, or the household's own search ("Use your own
  search", Settings → Chat & AI → Search): its own SearXNG address, or
  Brave Search with its own key. A search that fails says so
  (:attr:`LookupOutcome.notice`) and the answer falls back to Wikipedia.

Every byte that leaves goes through :mod:`domovoi.egress`: the helper is a
local service that proxies to the internet, so :func:`egress.require_internet`
is asked first; Wikipedia's and Brave's APIs and a page the person names
are fetched with :mod:`domovoi.net_safety` (public addresses only, every
redirect re-checked, a byte cap) and refused under "never" by it.

:func:`run_lookup` is the search (at most 3 results, each with a short
snippet and, where the source can give one, an extract chosen for the
question); :func:`read_page` is "what does this page say: <url>" (HTTPS
only, no cookies, no JavaScript, 2 MB at most, the main text kept). The
results are DATA: whoever gives them to a model fences them
(:mod:`domovoi.lookup_answer`).

Nothing here records a query: the log names the source and the outcome,
never the words (``log_privacy.said``).
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from domovoi import egress, home_address, lookup, net_safety
from domovoi.log_privacy import said

log = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/coders-farm-official/domovoi"
#: Results one lookup returns, at most (the phone route, the chat).
MAX_RESULTS = 3
#: A page the person names: at most this many bytes are read.
PAGE_MAX_BYTES = 2 * 1024 * 1024
#: An API answer (search results, a summary): at most this many bytes.
API_MAX_BYTES = 512 * 1024
#: How long one outbound call may take.
HTTP_TIMEOUT_SEC = 8.0
#: How much of a source's text is kept as a result's extract.
EXTRACT_MAX_CHARS = 1500
#: The licence a Wikipedia text carries, said under every reply that used one.
WIKIPEDIA_LICENCE = "CC BY-SA 4.0"
_WIKI_HOST_RE = re.compile(r"(?:^|\.)wikipedia\.org$", re.I)
_WIKIDATA_HOST_RE = re.compile(r"(?:^|\.)wikidata\.org$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


class LookupRefused(ValueError):
    """A lookup that can't be made as asked (a page that isn't HTTPS, a
    page of a kind that can't be read). ``code`` is stable; the text is a
    plain sentence for the person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OwnSearchFailed(RuntimeError):
    """The household's own search (its SearXNG, its Brave key) didn't
    work. The text says why in plain words; the lookup falls back."""


# ─── Small helpers ────────────────────────────────────────────────────────


_VERSION: str | None = None


def _version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            from domovoi import git_version

            _VERSION = git_version._pyproject_version() or "dev"
        except Exception:  # noqa: BLE001
            _VERSION = "dev"
    return _VERSION


def server_agent() -> str:
    """The User-Agent of the server's own API calls (Wikipedia asks every
    client to name itself and a way to reach its maker)."""
    return f"Domovoi/{_version()} ({PROJECT_URL})"


def user_agent() -> str:
    """The User-Agent of a page the person asked for: says it is a fetch a
    person started, not a crawler."""
    return f"Domovoi-User/{_version()} ({PROJECT_URL})"


def site_of(url: str | None) -> str:
    """The host a person reads ("en.wikipedia.org"), or ""."""
    try:
        host = urlsplit(str(url or "")).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_wikipedia(url: str | None) -> bool:
    return bool(_WIKI_HOST_RE.search(site_of(url)))


def _clean(text: Any, limit: int) -> str:
    t = _TAG_RE.sub("", str(text or ""))
    import html as _html

    t = _html.unescape(t)
    return " ".join(t.split())[:limit]


def _settings() -> Any:
    from domovoi.config import settings

    return settings


#: A seam for the tests: an httpx transport every client here is built
#: with (a fake search helper, a fake Wikipedia). None in production.
TRANSPORT: Any = None


def _extra() -> dict[str, Any]:
    return {"transport": TRANSPORT} if TRANSPORT is not None else {}


def _no_cookies() -> http.cookiejar.CookieJar:
    """A cookie jar that keeps nothing: a fetch never sends a cookie a
    redirect hop set."""
    return http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))


async def _get_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                    local: bool = False) -> tuple[int, Any]:
    """``(status, json or None)`` for a GET. ``local``: a service on this
    network (the search helper, the household's own SearXNG), through the
    egress client; otherwise a public API, through net_safety."""
    import httpx

    hdrs = {"Accept": "application/json", "User-Agent": server_agent(), **(headers or {})}
    if params:
        from urllib.parse import urlencode

        url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    if local:
        async with egress.async_client(timeout=HTTP_TIMEOUT_SEC, cookies=_no_cookies(),
                                       follow_redirects=False, **_extra()) as client:
            resp = await client.get(url, headers=hdrs)
            body = resp.content[:API_MAX_BYTES + 1]
            status = resp.status_code
    else:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC, cookies=_no_cookies(), **_extra()) as client:
            res = await net_safety.fetch_bytes(url, max_bytes=API_MAX_BYTES, headers=hdrs, client=client)
            body, status = res.content, res.status_code
    if len(body) > API_MAX_BYTES:
        return status, None
    try:
        import json

        return status, json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return status, None


# ─── The search helper (SearXNG) ──────────────────────────────────────────

#: The helper's engines that are public APIs (settings.yml ranks them first).
API_ENGINES = ("wikipedia", "wikidata", "wikipedia search")


async def searxng_search(base: str, query: str, *, engines: tuple[str, ...] | None = None,
                         limit: int = 5) -> tuple[str, list[dict[str, Any]]]:
    """``(status, items)`` from a SearXNG at ``base``: ``ok``,
    ``no_results``, ``unreachable``, ``refused`` (its JSON output is off:
    403) or ``error``. Items are ``{title, url, content, engine}``, the
    infoboxes (where Wikipedia's and Wikidata's engines put their best
    answer) first. Raises :class:`egress.InternetTurnedOff` under "never"."""
    import httpx

    egress.require_internet("lookup")
    params: dict[str, Any] = {"q": query, "format": "json", "language": "en-US"}
    if engines:
        params["engines"] = ",".join(engines)
    try:
        status, payload = await _get_json(f"{base.rstrip('/')}/search", params=params, local=True)
    except egress.InternetTurnedOff:
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return "unreachable", []
    except Exception as e:  # noqa: BLE001 — a search that failed found nothing
        log.warning("lookup: SearXNG failed: %s", type(e).__name__)
        return "error", []
    if status == 403:
        return "refused", []
    if status >= 400 or not isinstance(payload, dict):
        return "error", []
    items: list[dict[str, Any]] = []
    for box in payload.get("infoboxes") or []:
        if not isinstance(box, dict):
            continue
        url = box.get("id") or next((u.get("url") for u in box.get("urls") or []
                                     if isinstance(u, dict) and u.get("url")), None)
        if url and box.get("infobox"):
            items.append({"title": box.get("infobox"), "url": url, "content": box.get("content") or "",
                          "engine": box.get("engine") or ""})
    for r in payload.get("results") or []:
        if isinstance(r, dict) and r.get("url") and r.get("title"):
            items.append({"title": r["title"], "url": r["url"], "content": r.get("content") or "",
                          "engine": r.get("engine") or ""})
    seen: set[str] = set()
    out = []
    for it in items:
        key = str(it["url"]).split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out = out[:limit]
    return ("ok" if out else "no_results"), out


# ─── Wikipedia (and Wikidata): the public-API source ──────────────────────


def _wiki_base() -> str:
    return str(_settings().lookup_wikipedia_url or "https://en.wikipedia.org").rstrip("/")


def _title_from_url(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.path.startswith("/wiki/"):
        return None
    title = unquote(parts.path[len("/wiki/"):])
    return title or None


async def wikipedia_search(query: str, *, limit: int = 3, base: str | None = None) -> list[dict[str, Any]]:
    """Wikipedia's own search (``/w/rest.php/v1/search/page``): items as
    :func:`searxng_search` gives them. The fallback when the helper is
    down; the phone does the same from its side."""
    base = (base or _wiki_base()).rstrip("/")
    status, payload = await _get_json(f"{base}/w/rest.php/v1/search/page", params={"q": query, "limit": limit})
    if status >= 400 or not isinstance(payload, dict):
        return []
    out = []
    for p in payload.get("pages") or []:
        if not isinstance(p, dict) or not p.get("key") or not p.get("title"):
            continue
        out.append({"title": p["title"], "url": f"{base}/wiki/{quote(str(p['key']))}",
                    "content": _clean(p.get("excerpt") or p.get("description") or "", 600),
                    "engine": "wikipedia"})
    return out[:limit]


async def wikipedia_summary(url: str) -> tuple[str, str] | None:
    """``(title, extract)`` of a Wikipedia page from its REST summary, or
    None. Only for a ``*.wikipedia.org`` page (or the configured base)."""
    title = _title_from_url(url)
    if not title:
        return None
    parts = urlsplit(url)
    host_base = f"{parts.scheme}://{parts.netloc}"
    if not (is_wikipedia(url) or host_base.rstrip("/") == _wiki_base()):
        return None
    # The English Wikipedia's API is the configured one (LOOKUP_WIKIPEDIA_URL,
    # the real one unless a test points it at a fake); another language's
    # page is summarised by its own wiki.
    if (parts.hostname or "").lower() == "en.wikipedia.org":
        host_base = _wiki_base()
    status, payload = await _get_json(f"{host_base}/api/rest_v1/page/summary/{quote(title, safe='')}")
    if status >= 400 or not isinstance(payload, dict):
        return None
    extract = _clean(payload.get("extract") or "", 6000)
    if not extract:
        return None
    return _clean(payload.get("title") or title.replace("_", " "), 200), extract


class WikipediaSource:
    """Wikipedia and Wikidata: public APIs with real terms (class API)."""

    slug = "wikipedia"
    name = "Wikipedia and Wikidata"
    lookup_class = lookup.API

    async def search(self, query: str, *, limit: int = 3) -> list[lookup.LookupHit]:
        status, items = await searxng_search(_settings().searxng_url, query, engines=API_ENGINES, limit=limit)
        if status in ("unreachable", "error", "refused", "no_results"):
            if status != "no_results":
                log.info("lookup: the search helper answered %s; asking Wikipedia directly", status)
            try:
                items = await wikipedia_search(query, limit=limit)
            except egress.InternetTurnedOff:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("lookup: Wikipedia's search failed: %s", type(e).__name__)
                items = []
        return [_hit(it) for it in items[:limit]]

    async def fetch(self, id: str) -> lookup.LookupDocument | None:
        if not is_wikipedia(id) and not id.startswith(_wiki_base()):
            return None
        got = await wikipedia_summary(id)
        if got is None:
            return None
        title, extract = got
        return lookup.LookupDocument(id=id, title=title, text=extract, url=id, licence=WIKIPEDIA_LICENCE)


def _hit(it: dict[str, Any]) -> lookup.LookupHit:
    url = str(it.get("url") or "")
    return lookup.LookupHit(
        id=url, title=_clean(it.get("title"), 200), snippet=_clean(it.get("content"), 600), url=url,
        section=site_of(url), licence=WIKIPEDIA_LICENCE if is_wikipedia(url) else None,
    )


# ─── The open web: the helper's other engines, or the household's own ─────


def own_search() -> str:
    """``""`` (the helper), ``"searxng"`` or ``"brave"``: what the
    household chose, when it is usable (a choice with nothing filled in is
    the helper)."""
    s = _settings()
    choice = str(s.lookup_own_search or "").strip().lower()
    if choice == "searxng" and str(s.lookup_own_searxng_url or "").strip():
        return "searxng"
    if choice == "brave" and str(s.lookup_brave_key or "").strip():
        return "brave"
    return ""


def destination(kind: str = "search", url: str | None = None, *, own_host: bool = True) -> str:
    """Where a lookup goes, in the words the permission card uses.
    ``own_host`` False leaves the household's own SearXNG address out (what
    the device tier reads)."""
    if kind == "page":
        return site_of(url) or "that site"
    own = own_search()
    web = {
        "": "then the web through its search helper",
        "searxng": (f"then your own SearXNG ({site_of(_settings().lookup_own_searxng_url)})" if own_host
                    else "then your own SearXNG"),
        "brave": "then Brave Search, with your key",
    }[own]
    return f"your home server's search: Wikipedia and Wikidata first, {web}"


async def brave_search(query: str, key: str, *, limit: int = 3, base: str | None = None) -> list[dict[str, Any]]:
    """Brave Search's web search with the household's key. Raises
    :class:`OwnSearchFailed` (refused, over its limit, unreachable)."""
    import httpx

    base = (base or str(_settings().lookup_brave_url or "https://api.search.brave.com")).rstrip("/")
    try:
        status, payload = await _get_json(
            f"{base}/res/v1/web/search", params={"q": query, "count": max(1, min(limit, 10))},
            headers={"X-Subscription-Token": key},
        )
    except egress.InternetTurnedOff:
        raise
    except (httpx.HTTPError, net_safety.OutboundFetchError) as e:
        raise OwnSearchFailed("Brave Search didn't answer") from e
    if status in (401, 403):
        raise OwnSearchFailed("Brave Search refused your key")
    if status == 429:
        raise OwnSearchFailed("Brave Search says your key is over its limit")
    if status >= 400 or not isinstance(payload, dict):
        raise OwnSearchFailed(f"Brave Search answered with an error ({status})")
    out = []
    for r in ((payload.get("web") or {}).get("results")) or []:
        if isinstance(r, dict) and r.get("url") and r.get("title"):
            out.append({"title": r["title"], "url": r["url"], "content": r.get("description") or "",
                        "engine": "brave"})
    return out[:limit]


class OpenWebSource:
    """The open web (class WEB): the search helper's scraped engines, or the
    household's own SearXNG or Brave key."""

    slug = "web"
    name = "The web"
    lookup_class = lookup.WEB

    async def search(self, query: str, *, limit: int = 3) -> list[lookup.LookupHit]:
        s = _settings()
        own = own_search()
        if own == "brave":
            items = await brave_search(query, str(s.lookup_brave_key).strip(), limit=limit)
        elif own == "searxng":
            address = str(s.lookup_own_searxng_url).strip()
            # Saved before the home-network rule, or written into .env by
            # hand: still never sent in the clear across the internet.
            problem = home_address.own_search_address_problem(address)
            if problem:
                raise OwnSearchFailed(f"your SearXNG address can't be used: {problem}")
            status, items = await searxng_search(address, query, limit=limit)
            if status == "unreachable":
                raise OwnSearchFailed("your SearXNG didn't answer")
            if status == "refused":
                raise OwnSearchFailed("your SearXNG refused the search (is its JSON output on?)")
            if status == "error":
                raise OwnSearchFailed("your SearXNG answered with an error")
        else:
            _status, items = await searxng_search(s.searxng_url, query, limit=limit + 3)
            # Wikipedia's answers come from the source before this one.
            items = [it for it in items if it.get("engine") not in API_ENGINES and not is_wikipedia(it.get("url"))]
        return [_hit(it) for it in items[:limit]]

    async def fetch(self, id: str) -> lookup.LookupDocument | None:
        return None   # a web result is read only when the person names it (read_page)


lookup.mark_core_type(WikipediaSource)
lookup.mark_core_type(OpenWebSource)

WIKIPEDIA_SLUG = "core.wikipedia"
WEB_SLUG = "core.web"


def register_core_sources(registry: Any | None = None) -> None:
    """The core's internet sources, beside its help ("core"): registered at
    start-up, under their own slugs, as the core's."""
    lookup.register(WikipediaSource(), slug=WIKIPEDIA_SLUG, core=True, registry=registry)
    lookup.register(OpenWebSource(), slug=WEB_SLUG, core=True, registry=registry)


# ─── The lookup ───────────────────────────────────────────────────────────


@dataclass
class LookupResult:
    """One result as every client gets it (the phone route's JSON, the
    chat's passages and its sources line)."""

    title: str
    url: str | None
    snippet: str
    source: str
    source_name: str
    source_class: str
    site: str = ""
    licence: str | None = None
    #: The part of the source's text chosen for the question ("" when the
    #: source gives snippets only).
    extract: str = ""
    section: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "url": self.url, "site": self.site, "snippet": self.snippet,
            "extract": self.extract, "source": self.source, "source_name": self.source_name,
            "source_class": self.source_class, "licence": self.licence, "section": self.section,
        }

    @property
    def text(self) -> str:
        return self.extract or self.snippet


@dataclass
class LookupOutcome:
    kind: str                                   # "search" | "page"
    query: str
    results: list[LookupResult] = field(default_factory=list)
    #: Whether anything left the house for this lookup (a source that
    #: needs the internet was asked, or a page was fetched).
    internet_used: bool = False
    #: A plain sentence when something didn't work as set ("Brave Search
    #: refused your key, so this came from Wikipedia."), else None.
    notice: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "query": self.query, "results": [r.as_dict() for r in self.results],
                "internet_used": self.internet_used, "notice": self.notice}


def household_order() -> list[str]:
    return lookup.parse_household_order(getattr(_settings(), "lookup_source_order", ""))


async def run_lookup(
    query: str,
    *,
    internet: bool,
    limit: int = MAX_RESULTS,
    registry: Any | None = None,
    with_extracts: bool = True,
) -> LookupOutcome:
    """Search the sources for ``query``: local ones only unless ``internet``
    (the caller has the person's allow). At most ``limit`` (≤ 3) results,
    the sources asked in order and no further once there are enough: a
    question the offline library answers never reaches the internet. Raises
    :class:`egress.InternetTurnedOff` when ``internet`` is asked for while
    the household's answer is "never"."""
    from domovoi import lookup_answer

    query = " ".join(str(query or "").split())[:300]
    limit = max(1, min(int(limit), MAX_RESULTS))
    if internet and egress.internet_turned_off():
        raise egress.InternetTurnedOff("lookup")
    outcome = LookupOutcome(kind="search", query=query)
    if not query:
        return outcome
    classes = lookup.CLASS_ORDER if internet else (lookup.LOCAL,)
    sources = lookup.ordered_sources(registry, classes=classes, household_order=household_order())
    found: list[tuple[Any, lookup.LookupHit]] = []
    seen_urls: set[str] = set()
    for source in sources:
        if len(found) >= limit:
            break
        if lookup.needs_permission(source):
            outcome.internet_used = True
        try:
            raw = await asyncio.wait_for(source.search(query, limit=limit), lookup.SEARCH_TIMEOUT_SEC)
        except asyncio.CancelledError:
            raise
        except egress.InternetTurnedOff:
            continue
        except OwnSearchFailed as e:
            outcome.notice = f"{_sentence(str(e))}, so this came from Wikipedia and the other sources."
            log.info("lookup: the household's own search failed: %s", e)
            continue
        except Exception as e:  # noqa: BLE001 — a source's failure is its own
            log.warning("lookup source %s failed: %s", getattr(source, "slug", "?"), type(e).__name__)
            continue
        for h in (lookup._clean_hit(x) for x in (raw or [])[:lookup.MAX_HITS_PER_SOURCE]):
            if h is None or len(found) >= limit:
                continue
            key = (h.url or "").split("#")[0].rstrip("/")
            if key and key in seen_urls:
                continue
            if key:
                seen_urls.add(key)
            found.append((source, h))
    for source, h in found:
        extract = ""
        licence = h.licence
        if with_extracts and source.lookup_class != lookup.WEB:
            try:
                doc = await asyncio.wait_for(source.fetch(h.id), lookup.SEARCH_TIMEOUT_SEC)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the snippet still stands
                log.info("lookup source %s: fetch failed: %s", getattr(source, "slug", "?"), type(e).__name__)
                doc = None
            if doc is not None and doc.text:
                try:
                    extract = await _off_the_loop(_passage, doc.text, query, EXTRACT_MAX_CHARS)
                except asyncio.TimeoutError:
                    extract = ""
                licence = licence or getattr(doc, "licence", None)
        if not licence and is_wikipedia(h.url):
            licence = WIKIPEDIA_LICENCE
        outcome.results.append(LookupResult(
            title=h.title, url=h.url, snippet=h.snippet, source=getattr(source, "slug", ""),
            source_name=getattr(source, "name", ""), source_class=source.lookup_class,
            site=site_of(h.url) if source.lookup_class != lookup.LOCAL else "", licence=licence,
            extract=extract, section=h.section,
        ))
    log.info("lookup %r: %d result(s) from %s (internet %s)", said(query), len(outcome.results),
             ",".join(sorted({r.source for r in outcome.results})) or "nothing",
             "used" if outcome.internet_used else "not used")
    return outcome


def _sentence(text: str) -> str:
    t = (text or "").strip().rstrip(".")
    return t[:1].upper() + t[1:] if t else "Your own search didn't work"


_HTML_TYPES = ("text/html", "application/xhtml+xml")


async def read_page(url: str, *, query: str = "") -> LookupOutcome:
    """A page the person named: HTTPS only, no cookies, no JavaScript, at
    most :data:`PAGE_MAX_BYTES`, its main text kept (and the parts nearest
    ``query`` chosen as the extract). Raises :class:`LookupRefused`, or
    :class:`egress.InternetTurnedOff` under "never"."""
    import httpx

    from domovoi import lookup_answer, page_text

    url = str(url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme.lower() != "https" or not parts.hostname:
        raise LookupRefused("not_https", "Domovoi only reads pages whose address starts with https://.")
    if parts.username or parts.password:
        raise LookupRefused("bad_address", "That address carries a name or password; Domovoi won't send it.")
    egress.require_destination(url)
    outcome = LookupOutcome(kind="page", query=url, internet_used=True)
    headers = {"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC * 2, cookies=_no_cookies(), **_extra()) as client:
            res = await net_safety.fetch_bytes(url, max_bytes=PAGE_MAX_BYTES, headers=headers, client=client)
    except egress.InternetTurnedOff:
        raise
    except net_safety.ResponseTooLarge:
        raise LookupRefused("too_large", "That page is larger than 2 MB, so Domovoi didn't read it.")
    except net_safety.UnsafeOutboundURL:
        raise LookupRefused("not_public", "That address isn't a public web page Domovoi may read.")
    except net_safety.OutboundFetchError:
        raise LookupRefused("unreachable", "That page couldn't be reached.")
    except httpx.HTTPError:
        raise LookupRefused("unreachable", "That page couldn't be reached.")
    if res.status_code >= 400:
        raise LookupRefused("http_error", f"That site answered with an error ({res.status_code}).")
    final = res.url
    if urlsplit(final).scheme.lower() != "https":
        raise LookupRefused("not_https", "That page sent Domovoi to an address that isn't https://.")
    ctype = (res.content_type or "").split(";")[0].strip().lower()
    charset = "utf-8"
    m = re.search(r"charset=([A-Za-z0-9._-]+)", res.content_type or "")
    if m:
        charset = m.group(1)
    if ctype not in _HTML_TYPES and ctype != "text/plain" and ctype:
        raise LookupRefused("not_a_page", "That address isn't a web page Domovoi can read (only HTML and text).")
    try:
        from domovoi import page_worker

        title, text, extract = await page_worker.read_page_text(
            res.content, charset, ctype, query, EXTRACT_MAX_CHARS * 2,
        )
    except (asyncio.TimeoutError, TimeoutError):
        raise LookupRefused("too_slow", "That page took too long to read, so Domovoi stopped.")
    if text is None:
        raise LookupRefused("not_a_page", "That address isn't a web page Domovoi can read (only HTML and text).")
    if not text.strip():
        raise LookupRefused("empty", "That page has no text Domovoi could read (it may need JavaScript).")
    outcome.results.append(LookupResult(
        title=title or site_of(final), url=final, snippet=_clean(text, 300), source="page",
        source_name="the page you named", source_class=lookup.WEB, site=site_of(final),
        licence=WIKIPEDIA_LICENCE if is_wikipedia(final) else None, extract=extract,
    ))
    log.info("lookup: read a named page on %s (%d bytes)", site_of(final), len(res.content))
    return outcome


#: The longest choosing a passage from a source's text may take (linear and
#: capped, in this module's own small pool, never on the event loop).
TEXT_WORK_TIMEOUT_SEC = 5.0


async def _off_the_loop(fn: Any, *args: Any) -> Any:
    """``fn(*args)`` in the page worker's own thread pool, at most
    :data:`TEXT_WORK_TIMEOUT_SEC`. Only for work that is linear and capped
    (a passage): a page's HTML goes to a child process instead
    (:mod:`domovoi.page_worker`), which is killed when it runs over."""
    from domovoi import page_worker

    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(page_worker._POOL, fn, *args), TEXT_WORK_TIMEOUT_SEC)


def _passage(text: str, query: str, max_chars: int) -> str:
    from domovoi import lookup_answer

    return lookup_answer.best_passage(text, query, max_chars=max_chars)


def _read_text(content: bytes, charset: str, ctype: str, query: str) -> tuple[str, str | None, str]:
    """(title, text or None for not a page, extract) of a fetched page, in
    this process (what the child runs: :func:`domovoi.page_worker.read_text`)."""
    from domovoi import page_worker

    return page_worker.read_text(content, charset, ctype, query, EXTRACT_MAX_CHARS * 2)


# ─── The Test button (Settings → Search → Use your own search) ────────────


async def test_own_search(kind: str, *, key: str | None = None, url: str | None = None) -> dict[str, Any]:
    """One search of a fixed word with the household's own search (or the
    key/address typed): ``{result: works|refused|unreachable|error,
    detail}``. Never echoes the key."""
    probe = "Wikipedia"
    try:
        if kind == "brave":
            items = await brave_search(probe, key or "", limit=1)
        elif kind == "searxng":
            problem = home_address.own_search_address_problem(url or "")
            if problem:
                return {"result": "refused", "detail": _sentence(problem) + "."}
            status, items = await searxng_search(url or "", probe, limit=1)
            if status in ("unreachable", "refused", "error"):
                return {"result": "refused" if status == "refused" else status,
                        "detail": {"unreachable": "Nothing answered at that address.",
                                   "refused": "That SearXNG refused the search: turn its JSON output on "
                                              "(search.formats: json in its settings.yml).",
                                   "error": "That SearXNG answered with an error."}[status]}
        else:
            raise ValueError("kind is brave or searxng")
    except OwnSearchFailed as e:
        text = str(e)
        result = "refused" if ("refused" in text or "limit" in text) else "unreachable"
        return {"result": result, "detail": _sentence(text) + "."}
    return {"result": "works", "detail": f"It works: {len(items)} result(s) for a test search."}


# ─── A pace for the route (it reaches the internet for whoever asks) ──────


class Pace:
    """At most ``per_minute`` lookups a minute for everyone together, and
    ``per_device`` for any one caller (by :func:`caller_key`), so one device
    can't use up the house's minute. A refused try isn't counted."""

    def __init__(self, per_minute: int = 30, per_device: int = 10) -> None:
        self.per_minute = per_minute
        self.per_device = per_device
        self._times: list[float] = []
        self._by: dict[str, list[float]] = {}

    def allow(self, now: float | None = None, key: str = "") -> bool:
        now = time.monotonic() if now is None else now
        self._times = [t for t in self._times if now - t < 60.0]
        mine = [t for t in self._by.get(key, []) if now - t < 60.0]
        if len(self._times) >= self.per_minute or (key and len(mine) >= self.per_device):
            if key:
                self._by[key] = mine
            return False
        self._times.append(now)
        if key:
            mine.append(now)
            self._by[key] = mine
            if len(self._by) > 512:  # forget callers quiet for a minute
                self._by = {k: v for k, v in self._by.items() if v and now - v[-1] < 60.0}
        return True


PACE = Pace()


def caller_key(headers: Any, client_host: str | None) -> str:
    """Who is asking, for the pace: a digest of the credential the caller
    sent, normalised first (a device token in its canonical form, a Bearer
    without its scheme's case or spacing), so spelling it differently is the
    same caller; never the credential itself. Else the address the request
    came from (the web's hop forwards the browser's).

    "Per caller" is per credential: phones and browsers that share the
    household token share its minute (the device id they may send is
    self-asserted, so it isn't trusted to split it)."""
    import hashlib

    get = (lambda n: (headers.get(n) or "")) if headers is not None else (lambda n: "")
    token = get("x-device-token").strip()
    if token:
        try:
            from domovoi.admin_auth import normalize_device_token

            token = normalize_device_token(token) or token.lower()
        except Exception:  # noqa: BLE001 — the raw value, lower-cased
            token = token.lower()
        return "d" + hashlib.sha256(token.encode()).hexdigest()[:16]
    auth = " ".join(get("authorization").split())
    if auth:
        scheme, _, value = auth.partition(" ")
        cred = value.strip() if scheme.lower() == "bearer" else auth
        return "b" + hashlib.sha256(cred.encode()).hexdigest()[:16]
    fwd = get("x-forwarded-for").split(",")[0].strip()
    return "a" + (fwd or client_host or "?")


__all__ = [
    "LookupOutcome",
    "LookupRefused",
    "LookupResult",
    "OpenWebSource",
    "OwnSearchFailed",
    "WikipediaSource",
    "destination",
    "read_page",
    "register_core_sources",
    "run_lookup",
    "searxng_search",
    "server_agent",
    "site_of",
    "test_own_search",
    "user_agent",
]
