"""Tests for pebble_mcp.project (scaffolding library)."""

from __future__ import annotations

import json
import os
import uuid as _uuid

import pytest

from pebble_mcp import project as p


def _read_pkg(path: str) -> dict:
    with open(os.path.join(path, "package.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _is_uuid4(value: str) -> bool:
    u = _uuid.UUID(value)
    return u.version == 4


# --------------------------------------------------------------------------- #
# project_new -- C
# --------------------------------------------------------------------------- #
def test_new_c_watchface(tmp_path):
    r = p.project_new("My Face", kind="watchface", language="c", dest_dir=str(tmp_path))
    assert r["kind"] == "watchface"
    assert r["language"] == "c"
    assert os.path.basename(r["path"]) == "my-face"

    pkg = _read_pkg(r["path"])
    assert _is_uuid4(pkg["pebble"]["uuid"])
    assert pkg["pebble"]["uuid"] == r["uuid"]
    assert pkg["pebble"]["watchapp"]["watchface"] is True
    assert pkg["pebble"]["displayName"] == "My Face"
    assert pkg["name"] == "my-face"
    # C layout: a src/c/<slug>.c file, no embeddedjs.
    assert os.path.isfile(os.path.join(r["path"], "src", "c", "my-face.c"))
    assert not os.path.isdir(os.path.join(r["path"], "src", "embeddedjs"))
    assert "src/c/my-face.c" in r["files_created"]
    # digital-clock template markers.
    with open(os.path.join(r["path"], "src", "c", "my-face.c")) as fh:
        src = fh.read()
    assert "tick_timer_service_subscribe" in src
    assert "text_layer_create" in src


def test_new_c_watchapp_sets_watchface_false(tmp_path):
    r = p.project_new("Runner", kind="watchapp", language="c", dest_dir=str(tmp_path))
    pkg = _read_pkg(r["path"])
    assert pkg["pebble"]["watchapp"]["watchface"] is False
    with open(os.path.join(r["path"], "src", "c", "runner.c")) as fh:
        assert "app_event_loop" in fh.read()


def test_new_c_defaults_to_all_seven_platforms(tmp_path):
    r = p.project_new("Face", language="c", dest_dir=str(tmp_path))
    assert r["platforms"] == list(p.ALL_PLATFORMS)
    assert "emery" in r["platforms"]


def test_new_custom_platforms_validated_and_ordered(tmp_path):
    r = p.project_new("Face", language="c", platforms=["emery", "aplite"], dest_dir=str(tmp_path))
    # Canonical order, not input order.
    assert r["platforms"] == ["aplite", "emery"]


def test_new_rejects_unknown_platform(tmp_path):
    with pytest.raises(ValueError, match="unknown platform"):
        p.project_new("Face", platforms=["nope"], dest_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# project_new -- JavaScript / Alloy
# --------------------------------------------------------------------------- #
def test_new_javascript_is_alloy(tmp_path):
    r = p.project_new("Js Face", language="javascript", dest_dir=str(tmp_path))
    assert r["language"] == "javascript"
    assert r["project_type"] == "moddable"
    assert r["platforms"] == ["emery", "gabbro"]

    pkg = _read_pkg(r["path"])
    assert pkg["pebble"]["projectType"] == "moddable"
    # Alloy layout differs from C: mdbl.c glue + embeddedjs + pkjs.
    base = r["path"]
    assert os.path.isfile(os.path.join(base, "src", "c", "mdbl.c"))
    assert os.path.isfile(os.path.join(base, "src", "embeddedjs", "main.js"))
    assert os.path.isfile(os.path.join(base, "src", "embeddedjs", "manifest.json"))
    assert os.path.isfile(os.path.join(base, "src", "pkjs", "index.js"))


def test_new_javascript_clamps_classic_platforms_with_warning(tmp_path):
    r = p.project_new(
        "Js", language="javascript", platforms=["aplite", "emery"], dest_dir=str(tmp_path)
    )
    assert r["platforms"] == ["emery"]
    assert r["warnings"]
    assert "aplite" in r["warnings"][0]


def test_new_javascript_all_classic_falls_back_to_alloy_platforms(tmp_path):
    r = p.project_new(
        "Js", language="javascript", platforms=["aplite", "basalt"], dest_dir=str(tmp_path)
    )
    assert r["platforms"] == ["emery", "gabbro"]


# --------------------------------------------------------------------------- #
# companion / config
# --------------------------------------------------------------------------- #
def test_companion_adds_pkjs(tmp_path):
    r = p.project_new("App", language="c", companion=True, dest_dir=str(tmp_path))
    assert os.path.isfile(os.path.join(r["path"], "src", "pkjs", "index.js"))


def test_no_companion_by_default_for_c(tmp_path):
    r = p.project_new("App", language="c", dest_dir=str(tmp_path))
    assert not os.path.isdir(os.path.join(r["path"], "src", "pkjs"))


def test_config_adds_clay_page_and_dependency(tmp_path):
    r = p.project_new("App", language="c", config=True, dest_dir=str(tmp_path))
    base = r["path"]
    assert os.path.isfile(os.path.join(base, "src", "pkjs", "config.js"))
    assert os.path.isfile(os.path.join(base, "src", "pkjs", "index.js"))
    pkg = _read_pkg(base)
    assert "pebble-clay" in pkg["dependencies"]
    # Clay page implies at least one message key wired.
    assert pkg["pebble"]["messageKeys"]
    with open(os.path.join(base, "src", "pkjs", "index.js")) as fh:
        assert "pebble-clay" in fh.read()


def test_new_refuses_existing_dir(tmp_path):
    p.project_new("Dup", dest_dir=str(tmp_path))
    with pytest.raises(ValueError, match="already exists"):
        p.project_new("Dup", dest_dir=str(tmp_path))


def test_new_rejects_bad_kind_and_language(tmp_path):
    with pytest.raises(ValueError, match="unknown kind"):
        p.project_new("X", kind="widget", dest_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown language"):
        p.project_new("X", language="rust", dest_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# project_info round-trip
# --------------------------------------------------------------------------- #
def test_info_round_trips_c(tmp_path):
    r = p.project_new("Round Trip", kind="watchapp", language="c", dest_dir=str(tmp_path))
    info = p.project_info(r["path"])
    assert info["uuid"] == r["uuid"]
    assert info["displayName"] == "Round Trip"
    assert info["kind"] == "watchapp"
    assert info["watchface"] is False
    assert info["language"] == "c"
    assert info["platforms"] == r["platforms"]
    assert info["sdkVersion"] == "3"


def test_info_detects_javascript(tmp_path):
    r = p.project_new("Jsy", language="javascript", dest_dir=str(tmp_path))
    info = p.project_info(r["path"])
    assert info["language"] == "javascript"
    assert info["project_type"] == "moddable"
    assert info["kind"] == "watchface"


def test_info_errors_on_non_project(tmp_path):
    with pytest.raises(ValueError, match="not a Pebble project"):
        p.project_info(str(tmp_path))


def test_info_errors_on_json_without_pebble(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}')
    with pytest.raises(ValueError, match="no 'pebble' block"):
        p.project_info(str(tmp_path))


# --------------------------------------------------------------------------- #
# project_add_resource
# --------------------------------------------------------------------------- #
def test_add_resource_raw_copies_and_wires(tmp_path):
    r = p.project_new("Res", language="c", dest_dir=str(tmp_path))
    src = tmp_path / "blob.bin"
    src.write_bytes(b"\x01\x02\x03")
    out = p.project_add_resource(r["path"], str(src), "BLOB", "raw")
    assert os.path.isfile(os.path.join(r["path"], "resources", "blob.bin"))
    assert out["media"][-1] == {
        "type": "raw",
        "name": "BLOB",
        "file": "resources/blob.bin",
    }
    # Persisted to package.json.
    pkg = _read_pkg(r["path"])
    assert pkg["pebble"]["resources"]["media"][-1]["name"] == "BLOB"


def test_add_resource_bitmap_with_prep_target(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    r = p.project_new("Img", language="c", dest_dir=str(tmp_path))
    src = tmp_path / "in.png"
    Image.new("RGB", (400, 400), (200, 30, 30)).save(src)
    out = p.project_add_resource(r["path"], str(src), "ICON", "bitmap", prep_target="menu-icon")
    written = os.path.join(r["path"], "resources", "in.png")
    assert os.path.isfile(written)
    # Prepped to the menu-icon target size (25x25) and quantized.
    assert Image.open(written).size == (25, 25)
    assert out["media"][-1]["type"] == "bitmap"


def test_add_resource_rejects_missing_file(tmp_path):
    r = p.project_new("Res", language="c", dest_dir=str(tmp_path))
    with pytest.raises(ValueError, match="not a readable file"):
        p.project_add_resource(r["path"], str(tmp_path / "nope.bin"), "X", "raw")


def test_add_resource_replaces_same_name(tmp_path):
    r = p.project_new("Res", language="c", dest_dir=str(tmp_path))
    a = tmp_path / "a.bin"
    a.write_bytes(b"a")
    b = tmp_path / "b.bin"
    b.write_bytes(b"b")
    p.project_add_resource(r["path"], str(a), "SAME", "raw")
    out = p.project_add_resource(r["path"], str(b), "SAME", "raw")
    names = [m["name"] for m in out["media"]]
    assert names.count("SAME") == 1
    assert out["media"][-1]["file"] == "resources/b.bin"


# --------------------------------------------------------------------------- #
# project_set_meta
# --------------------------------------------------------------------------- #
def test_set_meta_new_uuid(tmp_path):
    r = p.project_new("Meta", language="c", dest_dir=str(tmp_path))
    old = r["uuid"]
    blk = p.project_set_meta(r["path"], uuid="new")
    assert blk["uuid"] != old
    assert _is_uuid4(blk["uuid"])


def test_set_meta_specific_uuid(tmp_path):
    r = p.project_new("Meta", language="c", dest_dir=str(tmp_path))
    blk = p.project_set_meta(r["path"], uuid="12345678-1234-1234-1234-123456789abc")
    assert blk["uuid"] == "12345678-1234-1234-1234-123456789abc"


def test_set_meta_add_remove_platforms(tmp_path):
    r = p.project_new("Meta", language="c", platforms=["aplite", "basalt"], dest_dir=str(tmp_path))
    blk = p.project_set_meta(r["path"], add_platforms=["emery"], remove_platforms=["aplite"])
    assert blk["targetPlatforms"] == ["basalt", "emery"]


def test_set_meta_capabilities_and_keys(tmp_path):
    r = p.project_new("Meta", language="c", dest_dir=str(tmp_path))
    blk = p.project_set_meta(
        r["path"], capabilities=["configurable", "location"], message_keys=["K1", "K2"]
    )
    assert blk["capabilities"] == ["configurable", "location"]
    assert blk["messageKeys"] == ["K1", "K2"]
    # Round-trips through project_info.
    info = p.project_info(r["path"])
    assert info["capabilities"] == ["configurable", "location"]
    assert info["messageKeys"] == ["K1", "K2"]


def test_set_meta_rejects_unknown_add_platform(tmp_path):
    r = p.project_new("Meta", language="c", dest_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown platform"):
        p.project_set_meta(r["path"], add_platforms=["bogus"])
