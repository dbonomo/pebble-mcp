"""Tests for pebble_mcp.tools_store — offline, against fixtures + synthetic
transport stubs (same pattern as tests/test_store.py). No network access.

Covers: registration exposes the expected tool names; client-side search
ranking over a synthetic candidate pool; store_compare shaping from the bulk
fixture; store_download_pbw writing bytes via a stubbed transport.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from pebble_mcp import tools_store
from pebble_mcp.store import StoreClient, StoreError

FIXTURES = Path(__file__).parent / "fixtures" / "store"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeTransport:
    """Replays scripted responses keyed on ``(method, path)`` (query stripped).

    A scripted value is either ``(status, fixture_filename)``, ``(status,
    raw_json_or_html_string)`` (detected by a leading ``{``/``<``), or
    ``(status, bytes)`` for binary bodies like a .pbw.
    """

    def __init__(self, script: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.script = script
        self.calls: list[tuple[str, str, dict[str, list[str]], bytes | None]] = []

    def __call__(self, method, url, body, timeout):
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.calls.append((method, parsed.path, query, body))
        key = (method, parsed.path)
        if key not in self.script:
            raise AssertionError(f"unscripted request: {method} {parsed.path}")
        status, payload = self.script[key]
        if isinstance(payload, bytes):
            raw = payload
        elif payload.startswith(("{", "<")):
            raw = payload.encode("utf-8")
        else:
            raw = _load(payload)
        return status, raw, {}


def client_for(script) -> tuple[StoreClient, FakeTransport]:
    transport = FakeTransport(script)
    return StoreClient(transport=transport), transport


def _synthetic_page(apps: list[dict], *, next_page: str | None = None) -> str:
    """Build a listing-page JSON body with the given app objects."""
    return json.dumps(
        {
            "data": apps,
            "limit": len(apps),
            "offset": 0,
            "links": {"nextPage": next_page},
        }
    )


def _app(app_id, title, author, description, *, type_="watchface", hearts=0, emery=True):
    compat = {"emery": {"supported": bool(emery)}, "basalt": {"supported": True}}
    return {
        "id": app_id,
        "title": title,
        "type": type_,
        "author": author,
        "hearts": hearts,
        "description": description,
        "compatibility": compat,
    }


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
async def test_register_exposes_expected_tools():
    mcp = FastMCP("test")
    tools_store.register(mcp)
    names = {t.name for t in await mcp.list_tools()}
    assert names == {
        "store_app",
        "store_collection",
        "store_category",
        "store_developer",
        "store_search",
        "store_compare",
        "store_download_pbw",
    }


async def test_registered_tools_have_docstrings():
    mcp = FastMCP("test")
    tools_store.register(mcp)
    for tool in await mcp.list_tools():
        assert tool.description and tool.description.strip()


# --------------------------------------------------------------------------- #
# store_app / listings shaping
# --------------------------------------------------------------------------- #
def test_store_app_full_shape_from_fixture():
    client, _ = client_for(
        {("GET", "/api/v1/apps/id/698fc9d8086643000aef17a3"): (200, "app_by_id_hubble.json")}
    )
    out = tools_store._store_app(client, "698fc9d8086643000aef17a3")
    assert out["title"] == "Hubble"
    assert out["has_emery"] is True
    assert out["pbw_url"].endswith("1.0.pbw")
    assert out["latest_release"]["version"] == "1.0"
    assert "emery" in out["compatible_platforms"]


def test_store_collection_summaries_and_paging_flag():
    body = _synthetic_page(
        [_app("a1", "Foo", "Bob", "desc", hearts=5)], next_page="/next?offset=50"
    )
    client, transport = client_for(
        {("GET", "/api/v1/apps/collection/most-loved/watchfaces"): (200, body)}
    )
    out = tools_store._store_collection(
        client, "most-loved", "watchfaces", "emery", 20, 0, "hearts"
    )
    assert out["slug"] == "most-loved"
    assert out["type"] == "watchfaces"
    assert out["has_more"] is True
    assert out["apps"][0] == {
        "id": "a1",
        "title": "Foo",
        "type": "watchface",
        "author": "Bob",
        "hearts": 5,
        "hardware": "emery",
        "compatible": True,
    }
    _, _, query, _ = transport.calls[0]
    assert query["sort"] == ["hearts"]


def test_store_category_and_developer_reach_right_endpoints():
    body = _synthetic_page([_app("a1", "Foo", "Bob", "d")])
    client, transport = client_for(
        {
            ("GET", "/api/v1/apps/category/faces"): (200, body),
            ("GET", "/api/v1/apps/dev/dev123"): (200, body),
        }
    )
    cat = tools_store._store_category(client, "faces", "emery", 20, 0, None)
    dev = tools_store._store_developer(client, "dev123", "emery", 20, 0, None)
    assert cat["category"] == "faces"
    assert dev["developer_id"] == "dev123"
    paths = {p for _, p, _, _ in transport.calls}
    assert "/api/v1/apps/category/faces" in paths
    assert "/api/v1/apps/dev/dev123" in paths


# --------------------------------------------------------------------------- #
# store_search — index path (preferred) over a synthetic search response
# --------------------------------------------------------------------------- #
SEARCH_PATH = "/1/indexes/*/queries"


def _synthetic_search(
    hits: list[dict], *, nb_hits: int | None = None, page: int = 0, nb_pages: int = 1
) -> str:
    """Build a search-index response body wrapping the given app records."""
    return json.dumps(
        {
            "results": [
                {
                    "hits": hits,
                    "nbHits": len(hits) if nb_hits is None else nb_hits,
                    "page": page,
                    "nbPages": nb_pages,
                    "hitsPerPage": len(hits),
                }
            ]
        }
    )


def _index_client(body: str, status: int = 200) -> tuple[StoreClient, FakeTransport]:
    return client_for({("POST", SEARCH_PATH): (status, body)})


def test_search_uses_the_index_and_returns_relevance_order():
    hits = [
        _app("touch", "2048 Touch", "vorsk", "", type_="watchapp", hearts=111),
        _app("other", "2048", "someone", "", type_="watchapp", hearts=3),
    ]
    client, transport = _index_client(_synthetic_search(hits, nb_hits=8, nb_pages=2))
    out = tools_store._store_search(client, "2048 touch", "any", "emery", 20)

    assert [r["id"] for r in out["results"]] == ["touch", "other"]  # index order kept
    assert out["method"].startswith("store search index")
    assert out["total_hits"] == 8
    assert out["returned"] == out["result_count"] == 2
    assert out["has_more"] is True
    assert "more_hint" in out
    assert out["results"][0]["compatible"] is True
    # One POST to the search endpoint; no collection listing was scanned.
    assert [(m, p) for m, p, _, _ in transport.calls] == [("POST", SEARCH_PATH)]
    body = json.loads(transport.calls[0][3])["requests"][0]
    assert body["query"] == "2048 touch"
    assert "tagFilters" not in body  # type_string="any" searches both kinds


def test_search_type_string_maps_to_an_index_tag_filter():
    client, transport = _index_client(_synthetic_search([]))
    tools_store._store_search(client, "x", "watchfaces", "emery", 20)
    assert json.loads(transport.calls[0][3])["requests"][0]["tagFilters"] == [["watchface"]]

    client, transport = _index_client(_synthetic_search([]))
    tools_store._store_search(client, "x", "watchapps-and-companions", "emery", 20)
    assert json.loads(transport.calls[0][3])["requests"][0]["tagFilters"] == [["watchapp"]]


def test_search_no_hits_points_at_the_by_id_escape_hatch():
    client, _ = _index_client(_synthetic_search([]))
    out = tools_store._store_search(client, "nothing matches this", "any", "emery", 20)
    assert out["result_count"] == 0
    assert "store_app" in out["next_step"]
    assert "24-hex" in out["next_step"]


def test_search_clamps_max_results_into_the_documented_range():
    hits = [_app(f"a{i}", f"App {i}", "X", "") for i in range(3)]
    client, transport = _index_client(_synthetic_search(hits))
    tools_store._store_search(client, "app", "any", "emery", 10**9)
    assert json.loads(transport.calls[0][3])["requests"][0]["hitsPerPage"] == 50

    client, transport = _index_client(_synthetic_search(hits))
    tools_store._store_search(client, "app", "any", "emery", -5)
    assert json.loads(transport.calls[0][3])["requests"][0]["hitsPerPage"] == 1


def test_search_falls_back_to_a_listing_scan_when_the_index_fails():
    pool = [_app("t", "Weather Pro", "Alice", "forecast", hearts=1)]
    client, transport = client_for(
        {
            ("POST", SEARCH_PATH): (503, '{"message": "index unavailable"}'),
            ("GET", "/api/v1/apps/collection/all/watchfaces"): (200, _synthetic_page(pool)),
            ("GET", "/api/v1/apps/collection/most-loved/watchfaces"): (
                200,
                _synthetic_page([]),
            ),
        }
    )
    out = tools_store._store_search(client, "weather", "watchfaces", "emery", 20)

    assert [r["id"] for r in out["results"]] == ["t"]
    assert out["method"].startswith("client-side listing scan")
    assert "index unavailable" in out["index_error"]
    assert "store_app" in out["next_step"]


def test_search_scan_for_any_type_walks_both_listings():
    face = _app("f", "Weather Face", "X", "d")
    app = _app("a", "Weather App", "X", "d", type_="watchapp")
    client, transport = client_for(
        {
            ("POST", SEARCH_PATH): (500, '{"message": "boom"}'),
            ("GET", "/api/v1/apps/collection/all/watchfaces"): (200, _synthetic_page([face])),
            ("GET", "/api/v1/apps/collection/most-loved/watchfaces"): (
                200,
                _synthetic_page([]),
            ),
            ("GET", "/api/v1/apps/collection/all/watchapps-and-companions"): (
                200,
                _synthetic_page([app]),
            ),
            ("GET", "/api/v1/apps/collection/most-loved/watchapps-and-companions"): (
                200,
                _synthetic_page([]),
            ),
        }
    )
    out = tools_store._store_search(client, "weather", "any", "emery", 20)
    assert {r["id"] for r in out["results"]} == {"f", "a"}


# --------------------------------------------------------------------------- #
# store_search — fallback ranking over a synthetic listing pool
# --------------------------------------------------------------------------- #
def _search_client(pool_apps: list[dict]) -> tuple[StoreClient, FakeTransport]:
    # store_search pages "all" then "most-loved"; give "all" the pool and
    # "most-loved" an empty page so the union is just the pool (deduped).
    return client_for(
        {
            ("GET", "/api/v1/apps/collection/all/watchfaces"): (
                200,
                _synthetic_page(pool_apps),
            ),
            ("GET", "/api/v1/apps/collection/most-loved/watchfaces"): (
                200,
                _synthetic_page([]),
            ),
        }
    )


def test_search_ranks_title_hit_above_description_hit():
    pool = [
        _app("t", "Weather Pro", "Alice", "shows the forecast", hearts=1),
        _app("d", "Clocky", "Bob", "great for weather nerds", hearts=1),
        _app("n", "Battery", "Carol", "battery meter", hearts=99),
    ]
    client, _ = _search_client(pool)
    out = tools_store._search_scan(client, "weather", "watchfaces", "emery", 20)

    ids = [r["id"] for r in out["results"]]
    assert ids == ["t", "d"]  # title match outranks description match; "n" excluded
    assert out["results"][0]["score"] > out["results"][1]["score"]
    assert out["candidates_scanned"] == 3
    assert out["method"].startswith("client-side")


def test_search_ties_break_on_hearts():
    pool = [
        _app("low", "Weather A", "X", "d", hearts=2),
        _app("high", "Weather B", "X", "d", hearts=500),
    ]
    client, _ = _search_client(pool)
    out = tools_store._search_scan(client, "weather", "watchfaces", "emery", 20)
    assert [r["id"] for r in out["results"]] == ["high", "low"]


def test_search_respects_max_results():
    pool = [_app(f"w{i}", f"Weather {i}", "X", "d", hearts=i) for i in range(5)]
    client, _ = _search_client(pool)
    out = tools_store._search_scan(client, "weather", "watchfaces", "emery", 2)
    assert out["result_count"] == 2
    assert len(out["results"]) == 2


def test_search_rejects_invalid_type_string():
    client, transport = client_for({})
    with pytest.raises(StoreError, match="invalid type_string"):
        tools_store._store_search(client, "x", "watchapps", "emery", 20)
    assert transport.calls == []


def test_search_pages_until_no_next_page():
    # Two pages of "all", then most-loved empty. Page 1 has a nextPage link so
    # the scan continues to offset 50; page 2 has none so it stops.
    page1 = _synthetic_page([_app("p1", "Weather One", "X", "d")], next_page="/n?offset=50")
    page2 = _synthetic_page([_app("p2", "Weather Two", "X", "d")])
    calls = {"n": 0}

    class PagingTransport(FakeTransport):
        def __call__(self, method, url, body, timeout):
            parsed = urllib.parse.urlsplit(url)
            self.calls.append((method, parsed.path, urllib.parse.parse_qs(parsed.query), body))
            if parsed.path == "/api/v1/apps/collection/all/watchfaces":
                q = urllib.parse.parse_qs(parsed.query)
                offset = q.get("offset", ["0"])[0]
                raw = (page1 if offset == "0" else page2).encode()
                return 200, raw, {}
            if parsed.path == "/api/v1/apps/collection/most-loved/watchfaces":
                return 200, _synthetic_page([]).encode(), {}
            raise AssertionError(parsed.path)

    client = StoreClient(transport=PagingTransport({}))
    out = tools_store._search_scan(client, "weather", "watchfaces", "emery", 20)
    assert {r["id"] for r in out["results"]} == {"p1", "p2"}
    assert calls  # silence unused


# --------------------------------------------------------------------------- #
# store_compare shaping
# --------------------------------------------------------------------------- #
def test_store_compare_shapes_table_from_bulk_fixture():
    client, transport = client_for({("POST", "/api/v1/apps/bulk"): (200, "apps_bulk.json")})
    out = tools_store._store_compare(
        client, ["698fc9d8086643000aef17a3", "52cc44e045ffdd31dd000180"]
    )
    assert out["found_count"] == len(out["rows"]) == 2
    assert out["missing_ids"] == []
    assert out["columns"][0] == "id"
    row = out["rows"][0]
    assert set(row) == {
        "id",
        "title",
        "type",
        "hearts",
        "platforms",
        "has_emery",
        "latest_release_date",
        "pbw_url",
    }
    method, path, _, body = transport.calls[0]
    assert method == "POST" and path == "/api/v1/apps/bulk"
    assert json.loads(body)["ids"] == [
        "698fc9d8086643000aef17a3",
        "52cc44e045ffdd31dd000180",
    ]


def test_store_compare_reports_missing_ids():
    client, _ = client_for({("POST", "/api/v1/apps/bulk"): (200, "apps_bulk_missing.json")})
    out = tools_store._store_compare(client, ["000000000000000000000000"])
    assert out["rows"] == []
    assert out["missing_ids"] == ["000000000000000000000000"]


# --------------------------------------------------------------------------- #
# store_download_pbw — writes bytes via stubbed transport
# --------------------------------------------------------------------------- #
def test_download_pbw_writes_file(tmp_path):
    pbw_path = "/api/assets/apps/698fc9d8086643000aef17a3/releases/1.0.pbw"
    client, transport = client_for(
        {
            ("GET", "/api/v1/apps/id/698fc9d8086643000aef17a3"): (
                200,
                "app_by_id_hubble.json",
            ),
            ("GET", pbw_path): (200, b"PK\x03\x04fake-pbw-bytes"),
        }
    )
    dest = tmp_path / "downloads"
    out = tools_store._store_download_pbw(client, "698fc9d8086643000aef17a3", str(dest))

    written = Path(out["path"])
    assert written.parent == dest
    assert written.read_bytes() == b"PK\x03\x04fake-pbw-bytes"
    assert out["bytes"] == len(b"PK\x03\x04fake-pbw-bytes")
    assert out["version"] == "1.0"
    assert out["pbw_url"].endswith("1.0.pbw")


def test_download_pbw_refuses_when_no_pbw():
    body = json.dumps(
        {
            "data": [
                {
                    "id": "nopbw",
                    "title": "No Release",
                    "type": "watchface",
                    "author": "X",
                    "compatibility": {},
                }
            ]
        }
    )
    client, _ = client_for({("GET", "/api/v1/apps/id/nopbw"): (200, body)})
    with pytest.raises(StoreError, match="no downloadable .pbw"):
        tools_store._store_download_pbw(client, "nopbw", "/tmp/should-not-be-created")


def test_download_pbw_surfaces_http_error(tmp_path):
    pbw_path = "/api/assets/apps/698fc9d8086643000aef17a3/releases/1.0.pbw"
    client, _ = client_for(
        {
            ("GET", "/api/v1/apps/id/698fc9d8086643000aef17a3"): (
                200,
                "app_by_id_hubble.json",
            ),
            ("GET", pbw_path): (404, b"nope"),
        }
    )
    with pytest.raises(StoreError, match="pbw download failed"):
        tools_store._store_download_pbw(client, "698fc9d8086643000aef17a3", str(tmp_path / "dl"))


# --------------------------------------------------------------------------- #
# store_download_pbw — adversarial: hostile filenames, size, dest_dir
# --------------------------------------------------------------------------- #
def _hostile_app_body(app_id: str, *, pbw_seg: str = "1.0.pbw") -> str:
    return json.dumps(
        {
            "data": [
                {
                    "id": app_id,
                    "title": "Evil",
                    "type": "watchface",
                    "author": "X",
                    "compatibility": {},
                    "latest_release": {
                        "version": "1.0",
                        "pbw_file": f"https://host/assets/{pbw_seg}",
                    },
                }
            ]
        }
    )


def test_download_pbw_hostile_id_cannot_escape_dest_dir(tmp_path):
    # Hostile API-supplied id with traversal: the file must land INSIDE dest_dir.
    app_id = "../../../../etc/evil"
    pbw_path = "/assets/1.0.pbw"
    client, _ = client_for(
        {
            ("GET", f"/api/v1/apps/id/{urllib.parse.quote(app_id, safe='')}"): (
                200,
                _hostile_app_body(app_id),
            ),
            ("GET", pbw_path): (200, b"PK\x03\x04pbw"),
        }
    )
    dest = tmp_path / "downloads"
    out = tools_store._store_download_pbw(client, app_id, str(dest))
    written = Path(out["path"]).resolve()
    assert written.parent == dest.resolve()  # did not escape
    assert not (tmp_path.parent / "etc" / "evil-1.0.pbw").exists()
    assert written.read_bytes() == b"PK\x03\x04pbw"


def test_download_pbw_hostile_pbw_url_segment_sanitized(tmp_path):
    # The .pbw URL's last segment is also attacker-controlled.
    app_id = "safeid"
    pbw_seg = "..%2f..%2fx.pbw"  # decoded path segment used verbatim by the client
    decoded_seg = urllib.parse.unquote(pbw_seg)
    body = _hostile_app_body(app_id, pbw_seg=decoded_seg)
    client, _ = client_for(
        {
            ("GET", "/api/v1/apps/id/safeid"): (200, body),
            ("GET", f"/assets/{decoded_seg}"): (200, b"PK\x03\x04pbw"),
        }
    )
    dest = tmp_path / "downloads"
    out = tools_store._store_download_pbw(client, app_id, str(dest))
    assert Path(out["path"]).resolve().parent == dest.resolve()


def test_download_pbw_rejects_dest_dir_that_is_a_file(tmp_path):
    existing_file = tmp_path / "not-a-dir"
    existing_file.write_text("i am a file")
    client, _ = client_for({("GET", "/api/v1/apps/id/x"): (200, _hostile_app_body("x"))})
    with pytest.raises(StoreError, match="not a directory"):
        tools_store._store_download_pbw(client, "x", str(existing_file))


def test_download_pbw_rejects_zero_byte_body(tmp_path):
    pbw_path = "/assets/1.0.pbw"
    client, _ = client_for(
        {
            ("GET", "/api/v1/apps/id/x"): (200, _hostile_app_body("x")),
            ("GET", pbw_path): (200, b""),
        }
    )
    with pytest.raises(StoreError, match="empty"):
        tools_store._store_download_pbw(client, "x", str(tmp_path / "dl"))


def test_download_pbw_rejects_oversized_body(tmp_path):
    pbw_path = "/assets/1.0.pbw"
    huge = b"P" * (tools_store._MAX_PBW_BYTES + 1)
    client, _ = client_for(
        {
            ("GET", "/api/v1/apps/id/x"): (200, _hostile_app_body("x")),
            ("GET", pbw_path): (200, huge),
        }
    )
    with pytest.raises(StoreError, match="safety cap"):
        tools_store._store_download_pbw(client, "x", str(tmp_path / "dl"))


# --------------------------------------------------------------------------- #
# store_search — adversarial: pathological queries and max_results, hostile pool
# --------------------------------------------------------------------------- #
def test_search_empty_query_returns_no_results():
    client, _ = _search_client([_app("a", "Weather", "X", "d")])
    out = tools_store._search_scan(client, "   ", "watchfaces", "emery", 20)
    assert out["result_count"] == 0
    assert out["results"] == []


def test_search_pathological_queries_do_not_crash():
    pool = [_app("a", "Weather Pro", "X", "regex .*+? [chars]")]
    for q in ("a" * 10_000, ".*+?[](){}", "☃️ unicode 作者", "\x00\x07ctl"):
        client, _ = _search_client(pool)
        out = tools_store._search_scan(client, q, "watchfaces", "emery", 20)
        assert "results" in out


def test_search_negative_and_huge_max_results():
    pool = [_app(f"w{i}", f"Weather {i}", "X", "d", hearts=i) for i in range(3)]
    client, _ = _search_client(pool)
    neg = tools_store._search_scan(client, "weather", "watchfaces", "emery", -5)
    assert neg["result_count"] == 0
    client, _ = _search_client(pool)
    huge = tools_store._search_scan(client, "weather", "watchfaces", "emery", 10**9)
    assert huge["result_count"] == 3  # bounded by the pool, no blow-up


def test_search_survives_mixed_string_and_int_hearts_in_pool():
    # Two apps tie on score; one has string hearts, one int. The tie-break sort
    # touches .hearts and would TypeError on mixed types without coercion.
    a = _app("a", "Weather A", "X", "d")
    b = _app("b", "Weather B", "X", "d")
    a["hearts"] = "5"
    b["hearts"] = 5
    client, _ = _search_client([a, b])
    out = tools_store._search_scan(client, "weather", "watchfaces", "emery", 20)
    assert {r["id"] for r in out["results"]} == {"a", "b"}
    assert all(isinstance(r["hearts"], int) for r in out["results"])
