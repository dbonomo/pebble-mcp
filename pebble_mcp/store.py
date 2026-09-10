"""HTTP client for the Rebble/Pebble appstore API (``appstore-api.repebble.com``).

Covers every documented read endpoint: apps by id, bulk fetch, apps by
developer, apps by category, apps by collection (e.g. ``most-loved``
watchfaces), and home rows. All requests are plain HTTPS GET/POST, no auth
required.

**Why stdlib ``urllib`` and not ``httpx``/``requests``:** Tier 1 (this
module) is supposed to work in *any* MCP host with nothing but `pip install
mcp`-equivalent — no extra install step, no compiled wheels, nothing that
can fail on an exotic platform. ``urllib.request`` ships with every Python
3.13, does everything we need here (JSON GET/POST over HTTPS, custom
headers, timeouts), and keeps the dependency footprint at zero for the
appstore client. If a future workstream needs connection pooling or async
fan-out, that can layer `httpx` in as an optional extra without touching
this module's public API.

**Why a custom User-Agent:** default Python UAs (``Python-urllib/3.x``) get
403'd by some edge/CDN configurations fronting Rebble-adjacent hosts. We
send a descriptive one unconditionally so a future change on their end
doesn't quietly break us.

The live API returns structured JSON error bodies (``{"error": "..."}"``)
for both 404s (unknown id/collection/etc.) and 400s (invalid parameter,
e.g. an unrecognized collection ``typeString``), so error handling parses
those bodies where present rather than treating every non-2xx as opaque.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE_URL = "https://appstore-api.repebble.com"
USER_AGENT = "pebble-mcp/0.1 (+https://github.com/pebble-dev; appstore client)"
DEFAULT_TIMEOUT = 15.0

# --------------------------------------------------------------------------- #
# Title search
# --------------------------------------------------------------------------- #
# The appstore REST API (appstore-api.repebble.com) has no text-search route --
# every /api/v1/apps/search-ish path returns the store front-end's 404 page.
# The *store website* (apps.repebble.com/search) instead queries a hosted
# Algolia index directly from the browser, with a public search-only key baked
# into its JavaScript bundle; that is the only real title search the ecosystem
# exposes today, so we use the same public endpoint the official web client
# does. Confirmed live: a query for "2048 touch" returns store id
# 6df87b64b7174448a065ef54 as the top hit -- an app that is invisible to a scan
# of the all/most-loved collections.
#
# The index records are close to the REST API's app shape (id/title/author/
# type/hearts/compatibility/uuid/source/website/version), so parse_app() reads
# them directly. They do NOT carry latest_release, so a search hit has no .pbw
# URL -- follow up with get_app(id) for that.
#
# Keys are public, client-side, read-only credentials (the same pair any
# visitor to the store site receives); they are not secrets. If Rebble rotates
# them the search path fails cleanly and callers fall back to a listing scan.
DEFAULT_SEARCH_URL = "https://gm3s9tryo4-dsn.algolia.net/1/indexes/*/queries"
SEARCH_APP_ID = "GM3S9TRYO4"
SEARCH_API_KEY = "0b83b4f8e4e8e9793d2f1f93c21894aa"  # search-only, public
SEARCH_INDEX = "apps"
SEARCH_MAX_HITS_PER_PAGE = 50

# Map a collection ``type_string`` onto the index's app-kind tag. ``None``
# (search everything) is the useful default for "find me this app by name".
SEARCH_TAG_BY_TYPE: dict[str, str] = {
    "faces": "watchface",
    "watchfaces": "watchface",
    "apps": "watchapp",
    "watchapps-and-companions": "watchapp",
}

# The app-type "typeString" segment used by the collection/home endpoints.
# The live API only accepts these four values (confirmed against a running
# instance); "watchapps" (plural, unqualified) 400s with "Invalid app type".
COLLECTION_APP_TYPES = ("apps", "watchapps-and-companions", "faces", "watchfaces")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class StoreError(Exception):
    """Base class for all appstore-client errors. Never lets a raw traceback
    (urllib exception, JSONDecodeError, etc.) escape to the caller."""


class StoreNotFoundError(StoreError):
    """The requested app/developer/category/collection does not exist (HTTP 404)."""


class StoreBadRequestError(StoreError):
    """The request was rejected as malformed, e.g. an invalid ``typeString`` (HTTP 400)."""


class StoreRateLimitedError(StoreError):
    """The API returned HTTP 429; ``retry_after`` is seconds if the server sent one."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class StoreServerError(StoreError):
    """The API returned a 5xx status."""


class StoreResponseError(StoreError):
    """The API returned a 2xx status but the body wasn't the JSON shape we expected."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Release:
    """An app's release (usually just ``latest_release``)."""

    version: str
    published_date: str | None
    release_notes: str | None
    pbw_file: str | None
    js_version: int | None = None


@dataclass(frozen=True)
class PlatformSupport:
    """One hardware platform's build for an app, from ``hardware_platforms``."""

    name: str
    sdk_version: str | None
    icon_url: str | None
    list_icon_url: str | None
    screenshot_url: str | None


@dataclass(frozen=True)
class App:
    """A store app/watchface, normalized from the API's ``jsonify_app`` shape."""

    id: str
    title: str
    type: str  # "watchapp" | "watchface"
    author: str
    developer_id: str | None
    hearts: int
    description: str
    category: str | None
    category_id: str | None
    uuid: str | None
    website: str | None
    source: str | None
    screenshot_urls: list[str] = field(default_factory=list)
    header_image_urls: list[str] = field(default_factory=list)
    compatible_platforms: list[str] = field(default_factory=list)
    platforms: list[PlatformSupport] = field(default_factory=list)
    latest_release: Release | None = None
    changelog: list[Release] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def pbw_url(self) -> str | None:
        """Shortcut to the latest release's .pbw download URL, if any."""
        return self.latest_release.pbw_file if self.latest_release else None


@dataclass(frozen=True)
class Page:
    """A paginated list of apps, as returned by every list-shaped endpoint."""

    apps: list[App]
    limit: int
    offset: int
    next_page_url: str | None


@dataclass(frozen=True)
class SearchPage:
    """One page of title-search hits from the store's search index.

    ``apps`` are parsed with the same :func:`parse_app` as REST results, but
    index records carry no ``latest_release`` -- ``App.pbw_url`` is therefore
    ``None`` on a search hit; call :meth:`StoreClient.get_app` for the
    download URL.
    """

    apps: list[App]
    query: str
    page: int
    pages: int
    total_hits: int
    hits_per_page: int

    @property
    def has_more(self) -> bool:
        """True when further pages of hits exist beyond this one."""
        return self.page + 1 < self.pages


@dataclass(frozen=True)
class CategoryInfo:
    """A category summary row, as seen in home-row responses."""

    id: str
    name: str
    slug: str
    color: str | None
    apps_url: str | None


@dataclass(frozen=True)
class CollectionInfo:
    """A collection summary row (e.g. "Most Loved"), as seen in home-row responses."""

    name: str
    slug: str
    application_ids: list[str]
    apps_url: str | None


@dataclass(frozen=True)
class HomeRows:
    """The ``/api/v1/home/<type>`` response: banners, categories, collections,
    plus the union of apps referenced by them."""

    categories: list[CategoryInfo]
    collections: list[CollectionInfo]
    apps: list[App]
    banners: list[dict[str, Any]]


@dataclass(frozen=True)
class BulkResult:
    """Result of a bulk-by-id fetch: found apps plus any ids that didn't resolve."""

    apps: list[App]
    missing_ids: list[str]


# --------------------------------------------------------------------------- #
# Parsing helpers (API JSON -> dataclasses)
# --------------------------------------------------------------------------- #
# The appstore is not a trusted source: it (or anything MITM-ing the plain
# read endpoints) can return nulls where dicts are expected, wrong types
# (``hearts`` as a string, ``compatibility`` as a list), or a bare JSON array
# at the top level. The parsers below are written to *never* raise an
# AttributeError/TypeError on such input — a hostile body must surface as a
# typed :class:`StoreResponseError` (for structural surprises) or be quietly
# coerced/dropped (for field-level junk), never a raw traceback. Downstream
# code (search scoring, filename building) assumes ``title``/``author`` are
# always ``str`` and ``hearts`` is always ``int``; these helpers guarantee it.


def _as_str(value: Any) -> str:
    """Coerce an API field to ``str``. ``None``/missing -> ``""``; a non-string
    scalar is stringified so callers can always ``.lower()`` it safely."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _coerce_int(value: Any, default: int = 0) -> int:
    """Coerce an API field to ``int`` (e.g. ``hearts`` arriving as ``"50"``).
    Anything non-numeric -> ``default`` so sort keys never mix str and int."""
    if isinstance(value, bool):  # bool is an int subclass; treat as junk
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` if it's a dict, else ``{}`` — tolerates a null/list
    where the API contract says an object."""
    return value if isinstance(value, dict) else {}


def _parse_release(data: dict[str, Any] | None) -> Release | None:
    if not isinstance(data, dict) or not data:
        return None
    return Release(
        version=_as_str(data.get("version")),
        published_date=data.get("published_date"),
        release_notes=data.get("release_notes"),
        pbw_file=data.get("pbw_file"),
        js_version=data.get("js_version"),
    )


def _parse_changelog(entries: list[dict[str, Any]] | None) -> list[Release]:
    if not isinstance(entries, list):
        return []
    return [
        Release(
            version=_as_str(e.get("version")),
            published_date=e.get("published_date"),
            release_notes=e.get("release_notes"),
            pbw_file=e.get("pbw_file"),
        )
        for e in entries
        if isinstance(e, dict)
    ]


def _parse_platform(data: dict[str, Any]) -> PlatformSupport:
    images = _as_dict(data.get("images"))
    return PlatformSupport(
        name=_as_str(data.get("name")),
        sdk_version=data.get("sdk_version"),
        icon_url=images.get("icon") or None,
        list_icon_url=images.get("list") or None,
        screenshot_url=images.get("screenshot") or None,
    )


def _first_urls(entries: list[dict[str, str]] | None) -> list[str]:
    """Flatten a list of size-keyed image dicts (e.g. screenshot_images) into
    a flat list of URLs, dropping empty strings. Tolerates a non-list, or list
    members that aren't dicts, or non-string URL values (all skipped)."""
    urls: list[str] = []
    if not isinstance(entries, list):
        return urls
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for url in entry.values():
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def parse_app(data: dict[str, Any]) -> App:
    """Parse one app object from any endpoint's ``data``/``applications`` array.

    Defensive against hostile/malformed field types: string/list where a dict
    is expected, nulls, ``hearts`` as a string, etc. never raise here.
    """
    if not isinstance(data, dict):
        raise StoreResponseError(
            f"appstore API returned a malformed app record (expected a JSON object, "
            f"got {type(data).__name__}); this is a server-side response issue, not "
            "something your request can fix -- retry, and report if it persists"
        )
    compat = _as_dict(data.get("compatibility"))
    compatible = [
        name
        for name, spec in compat.items()
        if isinstance(spec, dict) and spec.get("supported")
    ]
    platforms_raw = data.get("hardware_platforms")
    platforms = [
        _parse_platform(p)
        for p in (platforms_raw if isinstance(platforms_raw, list) else [])
        if isinstance(p, dict)
    ]
    return App(
        id=_as_str(data.get("id")),
        title=_as_str(data.get("title")),
        type=_as_str(data.get("type")),
        author=_as_str(data.get("author")),
        developer_id=data.get("developer_id"),
        hearts=_coerce_int(data.get("hearts")),
        description=_as_str(data.get("description")),
        category=data.get("category"),
        category_id=data.get("category_id"),
        uuid=data.get("uuid"),
        website=data.get("website"),
        source=data.get("source"),
        screenshot_urls=_first_urls(data.get("screenshot_images")),
        header_image_urls=_first_urls(data.get("header_images")),
        compatible_platforms=compatible,
        platforms=platforms,
        latest_release=_parse_release(data.get("latest_release")),
        changelog=_parse_changelog(data.get("changelog")),
        raw=data,
    )


def _parse_page(data: dict[str, Any]) -> Page:
    if not isinstance(data, dict):
        raise StoreResponseError(
            f"appstore API returned a malformed listing page (expected a JSON object, "
            f"got {type(data).__name__}); this is a server-side response issue -- retry, "
            "and report if it persists"
        )
    raw_apps = data.get("data")
    apps = [
        parse_app(a)
        for a in (raw_apps if isinstance(raw_apps, list) else [])
        if isinstance(a, dict)
    ]
    links = _as_dict(data.get("links"))
    return Page(
        apps=apps,
        limit=_coerce_int(data.get("limit"), default=len(apps)),
        offset=_coerce_int(data.get("offset")),
        next_page_url=links.get("nextPage"),
    )


def _parse_search(data: Any, query: str) -> SearchPage:
    """Parse the search index's multi-query response into a :class:`SearchPage`.

    Shape is ``{"results": [{"hits": [...], "nbHits": n, "page": p,
    "nbPages": q, "hitsPerPage": h}]}``; we always send exactly one request,
    so we read ``results[0]``. As with every other parser here, a surprising
    body raises :class:`StoreResponseError` rather than an AttributeError.
    """
    if not isinstance(data, dict):
        raise StoreResponseError(
            f"store search returned a malformed response (expected a JSON object, "
            f"got {type(data).__name__}); retry, and if it persists fall back to "
            "browsing a collection or looking the app up by id"
        )
    results = data.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise StoreResponseError(
            "store search returned no results block; retry, and if it persists "
            "fall back to browsing a collection or looking the app up by id"
        )
    result = results[0]
    raw_hits = result.get("hits")
    apps = [
        parse_app(h)
        for h in (raw_hits if isinstance(raw_hits, list) else [])
        if isinstance(h, dict)
    ]
    return SearchPage(
        apps=apps,
        query=query,
        page=_coerce_int(result.get("page")),
        pages=_coerce_int(result.get("nbPages"), default=1),
        total_hits=_coerce_int(result.get("nbHits"), default=len(apps)),
        hits_per_page=_coerce_int(result.get("hitsPerPage"), default=len(apps)),
    )


def _parse_home(data: dict[str, Any]) -> HomeRows:
    if not isinstance(data, dict):
        raise StoreResponseError(
            f"appstore API returned a malformed home response (expected a JSON object, "
            f"got {type(data).__name__}); this is a server-side response issue -- retry, "
            "and report if it persists"
        )
    categories = [
        CategoryInfo(
            id=_as_str(c.get("id")),
            name=_as_str(c.get("name")),
            slug=_as_str(c.get("slug")),
            color=c.get("color"),
            apps_url=_as_dict(c.get("links")).get("apps"),
        )
        for c in (data.get("categories") or [])
        if isinstance(c, dict)
    ]
    collections = [
        CollectionInfo(
            name=_as_str(c.get("name")),
            slug=_as_str(c.get("slug")),
            application_ids=[i for i in (c.get("application_ids") or []) if isinstance(i, str)],
            apps_url=_as_dict(c.get("links")).get("apps"),
        )
        for c in (data.get("collections") or [])
        if isinstance(c, dict)
    ]
    raw_apps = data.get("applications")
    apps = [
        parse_app(a)
        for a in (raw_apps if isinstance(raw_apps, list) else [])
        if isinstance(a, dict)
    ]
    banners = [b for b in (data.get("banners") or []) if isinstance(b, dict)]
    return HomeRows(
        categories=categories,
        collections=collections,
        apps=apps,
        banners=banners,
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
# A transport takes (method, url, body_bytes_or_None) and returns
# (status_code, response_body_bytes, headers_dict). Isolating urlopen behind
# this makes the client fully testable against recorded fixtures with no
# network access.
Transport = Any  # Callable[[str, str, bytes | None], tuple[int, bytes, dict[str, str]]]


def urllib_transport(
    method: str, url: str, body: bytes | None, timeout: float
) -> tuple[int, bytes, dict[str, str]]:
    """Default transport: actually hit the network via ``urllib.request``."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except urllib.error.URLError as e:
        raise StoreError(f"network error contacting appstore: {e.reason}") from e
    except TimeoutError as e:
        raise StoreError(f"appstore request timed out: {e}") from e


class StoreClient:
    """Client for the read-only Rebble/Pebble appstore API.

    All methods raise a :class:`StoreError` subclass on failure and never let
    a raw ``urllib``/``json`` exception escape.

    **Parameter-validation philosophy:** we validate locally *only* the
    parameters this client itself interprets — the collection/home
    ``type_string`` (checked against :data:`COLLECTION_APP_TYPES`, since the
    live API 400s on anything else and a local check saves a round trip).
    Everything the *API* is the authority on — ``hardware`` platform names,
    ``limit``/``offset`` bounds, ``sort`` keys, slugs/ids — is passed through
    verbatim and the server decides. A nonsensical ``limit=-5`` or
    ``hardware=nonsense`` therefore reaches the API as-is rather than being
    second-guessed here; that keeps this client from drifting out of sync with
    server-side rules it doesn't own.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        search_url: str = DEFAULT_SEARCH_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport = urllib_transport,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.search_url = search_url
        self.timeout = timeout
        self._transport = transport

    # -- low-level request/response plumbing -------------------------------- #
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        absolute_url: str | None = None,
    ) -> Any:
        # ``absolute_url`` escapes ``base_url`` for the one endpoint that lives
        # on a different host (the search index); ``path`` is still used for
        # error messages so failures name the operation, not the vendor URL.
        url = absolute_url if absolute_url is not None else self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        status, raw, headers = self._transport(method, url, body, self.timeout)
        return self._handle_response(status, raw, headers, method, path)

    def _handle_response(
        self, status: int, raw: bytes, headers: dict[str, str], method: str, path: str
    ) -> Any:
        error_message = self._error_message(raw)

        if status == 429:
            retry_after = None
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra is not None:
                try:
                    retry_after = float(ra)
                except ValueError:
                    retry_after = None
            raise StoreRateLimitedError(
                error_message or f"{method} {path}: rate limited", retry_after=retry_after
            )
        if status == 404:
            raise StoreNotFoundError(error_message or f"{method} {path}: not found")
        if status == 400:
            raise StoreBadRequestError(error_message or f"{method} {path}: bad request")
        if 400 <= status < 500:
            raise StoreError(error_message or f"{method} {path}: HTTP {status}")
        if status >= 500:
            raise StoreServerError(error_message or f"{method} {path}: server error ({status})")

        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise StoreResponseError(
                f"{method} {path}: response was not valid JSON ({e})"
            ) from e

    @staticmethod
    def _error_message(raw: bytes) -> str | None:
        """Best-effort extraction of the API's ``{"error": "..."}"`` body.
        Returns None if the body isn't that shape (e.g. an HTML error page
        from an edge proxy) so callers fall back to a generic message."""
        if not raw:
            return None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            return body["error"]
        # The search index reports failures as {"message": "...", "status": n}
        # rather than {"error": ...}; surface those too.
        if isinstance(body, dict) and isinstance(body.get("message"), str):
            return body["message"]
        return None

    # -- public read endpoints ------------------------------------------------ #
    def get_app(self, app_id: str, *, hardware: str | None = None) -> App:
        """``GET /api/v1/apps/id/<id>`` — full metadata for one app.

        Note the live endpoint actually returns a one-item ``data`` page (not
        a bare object), presumably so it shares ``generate_app_response``
        with the list endpoints; this method unwraps that for callers.
        """
        data = self._request(
            "GET",
            f"/api/v1/apps/id/{urllib.parse.quote(app_id, safe='')}",
            params={"hardware": hardware},
        )
        page = _parse_page(data)
        if not page.apps:
            raise StoreNotFoundError(f"app {app_id!r} not found")
        return page.apps[0]

    def search_apps(
        self,
        query: str,
        *,
        type_string: str | None = None,
        limit: int = 20,
        page: int = 0,
    ) -> SearchPage:
        """Title/keyword search against the store's hosted search index.

        This is a *real* server-side search over the whole catalog -- unlike
        a client-side scan of the ``all``/``most-loved`` collections, it finds
        low-heart and long-tail apps. See the module-level notes on
        :data:`DEFAULT_SEARCH_URL` for why this endpoint (and not the REST
        API) is what the official store website itself uses.

        ``type_string`` accepts the same values as the collection endpoints
        and filters to watchfaces or watchapps; ``None`` (the default)
        searches both. Hits carry no ``latest_release`` -- call
        :meth:`get_app` for a download URL.
        """
        tag: str | None = None
        if type_string is not None:
            tag = SEARCH_TAG_BY_TYPE.get(type_string)
            if tag is None:
                raise StoreBadRequestError(
                    f"invalid search type_string {type_string!r}; expected one of "
                    f"{tuple(SEARCH_TAG_BY_TYPE)} or None to search both"
                )
        request: dict[str, Any] = {
            "indexName": SEARCH_INDEX,
            "query": query,
            "hitsPerPage": max(1, min(int(limit), SEARCH_MAX_HITS_PER_PAGE)),
            "page": max(0, int(page)),
        }
        if tag is not None:
            request["tagFilters"] = [[tag]]
        url = self.search_url + "?" + urllib.parse.urlencode(
            {
                "x-algolia-api-key": SEARCH_API_KEY,
                "x-algolia-application-id": SEARCH_APP_ID,
            }
        )
        data = self._request(
            "POST",
            "/store-search",
            absolute_url=url,
            json_body={"requests": [request]},
        )
        return _parse_search(data, query)

    def get_apps_bulk(self, app_ids: list[str]) -> BulkResult:
        """``POST /api/v1/apps/bulk`` — fetch many apps by id in one call.

        Unknown ids are reported in ``missing_ids`` rather than raising —
        this endpoint is explicitly designed for "give me what you have".
        """
        if not app_ids:
            return BulkResult(apps=[], missing_ids=[])
        data = self._request("POST", "/api/v1/apps/bulk", json_body={"ids": list(app_ids)})
        if not isinstance(data, dict):
            raise StoreResponseError(
                f"appstore API returned a malformed bulk response (expected a JSON "
                f"object, got {type(data).__name__}); this is a server-side response "
                "issue -- retry, and report if it persists"
            )
        raw_apps = data.get("data")
        apps = [
            parse_app(a)
            for a in (raw_apps if isinstance(raw_apps, list) else [])
            if isinstance(a, dict)
        ]
        missing = [m for m in (data.get("missing") or []) if isinstance(m, str)]
        return BulkResult(apps=apps, missing_ids=missing)

    def get_apps_by_developer(
        self,
        developer_id: str,
        *,
        hardware: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
    ) -> Page:
        """``GET /api/v1/apps/dev/<developer_id>`` — an author's published apps."""
        return _parse_page(
            self._request(
                "GET",
                f"/api/v1/apps/dev/{urllib.parse.quote(developer_id, safe='')}",
                params={"hardware": hardware, "limit": limit, "offset": offset, "sort": sort},
            )
        )

    def get_apps_by_category(
        self,
        category_slug: str,
        *,
        hardware: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
    ) -> Page:
        """``GET /api/v1/apps/category/<slug>`` — e.g. ``faces``, ``games``."""
        return _parse_page(
            self._request(
                "GET",
                f"/api/v1/apps/category/{urllib.parse.quote(category_slug, safe='')}",
                params={"hardware": hardware, "limit": limit, "offset": offset, "sort": sort},
            )
        )

    def get_apps_by_collection(
        self,
        slug: str,
        type_string: str,
        *,
        hardware: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
    ) -> Page:
        """``GET /api/v1/apps/collection/<slug>/<type_string>``.

        ``slug`` examples: ``most-loved``, ``all``, ``recently-updated``, or
        a named collection slug from a home-row response. ``type_string``
        must be one of :data:`COLLECTION_APP_TYPES` (``apps``,
        ``watchapps-and-companions``, ``faces``, ``watchfaces``) — the live
        API 400s with ``"Invalid app type"`` for anything else, so we raise
        :class:`StoreBadRequestError` locally before making the request.
        """
        if type_string not in COLLECTION_APP_TYPES:
            raise StoreBadRequestError(
                f"invalid collection type_string {type_string!r}; "
                f"expected one of {COLLECTION_APP_TYPES}"
            )
        return _parse_page(
            self._request(
                "GET",
                f"/api/v1/apps/collection/{urllib.parse.quote(slug, safe='')}"
                f"/{urllib.parse.quote(type_string, safe='')}",
                params={"hardware": hardware, "limit": limit, "offset": offset, "sort": sort},
            )
        )

    def get_home(self, home_type: str, *, hardware: str | None = None) -> HomeRows:
        """``GET /api/v1/home/<home_type>`` — the appstore home-page rows
        (banners, categories, collections) plus every app they reference.

        ``home_type`` takes the same values as a collection ``type_string``
        (``apps``/``watchapps-and-companions``/``faces``/``watchfaces``).
        """
        if home_type not in COLLECTION_APP_TYPES:
            raise StoreBadRequestError(
                f"invalid home_type {home_type!r}; expected one of {COLLECTION_APP_TYPES}"
            )
        data = self._request(
            "GET",
            f"/api/v1/home/{urllib.parse.quote(home_type, safe='')}",
            params={"hardware": hardware},
        )
        return _parse_home(data)
