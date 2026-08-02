"""Tests for pebble_mcp.store — entirely offline against recorded fixtures.

The HTTP layer is stubbed via a fake :data:`~pebble_mcp.store.Transport` that
serves recorded JSON fixtures (see ``tests/fixtures/store/``, captured from
live GETs against appstore-api.repebble.com during development) or
synthetic error bodies. No network access happens in this file.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from pebble_mcp.store import (
    COLLECTION_APP_TYPES,
    StoreBadRequestError,
    StoreClient,
    StoreNotFoundError,
    StoreRateLimitedError,
    StoreResponseError,
    StoreServerError,
    parse_app,
)

FIXTURES = Path(__file__).parent / "fixtures" / "store"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeTransport:
    """Records every call made through it and replays a scripted response.

    ``script`` maps ``(method, path)`` -> (status, fixture_filename). ``path``
    is matched without the query string so tests can assert on the query
    params captured separately via :attr:`calls`.
    """

    def __init__(self, script: dict[tuple[str, str], tuple[int, str]]) -> None:
        self.script = script
        self.calls: list[tuple[str, str, dict[str, list[str]], bytes | None]] = []

    def __call__(
        self, method: str, url: str, body: bytes | None, timeout: float
    ) -> tuple[int, bytes, dict[str, str]]:
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.calls.append((method, parsed.path, query, body))
        key = (method, parsed.path)
        if key not in self.script:
            raise AssertionError(f"unscripted request: {method} {parsed.path}")
        status, fixture_or_raw = self.script[key]
        # A scripted string that looks like a raw body (JSON object/array/string
        # or an HTML error page) is used verbatim; anything else is a fixture
        # filename to load from disk.
        if fixture_or_raw.startswith(("{", "[", '"', "<")):
            raw = fixture_or_raw.encode("utf-8")
        else:
            raw = _load(fixture_or_raw)
        return status, raw, {}


def client_for(script: dict[tuple[str, str], tuple[int, str]]) -> tuple[StoreClient, FakeTransport]:
    transport = FakeTransport(script)
    return StoreClient(transport=transport), transport


# --------------------------------------------------------------------------- #
# get_app
# --------------------------------------------------------------------------- #
def test_get_app_parses_hubble_fixture():
    client, transport = client_for(
        {("GET", "/api/v1/apps/id/698fc9d8086643000aef17a3"): (200, "app_by_id_hubble.json")}
    )
    app = client.get_app("698fc9d8086643000aef17a3")

    assert app.id == "698fc9d8086643000aef17a3"
    assert app.title == "Hubble"
    assert app.type == "watchapp"
    assert app.author == "Logan Head"
    assert app.hearts >= 0
    assert "astronomy" in app.description.lower()


def test_get_app_surfaces_pbw_url_and_platform_compatibility():
    client, _ = client_for(
        {("GET", "/api/v1/apps/id/698fc9d8086643000aef17a3"): (200, "app_by_id_hubble.json")}
    )
    app = client.get_app("698fc9d8086643000aef17a3")

    assert app.pbw_url == (
        "https://appstore-api.repebble.com/api/assets/apps/698fc9d8086643000aef17a3/releases/1.0.pbw"
    )
    assert app.latest_release is not None
    assert app.latest_release.version == "1.0"
    # Compatibility dict on the wire marks every classic platform supported.
    assert "emery" in app.compatible_platforms
    assert "aplite" in app.compatible_platforms
    # hardware_platforms carries per-platform screenshot/icon URLs.
    names = {p.name for p in app.platforms}
    assert "emery" in names


def test_get_app_not_found_raises_typed_error():
    client, _ = client_for(
        {("GET", "/api/v1/apps/id/000000000000000000000000"): (404, "app_not_found.json")}
    )
    with pytest.raises(StoreNotFoundError, match="App not found"):
        client.get_app("000000000000000000000000")


def test_get_app_passes_hardware_param():
    client, transport = client_for(
        {("GET", "/api/v1/apps/id/698fc9d8086643000aef17a3"): (200, "app_by_id_hubble.json")}
    )
    client.get_app("698fc9d8086643000aef17a3", hardware="emery")
    _, _, query, _ = transport.calls[0]
    assert query["hardware"] == ["emery"]


# --------------------------------------------------------------------------- #
# get_apps_bulk
# --------------------------------------------------------------------------- #
def test_get_apps_bulk_parses_found_apps():
    client, transport = client_for({("POST", "/api/v1/apps/bulk"): (200, "apps_bulk.json")})
    result = client.get_apps_bulk(["698fc9d8086643000aef17a3", "52cc44e045ffdd31dd000180"])

    assert len(result.apps) == 2
    assert result.missing_ids == []
    method, path, _, body = transport.calls[0]
    assert method == "POST"
    assert json.loads(body) == {"ids": ["698fc9d8086643000aef17a3", "52cc44e045ffdd31dd000180"]}


def test_get_apps_bulk_reports_missing_ids():
    client, _ = client_for({("POST", "/api/v1/apps/bulk"): (200, "apps_bulk_missing.json")})
    result = client.get_apps_bulk(["000000000000000000000000"])

    assert result.apps == []
    assert result.missing_ids == ["000000000000000000000000"]


def test_get_apps_bulk_empty_input_skips_request():
    client, transport = client_for({})
    result = client.get_apps_bulk([])
    assert result.apps == []
    assert result.missing_ids == []
    assert transport.calls == []


# --------------------------------------------------------------------------- #
# get_apps_by_developer / get_apps_by_category — pagination + param construction
# --------------------------------------------------------------------------- #
def test_get_apps_by_developer_parses_page():
    client, transport = client_for(
        {("GET", "/api/v1/apps/dev/5c26a4f727cdec0001c0500d"): (200, "developer_apps.json")}
    )
    page = client.get_apps_by_developer(
        "5c26a4f727cdec0001c0500d", hardware="emery", limit=3, sort="hearts"
    )
    assert page.apps
    assert all(app.author == "Logan Head" for app in page.apps)
    _, _, query, _ = transport.calls[0]
    assert query["hardware"] == ["emery"]
    assert query["limit"] == ["3"]
    assert query["sort"] == ["hearts"]
    # offset omitted -> not sent at all
    assert "offset" not in query


def test_get_apps_by_category_pagination_fields():
    client, _ = client_for({("GET", "/api/v1/apps/category/faces"): (200, "category_faces.json")})
    page = client.get_apps_by_category("faces", hardware="emery", limit=3)

    assert page.limit == 3
    assert page.offset == 0
    assert page.next_page_url is not None
    assert "offset=3" in page.next_page_url
    assert len(page.apps) == 3


# --------------------------------------------------------------------------- #
# get_apps_by_collection
# --------------------------------------------------------------------------- #
def test_get_apps_by_collection_most_loved_watchfaces():
    client, transport = client_for(
        {
            ("GET", "/api/v1/apps/collection/most-loved/watchfaces"): (
                200,
                "collection_most_loved_watchfaces.json",
            )
        }
    )
    page = client.get_apps_by_collection("most-loved", "watchfaces", hardware="emery", limit=3)

    assert len(page.apps) == 3
    assert all(app.type == "watchface" for app in page.apps)
    _, path, query, _ = transport.calls[0]
    assert path == "/api/v1/apps/collection/most-loved/watchfaces"
    assert query["hardware"] == ["emery"]


def test_get_apps_by_collection_offset_and_sort_construction():
    client, transport = client_for(
        {("GET", "/api/v1/apps/collection/all/apps"): (200, "collection_all_apps.json")}
    )
    page = client.get_apps_by_collection(
        "all", "apps", hardware="aplite", limit=2, offset=1, sort="hearts"
    )
    _, _, query, _ = transport.calls[0]
    assert query == {
        "hardware": ["aplite"],
        "limit": ["2"],
        "offset": ["1"],
        "sort": ["hearts"],
    }
    assert page.offset == 1
    assert page.limit == 2


def test_get_apps_by_collection_rejects_invalid_type_string_locally():
    # "watchapps" (unqualified plural) is a 400 on the live API; we validate
    # client-side so callers get a fast, typed error without a round trip.
    client, transport = client_for({})
    with pytest.raises(StoreBadRequestError, match="invalid collection type_string"):
        client.get_apps_by_collection("all", "watchapps")
    assert transport.calls == []


def test_get_apps_by_collection_surfaces_server_400():
    client, _ = client_for(
        {("GET", "/api/v1/apps/collection/all/apps"): (400, "invalid_app_type.json")}
    )
    with pytest.raises(StoreBadRequestError, match="Invalid app type"):
        client.get_apps_by_collection("all", "apps")


def test_get_apps_by_collection_not_found():
    client, _ = client_for(
        {
            ("GET", "/api/v1/apps/collection/bogus-slug/watchfaces"): (
                404,
                "collection_not_found.json",
            )
        }
    )
    with pytest.raises(StoreNotFoundError, match="Collection not found"):
        client.get_apps_by_collection("bogus-slug", "watchfaces")


def test_collection_app_types_matches_documented_values():
    assert COLLECTION_APP_TYPES == ("apps", "watchapps-and-companions", "faces", "watchfaces")


# --------------------------------------------------------------------------- #
# get_home
# --------------------------------------------------------------------------- #
def test_get_home_rejects_invalid_type_locally():
    client, transport = client_for({})
    with pytest.raises(StoreBadRequestError, match="invalid home_type"):
        client.get_home("watchapps")
    assert transport.calls == []


def test_get_home_parses_rows():
    # Small synthetic home fixture (the real one is ~250KB) exercising the
    # categories/collections/applications union shape.
    payload = {
        "applications": [json.loads(_load("app_by_id_hubble.json"))["data"][0]],
        "banners": [],
        "categories": [
            {
                "id": "cat1",
                "name": "Faces",
                "slug": "faces",
                "color": "ffffff",
                "application_ids": [],
                "links": {"apps": "/api/v1/apps/category/faces"},
            }
        ],
        "collections": [
            {
                "name": "Most Loved",
                "slug": "most-loved",
                "application_ids": ["698fc9d8086643000aef17a3"],
                "links": {"apps": "/api/v1/apps/collection/most-loved/apps"},
            }
        ],
    }
    # FakeTransport treats a scripted string starting with "{" as a raw JSON
    # body rather than a fixture filename, so we can hand it the payload
    # directly without touching disk.
    client, transport = client_for({("GET", "/api/v1/home/apps"): (200, json.dumps(payload))})
    home = client.get_home("apps", hardware="emery")

    assert home.categories[0].slug == "faces"
    assert home.collections[0].slug == "most-loved"
    assert home.apps[0].title == "Hubble"


# --------------------------------------------------------------------------- #
# Generic error handling
# --------------------------------------------------------------------------- #
def test_rate_limited_raises_typed_error_with_retry_after():
    class RetryAfterTransport(FakeTransport):
        def __call__(self, method, url, body, timeout):
            status, raw, _ = super().__call__(method, url, body, timeout)
            return status, raw, {"Retry-After": "2.5"}

    client = StoreClient(
        transport=RetryAfterTransport(
            {("GET", "/api/v1/apps/category/faces"): (429, '{"error":"slow down"}')}
        )
    )
    with pytest.raises(StoreRateLimitedError) as exc_info:
        client.get_apps_by_category("faces")
    assert exc_info.value.retry_after == 2.5


def test_server_error_raises_typed_error():
    client, _ = client_for(
        {("GET", "/api/v1/apps/category/faces"): (503, '{"error":"upstream unavailable"}')}
    )
    with pytest.raises(StoreServerError, match="upstream unavailable"):
        client.get_apps_by_category("faces")


def test_malformed_json_raises_response_error_not_raw_traceback():
    client, _ = client_for({("GET", "/api/v1/apps/category/faces"): (200, "{not valid json")})
    with pytest.raises(StoreResponseError, match="not valid JSON"):
        client.get_apps_by_category("faces")


def test_error_body_without_json_shape_falls_back_to_generic_message():
    # e.g. an HTML error page from an edge proxy instead of the API's own
    # {"error": ...} body -- must not raise a raw json.JSONDecodeError.
    client, _ = client_for(
        {("GET", "/api/v1/apps/category/faces"): (404, "<html>not found</html>")}
    )
    with pytest.raises(StoreNotFoundError, match="not found"):
        client.get_apps_by_category("faces")


# --------------------------------------------------------------------------- #
# Adversarial: malformed / hostile-typed API bodies must never crash the parser
# --------------------------------------------------------------------------- #
def test_parse_app_rejects_non_dict_with_actionable_message():
    with pytest.raises(StoreResponseError, match="server-side response issue") as exc_info:
        parse_app([1, 2, 3])
    assert "list" in str(exc_info.value)


def test_parse_app_tolerates_null_string_fields():
    # null title/author would previously become None and crash a later
    # ``.lower()`` in search scoring; they must coerce to "".
    app = parse_app({"id": "x", "title": None, "author": None, "description": None})
    assert app.title == ""
    assert app.author == ""
    assert app.description == ""
    # every field search scoring touches must be safely lower()-able
    assert app.title.lower() == "" and app.author.lower() == ""


def test_parse_app_coerces_string_hearts_to_int():
    assert parse_app({"id": "x", "hearts": "50"}).hearts == 50
    assert parse_app({"id": "x", "hearts": "garbage"}).hearts == 0
    assert parse_app({"id": "x", "hearts": None}).hearts == 0
    assert parse_app({"id": "x", "hearts": True}).hearts == 0  # bool is not a heart count


def test_parse_app_tolerates_wrong_typed_containers():
    # compatibility as a list, hardware_platforms full of junk, screenshot_images
    # as a dict, latest_release as a string -- all previously AttributeError'd.
    app = parse_app(
        {
            "id": "x",
            "compatibility": [1, 2, 3],
            "hardware_platforms": ["junk", 5, {"name": "emery", "images": "nope"}],
            "screenshot_images": {"a": "b"},
            "latest_release": "not-a-dict",
        }
    )
    assert app.compatible_platforms == []
    assert [p.name for p in app.platforms] == ["emery"]  # only the dict member kept
    assert app.platforms[0].icon_url is None  # images was a string -> ignored
    assert app.screenshot_urls == []
    assert app.latest_release is None


def test_parse_page_rejects_top_level_array():
    # A bare JSON array at the top level (not the documented {"data": [...]}) must
    # surface as a typed StoreResponseError, never a raw AttributeError.
    client, _ = client_for(
        {("GET", "/api/v1/apps/category/faces"): (200, "[1, 2, 3]")}
    )
    # Message should make clear this is a server-side response problem (not a
    # bad request the caller can fix by changing arguments) and suggest retrying.
    with pytest.raises(StoreResponseError, match="server-side response issue"):
        client.get_apps_by_category("faces")


def test_get_apps_bulk_rejects_non_object_body():
    client, _ = client_for({("POST", "/api/v1/apps/bulk"): (200, "[1, 2, 3]")})
    with pytest.raises(StoreResponseError, match="server-side response issue"):
        client.get_apps_bulk(["x"])


def test_get_home_rejects_non_object_body():
    client, _ = client_for({("GET", "/api/v1/home/apps"): (200, "\"just a string\"")})
    with pytest.raises(StoreResponseError, match="server-side response issue"):
        client.get_home("apps")


def test_parse_app_survives_control_chars_and_unicode_in_text():
    app = parse_app(
        {"id": "x", "title": "W​eather\x07퟿", "author": "作者"}
    )
    # stored verbatim, and still safely lower()-able (used by search scoring)
    assert isinstance(app.title, str)
    assert app.title.lower() == app.title.lower()
    assert app.author == "作者"


# --------------------------------------------------------------------------- #
# Parameter-validation decision: API-owned params pass through verbatim
# --------------------------------------------------------------------------- #
def test_negative_and_absurd_paging_params_pass_through_to_api():
    client, transport = client_for(
        {("GET", "/api/v1/apps/category/faces"): (200, "category_faces.json")}
    )
    client.get_apps_by_category(
        "faces", hardware="nonsense$$", limit=-5, offset=10**9
    )
    _, _, query, _ = transport.calls[0]
    # We deliberately do NOT second-guess API-owned params; they go as-is.
    assert query["hardware"] == ["nonsense$$"]
    assert query["limit"] == ["-5"]
    assert query["offset"] == ["1000000000"]
