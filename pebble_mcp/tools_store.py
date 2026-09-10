"""Tier 1 appstore MCP tools — thin, JSON-safe wrappers over
:mod:`pebble_mcp.store`.

This module owns the *tool surface* for the Rebble/Pebble appstore: it maps
each read capability of :class:`~pebble_mcp.store.StoreClient` to an
``@mcp.tool()`` and shapes the client's rich dataclasses down to plain
``dict``/``list`` values a model can consume directly. Nothing here talks to
the network on its own — every call goes through the injected client, whose
transport is stubbable for tests.

Registration is deferred: :func:`register` attaches the tools to a caller's
``FastMCP`` instance (``server.py`` wires this after merge), so this module
never imports or mutates the server object.

**Error contract:** all failures raised by ``store.py`` are
:class:`~pebble_mcp.store.StoreError` subclasses carrying a human-readable
message. FastMCP turns any exception raised inside a tool into a
``ToolError`` whose text is that message — so we let store errors propagate
unchanged and the model sees a clear string, never a traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pebble_mcp.store import (
    COLLECTION_APP_TYPES,
    App,
    StoreClient,
    StoreError,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

# store_search accepts every collection type_string plus "any" (both kinds),
# which is the default: "find me the app called X" should not require the
# caller to already know whether X is a watchface or a watchapp.
SEARCH_TYPE_STRINGS = ("any", *COLLECTION_APP_TYPES)
_SEARCH_MAX_RESULTS = 50

# Fallback (listing-scan) knobs, used only when the store's search index is
# unreachable. The REST API itself has no text-search route, so this scans
# store *listings*; these bound how much we page.
_SEARCH_POOL_SLUGS = ("all", "most-loved")
_SEARCH_PAGE_SIZE = 50
_SEARCH_MAX_PAGES = 4  # per pool slug -> at most 200 candidates per pool
# Which collections a scan walks when the caller didn't narrow the type.
_SCAN_TYPES_FOR_ANY = ("watchfaces", "watchapps-and-companions")

# Escape hatch printed alongside thin/empty results: every store listing URL
# ends in the 24-hex id these tools take.
_BY_ID_HINT = (
    "Nothing (or not what you wanted)? Every app is reachable by id: a store "
    "URL like https://apps.repebble.com/2048-touch_6df87b64b7174448a065ef54 "
    "ends in the 24-hex-character id. Pass that to store_app for full "
    "metadata or straight to store_download_pbw to fetch the .pbw."
)


# --------------------------------------------------------------------------- #
# Shaping helpers: store dataclasses -> plain JSON-safe dicts
# --------------------------------------------------------------------------- #
def _summary(app: App, hardware: str) -> dict[str, Any]:
    """Compact one-line-ish summary row for listing/search results.

    ``compatible`` is the platform-compat flag: whether ``app`` supports the
    ``hardware`` the listing was requested for.
    """
    return {
        "id": app.id,
        "title": app.title,
        "type": app.type,
        "author": app.author,
        "hearts": app.hearts,
        "hardware": hardware,
        "compatible": hardware in app.compatible_platforms,
    }


def _release_dict(app: App) -> dict[str, Any] | None:
    rel = app.latest_release
    if rel is None:
        return None
    return {
        "version": rel.version,
        "published_date": rel.published_date,
        "release_notes": rel.release_notes,
        "pbw_file": rel.pbw_file,
    }


def _full_app(app: App) -> dict[str, Any]:
    """Full-metadata shape for store_app: hearts, platforms, pbw URL, etc."""
    return {
        "id": app.id,
        "title": app.title,
        "type": app.type,
        "author": app.author,
        "developer_id": app.developer_id,
        "hearts": app.hearts,
        "description": app.description,
        "category": app.category,
        "category_id": app.category_id,
        "uuid": app.uuid,
        "website": app.website,
        "source": app.source,
        "compatible_platforms": list(app.compatible_platforms),
        "hardware_platforms": [p.name for p in app.platforms],
        "has_emery": "emery" in app.compatible_platforms,
        "pbw_url": app.pbw_url,
        "latest_release": _release_dict(app),
        "screenshot_urls": list(app.screenshot_urls),
        "header_image_urls": list(app.header_image_urls),
    }


def _page_dict(page: Any, hardware: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shape a :class:`~pebble_mcp.store.Page` into a listing response."""
    out: dict[str, Any] = {
        "hardware": hardware,
        "limit": page.limit,
        "offset": page.offset,
        "count": len(page.apps),
        "has_more": page.next_page_url is not None,
        "apps": [_summary(a, hardware) for a in page.apps],
    }
    if extra:
        out = {**extra, **out}
    return out


# --------------------------------------------------------------------------- #
# Implementations (client passed in -> fully offline-testable)
# --------------------------------------------------------------------------- #
def _store_app(client: StoreClient, app_id: str) -> dict[str, Any]:
    return _full_app(client.get_app(app_id))


def _store_collection(
    client: StoreClient,
    slug: str,
    type_string: str,
    hardware: str,
    limit: int,
    offset: int,
    sort: str | None,
) -> dict[str, Any]:
    page = client.get_apps_by_collection(
        slug, type_string, hardware=hardware, limit=limit, offset=offset, sort=sort
    )
    return _page_dict(page, hardware, extra={"slug": slug, "type": type_string})


def _store_category(
    client: StoreClient,
    category_slug: str,
    hardware: str,
    limit: int,
    offset: int,
    sort: str | None,
) -> dict[str, Any]:
    page = client.get_apps_by_category(
        category_slug, hardware=hardware, limit=limit, offset=offset, sort=sort
    )
    return _page_dict(page, hardware, extra={"category": category_slug})


def _store_developer(
    client: StoreClient,
    developer_id: str,
    hardware: str,
    limit: int,
    offset: int,
    sort: str | None,
) -> dict[str, Any]:
    page = client.get_apps_by_developer(
        developer_id, hardware=hardware, limit=limit, offset=offset, sort=sort
    )
    return _page_dict(page, hardware, extra={"developer_id": developer_id})


def _score(app: App, query: str, tokens: list[str]) -> int:
    """Rank an app against the query by case-insensitive substring/token
    matches on title + author + description. Higher is better; 0 = no match."""
    title = app.title.lower()
    author = app.author.lower()
    desc = app.description.lower()
    score = 0
    if query and query in title:
        score += 10  # whole-query title hit is the strongest signal
    if query and query in author:
        score += 4
    for tok in tokens:
        if tok in title:
            score += 3
        if tok in author:
            score += 2
        if tok in desc:
            score += 1
    return score


def _search_index(
    client: StoreClient,
    query: str,
    type_string: str,
    hardware: str,
    max_results: int,
) -> dict[str, Any]:
    """Real catalog search via the store's hosted search index (preferred path).

    Covers the whole store, not just the collection listings, so long-tail and
    low-heart apps are findable by name. Ranking is the index's own relevance
    order; we do not re-sort it.
    """
    page = client.search_apps(
        query,
        type_string=None if type_string == "any" else type_string,
        limit=max_results,
    )
    results = [_summary(a, hardware) for a in page.apps]
    out: dict[str, Any] = {
        "query": query,
        "type": type_string,
        "hardware": hardware,
        "method": "store search index (whole catalog, server-side relevance)",
        "total_hits": page.total_hits,
        "returned": len(results),
        "result_count": len(results),
        "has_more": page.has_more,
        "results": results,
    }
    if page.has_more:
        out["more_hint"] = (
            f"{page.total_hits} apps matched; raise max_results (up to "
            f"{_SEARCH_MAX_RESULTS}) or use a more specific query."
        )
    if not results:
        out["next_step"] = _BY_ID_HINT
    return out


def _search_scan(
    client: StoreClient,
    query: str,
    type_string: str,
    hardware: str,
    max_results: int,
) -> dict[str, Any]:
    """Fallback: client-side ranking over a bounded scan of store listings.

    Only reached when the search index is unreachable. Coverage is limited to
    what the ``all``/``most-loved`` collections return, so an app outside
    those pools is invisible here — hence the by-id escape hatch in the return.
    """
    scan_types = _SCAN_TYPES_FOR_ANY if type_string == "any" else (type_string,)

    # Gather a candidate pool by paging the relevant listings. Dedupe by id;
    # first sighting wins (identical app across pools is the same object).
    pool: dict[str, App] = {}
    for scan_type in scan_types:
        for slug in _SEARCH_POOL_SLUGS:
            for page_i in range(_SEARCH_MAX_PAGES):
                offset = page_i * _SEARCH_PAGE_SIZE
                page = client.get_apps_by_collection(
                    slug, scan_type, hardware=hardware, limit=_SEARCH_PAGE_SIZE, offset=offset
                )
                for app in page.apps:
                    pool.setdefault(app.id, app)
                if page.next_page_url is None or not page.apps:
                    break

    q = query.strip().lower()
    tokens = [t for t in q.split() if t]
    scored = []
    for app in pool.values():
        s = _score(app, q, tokens)
        if s > 0:
            scored.append((s, app))
    # Rank by score, break ties by hearts (popularity), then title for stability.
    scored.sort(key=lambda pair: (pair[0], pair[1].hearts, pair[1].title), reverse=True)

    results = []
    for s, app in scored[: max(0, max_results)]:
        row = _summary(app, hardware)
        row["score"] = s
        results.append(row)

    return {
        "query": query,
        "type": type_string,
        "hardware": hardware,
        "method": "client-side listing scan (fallback — search index unreachable)",
        "candidates_scanned": len(pool),
        "returned": len(results),
        "result_count": len(results),
        "has_more": False,
        "results": results,
        "next_step": _BY_ID_HINT,
    }


def _store_search(
    client: StoreClient,
    query: str,
    type_string: str,
    hardware: str,
    max_results: int,
) -> dict[str, Any]:
    """Search the store: index first, bounded listing scan if that's down."""
    if type_string not in SEARCH_TYPE_STRINGS:
        raise StoreError(
            f"invalid type_string {type_string!r}; expected one of {SEARCH_TYPE_STRINGS}"
        )
    max_results = max(1, min(int(max_results), _SEARCH_MAX_RESULTS))
    try:
        return _search_index(client, query, type_string, hardware, max_results)
    except StoreError as e:
        # The index is a third-party host; if it 4xx/5xx/times out we still owe
        # the caller an answer rather than an exception. Say which path ran.
        out = _search_scan(client, query, type_string, hardware, max_results)
        out["index_error"] = f"{type(e).__name__}: {e}"[:300]
        return out


def _store_compare(client: StoreClient, app_ids: list[str]) -> dict[str, Any]:
    bulk = client.get_apps_bulk(list(app_ids))
    rows = []
    for app in bulk.apps:
        rel = app.latest_release
        rows.append(
            {
                "id": app.id,
                "title": app.title,
                "type": app.type,
                "hearts": app.hearts,
                "platforms": list(app.compatible_platforms),
                "has_emery": "emery" in app.compatible_platforms,
                "latest_release_date": rel.published_date if rel else None,
                "pbw_url": app.pbw_url,
            }
        )
    return {
        "requested": list(app_ids),
        "found_count": len(rows),
        "missing_ids": list(bulk.missing_ids),
        "columns": [
            "id",
            "title",
            "type",
            "hearts",
            "platforms",
            "has_emery",
            "latest_release_date",
            "pbw_url",
        ],
        "rows": rows,
    }


# Hard cap on a downloaded .pbw. Typical Pebble apps are small — a single-
# platform watchface is tens of KB — but a fat multi-platform build with
# bitmap resources for all seven platforms runs to a few MB, so the old
# "well under a megabyte" rule of thumb was wrong. 32 MiB still leaves an
# order of magnitude of headroom over the largest real .pbw while refusing a
# hostile/mistaken endpoint that would stream gigabytes
# into memory and onto disk. The transport hands us the whole body at once, so
# this bounds what we *persist* rather than what we buffer — a stricter,
# streaming limit would require changing the shared transport signature.
_MAX_PBW_BYTES = 32 * 1024 * 1024


def _sanitize_filename(name: str) -> str:
    """Reduce API-derived text to a safe single-path-component basename.

    The download filename is built from ``app.id`` / the release version /
    the pbw URL's last segment — all of which come straight from the (untrusted)
    appstore response. A hostile ``id`` like ``../../etc/cron.d/x`` would
    otherwise let the server steer the write outside ``dest_dir``. We strip any
    path separators, keep only the final segment, and drop leading dots so the
    result can never traverse upward or write a hidden file.
    """
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = name.lstrip(".").strip() or "download"
    if not name.endswith(".pbw"):
        name += ".pbw"
    return name


def _pbw_filename(app: App) -> str:
    """Pick a sensible, path-safe on-disk name for the downloaded .pbw."""
    url = app.pbw_url or ""
    base = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if base.endswith(".pbw"):
        return _sanitize_filename(f"{app.id}-{base}")
    ver = app.latest_release.version if app.latest_release else "latest"
    return _sanitize_filename(f"{app.id}-{ver}.pbw")


def _store_download_pbw(client: StoreClient, app_id: str, dest_dir: str) -> dict[str, Any]:
    app = client.get_app(app_id)
    url = app.pbw_url
    if not url:
        raise StoreError(f"app {app_id!r} has no downloadable .pbw (no latest release)")

    # Fail fast if dest_dir is unusable before spending a download on it.
    dest = Path(dest_dir)
    if dest.exists() and not dest.is_dir():
        raise StoreError(f"dest_dir {dest_dir!r} exists and is not a directory")
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StoreError(f"could not create dest_dir {dest_dir!r}: {e}") from e

    # Reuse the client's transport: for real use that's urllib_transport, which
    # already sends the polite pebble-mcp User-Agent; for tests it's the stub.
    status, raw, _headers = client._transport("GET", url, None, client.timeout)
    if status != 200:
        raise StoreError(f"pbw download failed for {app_id!r}: HTTP {status} from {url}")
    if not raw:
        raise StoreError(f"pbw download for {app_id!r} returned an empty (0-byte) body")
    if len(raw) > _MAX_PBW_BYTES:
        raise StoreError(
            f"pbw for {app_id!r} is {len(raw)} bytes, exceeding the "
            f"{_MAX_PBW_BYTES}-byte ({_MAX_PBW_BYTES // (1024 * 1024)} MiB) safety cap"
        )

    path = dest / _pbw_filename(app)
    # Belt-and-suspenders: even after sanitizing the filename, verify the
    # resolved target really lands inside dest_dir before writing.
    if not path.resolve().is_relative_to(dest.resolve()):
        raise StoreError(f"refusing to write pbw outside dest_dir (computed {path!s})")
    path.write_bytes(raw)
    return {
        "app_id": app.id,
        "title": app.title,
        "version": app.latest_release.version if app.latest_release else None,
        "pbw_url": url,
        "path": str(path),
        "bytes": len(raw),
    }


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def _new_client() -> StoreClient:
    """Construct the default (network-backed) appstore client."""
    return StoreClient()


def register(mcp: FastMCP) -> None:
    """Register the Tier 1 appstore tools on ``mcp``.

    Called by the server after construction. Each tool closes over a single
    shared :class:`~pebble_mcp.store.StoreClient` with the default urllib
    transport (which sends the polite pebble-mcp User-Agent).
    """
    client = _new_client()

    @mcp.tool()
    def store_app(app_id: str) -> dict[str, Any]:
        """Full metadata for one appstore app or watchface by its id.

        This is the by-id lookup every other store tool funnels into, and the
        fastest path when you already know (or can see) the app: a store
        listing URL ends in the id, e.g.
        ``https://apps.repebble.com/2048-touch_6df87b64b7174448a065ef54`` ->
        ``6df87b64b7174448a065ef54``. Ids are 24 hex characters; ``store_search``
        and every listing tool return them in ``id``.

        Returns hearts, author, description, the list of compatible hardware
        platforms (with a ``has_emery`` convenience flag), the latest release
        (version + date + notes), the direct ``.pbw`` download URL, and
        screenshot/header image URLs. Pass the same id to
        ``store_download_pbw`` to fetch the build.
        """
        return _store_app(client, app_id)

    @mcp.tool()
    def store_collection(
        slug: str,
        type_string: str,
        hardware: str = "emery",
        limit: int = 20,
        offset: int = 0,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """List a named store collection, paginated, as compact summaries.

        ``slug`` examples: ``most-loved``, ``all``, ``recently-updated``, or a
        collection slug from a home row. ``type_string`` must be one of
        ``apps``, ``watchapps-and-companions``, ``faces``, ``watchfaces``.
        Each result row is id/title/type/author/hearts plus a ``compatible``
        flag for the requested ``hardware``. ``has_more`` indicates further
        pages; raise ``offset`` by ``limit`` to page.
        """
        return _store_collection(client, slug, type_string, hardware, limit, offset, sort)

    @mcp.tool()
    def store_category(
        category_slug: str,
        hardware: str = "emery",
        limit: int = 20,
        offset: int = 0,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """List apps in a store category (e.g. ``faces``, ``games``, ``tools``),
        paginated, as compact id/title/type/author/hearts summaries with a
        ``compatible`` flag for the requested ``hardware``. ``has_more``
        indicates further pages.
        """
        return _store_category(client, category_slug, hardware, limit, offset, sort)

    @mcp.tool()
    def store_developer(
        developer_id: str,
        hardware: str = "emery",
        limit: int = 20,
        offset: int = 0,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """List every app published by one developer, paginated, as compact
        id/title/type/author/hearts summaries with a ``compatible`` flag for
        the requested ``hardware``. ``has_more`` indicates further pages.
        """
        return _store_developer(client, developer_id, hardware, limit, offset, sort)

    @mcp.tool()
    def store_search(
        query: str,
        type_string: str = "any",
        hardware: str = "emery",
        max_results: int = 20,
    ) -> dict[str, Any]:
        """Search the whole appstore catalog by title/keyword.

        Queries the same hosted search index the official store website uses,
        so long-tail and low-heart apps are findable by name — searching
        "2048 touch" returns that app even though it is nowhere near the
        most-loved listings. Results are compact summaries
        (id/title/type/author/hearts + a ``compatible`` flag for ``hardware``)
        in the index's own relevance order, with ``total_hits`` and
        ``has_more``.

        Args:
            query: free text; matched against title, author, and listing text.
            type_string: ``any`` (default, searches both kinds), ``faces`` /
                ``watchfaces``, or ``apps`` / ``watchapps-and-companions``.
            hardware: platform the ``compatible`` flag is computed for.
            max_results: 1–50.

        Sharp edges:
        - A search hit has no ``.pbw`` URL — call ``store_app(id)`` (or go
          straight to ``store_download_pbw(id, dest)``) for the download.
        - ``hardware`` flags compatibility, it does not filter results out.
        - If the index is unreachable the call still answers, degrading to a
          bounded client-side scan of the ``all``/``most-loved`` listings;
          ``method`` always states which path ran, and the scan path adds
          ``index_error``.
        - Already know the app? Skip search: every store URL ends in the app's
          24-hex id (e.g. ``.../2048-touch_6df87b64b7174448a065ef54``), and
          ``store_app`` / ``store_download_pbw`` take that id directly.
        """
        return _store_search(client, query, type_string, hardware, max_results)

    @mcp.tool()
    def store_compare(app_ids: list[str]) -> dict[str, Any]:
        """Bulk-fetch several apps by id and return a side-by-side comparison
        table for competitive research.

        ``rows`` has one entry per found app with: hearts, type, the list of
        compatible platforms, a ``has_emery`` flag, the latest release date,
        and the ``.pbw`` URL. Any ids that don't resolve come back in
        ``missing_ids`` rather than failing the whole call.
        """
        return _store_compare(client, app_ids)

    @mcp.tool()
    def store_download_pbw(app_id: str, dest_dir: str) -> dict[str, Any]:
        """Download an app's latest-release ``.pbw`` to ``dest_dir`` by app id.

        ``app_id`` is the 24-hex id from ``store_search``/any listing tool, or
        the trailing segment of a store URL
        (``https://apps.repebble.com/2048-touch_6df87b64b7174448a065ef54``) —
        no search call is required if you already have it.

        Creates ``dest_dir`` if needed, writes the file, and returns its path
        and byte size (plus the app title and version). The on-disk filename is
        derived from the (untrusted) appstore response but always sanitized to a
        single basename inside ``dest_dir`` — a hostile ``id``/version cannot
        traverse out. Refuses with a clear error if: the app has no published
        ``.pbw``; ``dest_dir`` exists as a non-directory; the body is empty
        (0 bytes); or the body exceeds a 32 MiB safety cap (a real .pbw runs
        from tens of KB to a few MB). Sends the polite pebble-mcp User-Agent. This
        feeds the install/emulator flow — hand the returned ``path`` to a Tier 3
        install tool.
        """
        return _store_download_pbw(client, app_id, dest_dir)
