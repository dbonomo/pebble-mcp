"""Project scaffolding -- the create-and-manage front door for Pebble apps.

Pure-Python generation of *buildable* Pebble projects (no shelling out to
``pebble new-project``, so this works on hosts without the CLI). The output
mirrors what the real SDK v4.17 / Core Devices toolchain emits, verified by
generating reference projects with the CLI and building them:

* **C** projects -> ``package.json`` + ``wscript`` + ``src/c/<name>.c``
  (plus optional ``src/pkjs/index.js`` companion and a ``src/pkjs/config.js``
  Clay settings page).
* **JavaScript** projects -> **Alloy** (``projectType: "moddable"``), the
  modern on-watch JS path powered by the Moddable XS engine. Layout:
  ``src/c/mdbl.c`` (native glue) + ``src/embeddedjs/main.js`` (watch-side JS)
  + ``src/embeddedjs/manifest.json`` + ``src/pkjs/index.js`` (phone-side).
  Alloy only runs on the two Moddable-XS platforms -- **emery** (Pebble
  Time 2) and **gabbro** (Pebble Round 2) -- so JavaScript projects default
  to and are restricted to those. (Rocky.js is the legacy predecessor and is
  not emitted here.)

Language is intentionally limited to ``"c"`` and ``"javascript"``; the
dispatch is table-driven so more backends can be added later.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid as _uuid
from typing import Any

from pebble_mcp.platforms import PLATFORMS

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
#: The seven platforms the SDK can build for, in canonical order (sourced from
#: the display-spec table so the two never drift).
ALL_PLATFORMS: tuple[str, ...] = tuple(PLATFORMS)
#: Platforms carrying the Moddable XS engine -- the only ones Alloy (on-watch
#: JavaScript) can target.
ALLOY_PLATFORMS: tuple[str, ...] = ("emery", "gabbro")

#: Sensible modern default for C projects (every buildable platform).
DEFAULT_C_PLATFORMS: tuple[str, ...] = ALL_PLATFORMS

KINDS = ("watchface", "watchapp")
LANGUAGES = ("c", "javascript")

SDK_VERSION = "3"
DEFAULT_AUTHOR = "pebble-mcp"
CLAY_VERSION = "^1.0.4"

_WSCRIPT = """\
#
# This file is the default set of rules to compile a Pebble application.
#
# Feel free to customize this to your needs.
#
import os.path

top = '.'
out = 'build'


def options(ctx):
    ctx.load('pebble_sdk')


def configure(ctx):
    ctx.load('pebble_sdk')


def build(ctx):
    ctx.load('pebble_sdk')

    build_worker = os.path.exists('worker_src')
    binaries = []

    cached_env = ctx.env
    for platform in ctx.env.TARGET_PLATFORMS:
        ctx.env = ctx.all_envs[platform]
        ctx.set_group(ctx.env.PLATFORM_NAME)
        app_elf = '{}/pebble-app.elf'.format(ctx.env.BUILD_DIR)
        ctx.pbl_build(source=ctx.path.ant_glob('src/c/**/*.c'), target=app_elf, bin_type='app')

        if build_worker:
            worker_elf = '{}/pebble-worker.elf'.format(ctx.env.BUILD_DIR)
            binaries.append({'platform': platform, 'app_elf': app_elf, 'worker_elf': worker_elf})
            ctx.pbl_build(source=ctx.path.ant_glob('worker_src/c/**/*.c'),
                          target=worker_elf,
                          bin_type='worker')
        else:
            binaries.append({'platform': platform, 'app_elf': app_elf})
    ctx.env = cached_env

    ctx.set_group('bundle')
    ctx.pbl_bundle(binaries=binaries,
                   js=ctx.path.ant_glob(['src/pkjs/**/*.js',
                                         'src/pkjs/**/*.json',
                                         'src/common/**/*.js']),
                   js_entry_file='src/pkjs/index.js')
"""

_C_WATCHFACE = """\
#include <pebble.h>

static Window *s_window;
static TextLayer *s_time_layer;

static void update_time(void) {
  time_t now = time(NULL);
  struct tm *t = localtime(&now);
  static char s_buf[16];
  strftime(s_buf, sizeof(s_buf), clock_is_24h_style() ? "%H:%M" : "%I:%M", t);
  text_layer_set_text(s_time_layer, s_buf);
}

static void tick_handler(struct tm *tick_time, TimeUnits units_changed) {
  update_time();
}

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(root);

  s_time_layer = text_layer_create(
      GRect(0, (bounds.size.h - 50) / 2, bounds.size.w, 50));
  text_layer_set_background_color(s_time_layer, GColorClear);
  text_layer_set_text_color(s_time_layer, GColorBlack);
  text_layer_set_font(s_time_layer,
      fonts_get_system_font(FONT_KEY_BITHAM_42_BOLD));
  text_layer_set_text_alignment(s_time_layer, GTextAlignmentCenter);
  layer_add_child(root, text_layer_get_layer(s_time_layer));
}

static void window_unload(Window *window) {
  text_layer_destroy(s_time_layer);
}

static void init(void) {
  s_window = window_create();
  window_set_background_color(s_window, GColorWhite);
  window_set_window_handlers(s_window, (WindowHandlers) {
    .load = window_load,
    .unload = window_unload,
  });
  window_stack_push(s_window, true);
  update_time();
  tick_timer_service_subscribe(MINUTE_UNIT, tick_handler);
}

static void deinit(void) {
  window_destroy(s_window);
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
"""

_C_WATCHAPP = """\
#include <pebble.h>

static Window *s_window;
static TextLayer *s_text_layer;

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(root);

  s_text_layer = text_layer_create(
      GRect(0, (bounds.size.h - 28) / 2, bounds.size.w, 28));
  text_layer_set_text(s_text_layer, "Hello, Pebble!");
  text_layer_set_text_alignment(s_text_layer, GTextAlignmentCenter);
  text_layer_set_font(s_text_layer,
      fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));
  layer_add_child(root, text_layer_get_layer(s_text_layer));
}

static void window_unload(Window *window) {
  text_layer_destroy(s_text_layer);
}

static void init(void) {
  s_window = window_create();
  window_set_window_handlers(s_window, (WindowHandlers) {
    .load = window_load,
    .unload = window_unload,
  });
  window_stack_push(s_window, true);
}

static void deinit(void) {
  window_destroy(s_window);
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
"""

# Alloy native glue -- boots the Moddable XS machine that runs src/embeddedjs.
_ALLOY_MDBL_C = """\
#include <pebble.h>

int main(void) {
  Window *w = window_create();
  window_stack_push(w, true);

#ifdef PBL_DEBUG
  ModdableCreationRecord cr = {
    .recordSize = sizeof(cr),
    .flags = kModdableCreationFlagDebug,
  };
  moddable_createMachine(&cr);
#else
  moddable_createMachine(NULL);
#endif

  window_destroy(w);
}
"""

_ALLOY_MAIN_WATCHFACE = """\
import Poco from "commodetto/Poco";

let render = new Poco(screen);
const font = new render.Font("Bitham-Black", 30);
const black = render.makeColor(0, 0, 0);
const white = render.makeColor(255, 255, 255);

function draw() {
	render.begin();
	render.fillRectangle(white, 0, 0, render.width, render.height);
	const msg = (new Date).toTimeString().slice(0, 8);
	const width = render.getTextWidth(msg, font);
	render.drawText(msg, font, black,
		(render.width - width) / 2, (render.height - font.height) / 2);
	render.end();
}

watch.addEventListener('secondchange', draw);
"""

_ALLOY_MAIN_WATCHAPP = """\
import Poco from "commodetto/Poco";

let render = new Poco(screen);
const font = new render.Font("Bitham-Black", 30);
const black = render.makeColor(0, 0, 0);
const white = render.makeColor(255, 255, 255);

function draw() {
	render.begin();
	render.fillRectangle(white, 0, 0, render.width, render.height);
	const msg = "Hello!";
	const width = render.getTextWidth(msg, font);
	render.drawText(msg, font, black,
		(render.width - width) / 2, (render.height - font.height) / 2);
	render.end();
}

draw();
"""

_ALLOY_MANIFEST = """\
{
	"include":  [
		"$(MODDABLE)/examples/manifest_mod.json",
		"$(MODDABLE)/examples/manifest_typings.json"
	],
	"modules": {
		"*": "./main"
	}
}
"""

_PKJS_PLAIN = """\
Pebble.addEventListener("ready", function (e) {
  console.log("PebbleKit JS ready.");
});
"""

_PKJS_CLAY = """\
var Clay = require('pebble-clay');
var clayConfig = require('./config');
var clay = new Clay(clayConfig);

Pebble.addEventListener("ready", function (e) {
  console.log("PebbleKit JS ready (Clay config enabled).");
});
"""

_CLAY_CONFIG_JS = """\
// Clay configuration page. Fields map onto the pebble.messageKeys declared in
// package.json; edit both together. See https://github.com/pebble/clay
module.exports = [
  {
    type: 'heading',
    defaultValue: 'App settings'
  },
  {
    type: 'section',
    items: [
      {
        type: 'toggle',
        messageKey: 'SETTING_ENABLED',
        label: 'Enable feature',
        defaultValue: true
      }
    ]
  },
  {
    type: 'submit',
    defaultValue: 'Save'
  }
];
"""

_CLAY_MESSAGE_KEYS = ["SETTING_ENABLED"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    """Package/source name: lowercase, non-alnum -> ``-``, trimmed."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a project name from {name!r}")
    return slug


def _validate_platforms(platforms: list[str]) -> list[str]:
    unknown = [p for p in platforms if p not in ALL_PLATFORMS]
    if unknown:
        raise ValueError(
            f"unknown platform(s) {unknown}; valid platforms are {list(ALL_PLATFORMS)}"
        )
    # De-dup, preserve canonical order.
    return [p for p in ALL_PLATFORMS if p in platforms]


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _load_pkg(directory: str) -> dict[str, Any]:
    pkg_path = os.path.join(directory, "package.json")
    if not os.path.isfile(pkg_path):
        raise ValueError(f"{directory!r} is not a Pebble project: no package.json found")
    try:
        with open(pkg_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"package.json in {directory!r} is not valid JSON: {exc}") from None
    if "pebble" not in data or not isinstance(data["pebble"], dict):
        raise ValueError(
            f"{directory!r} is not a Pebble project: package.json has no 'pebble' block"
        )
    return data


def _save_pkg(directory: str, data: dict[str, Any]) -> None:
    with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# project_new
# --------------------------------------------------------------------------- #
def project_new(
    name: str,
    kind: str = "watchface",
    language: str = "c",
    platforms: list[str] | None = None,
    companion: bool = False,
    config: bool = False,
    dest_dir: str = ".",
) -> dict[str, Any]:
    """Generate a buildable Pebble project. See module docstring for layout."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {list(KINDS)}")
    if language not in LANGUAGES:
        raise ValueError(f"unknown language {language!r}; expected one of {list(LANGUAGES)}")

    slug = _slug(name)
    is_watchface = kind == "watchface"
    warnings: list[str] = []

    if language == "javascript":
        # Alloy: Moddable-XS platforms only.
        if platforms is None:
            plats = list(ALLOY_PLATFORMS)
        else:
            plats = _validate_platforms(platforms)
            dropped = [p for p in plats if p not in ALLOY_PLATFORMS]
            if dropped:
                warnings.append(
                    "Alloy (JavaScript) only runs on the Moddable-XS platforms "
                    f"{list(ALLOY_PLATFORMS)}; dropped unsupported {dropped}."
                )
            plats = [p for p in plats if p in ALLOY_PLATFORMS]
            if not plats:
                plats = list(ALLOY_PLATFORMS)
    else:
        plats = list(DEFAULT_C_PLATFORMS) if platforms is None else _validate_platforms(platforms)

    project_dir = os.path.abspath(os.path.join(dest_dir, slug))
    if os.path.exists(project_dir):
        raise ValueError(f"destination already exists: {project_dir}")

    uuid_str = str(_uuid.uuid4())
    # Clay implies a phone-side companion.
    has_companion = companion or config or language == "javascript"

    message_keys: list[str] = list(_CLAY_MESSAGE_KEYS) if config else []

    pebble_block: dict[str, Any] = {
        "displayName": name,
        "uuid": uuid_str,
        "sdkVersion": SDK_VERSION,
        "enableMultiJS": True,
        "targetPlatforms": plats,
        "watchapp": {"watchface": is_watchface},
        "messageKeys": message_keys,
        "resources": {"media": []},
    }
    if language == "javascript":
        pebble_block["projectType"] = "moddable"

    pkg: dict[str, Any] = {
        "name": slug,
        "author": DEFAULT_AUTHOR,
        "version": "1.0.0",
        "keywords": ["pebble-app"],
        "private": True,
        "dependencies": {"pebble-clay": CLAY_VERSION} if config else {},
        "pebble": pebble_block,
    }

    files: list[str] = []

    def emit(rel: str, content: str) -> None:
        _write(os.path.join(project_dir, rel), content)
        files.append(rel)

    # package.json
    os.makedirs(project_dir, exist_ok=True)
    _save_pkg(project_dir, pkg)
    files.append("package.json")

    emit("wscript", _WSCRIPT)

    if language == "c":
        emit(f"src/c/{slug}.c", _C_WATCHFACE if is_watchface else _C_WATCHAPP)
    else:  # javascript / Alloy
        emit("src/c/mdbl.c", _ALLOY_MDBL_C)
        emit(
            "src/embeddedjs/main.js",
            _ALLOY_MAIN_WATCHFACE if is_watchface else _ALLOY_MAIN_WATCHAPP,
        )
        emit("src/embeddedjs/manifest.json", _ALLOY_MANIFEST)

    if has_companion:
        emit("src/pkjs/index.js", _PKJS_CLAY if config else _PKJS_PLAIN)
    if config:
        emit("src/pkjs/config.js", _CLAY_CONFIG_JS)

    next_steps = [
        f"cd {project_dir}",
        "pebble build",
        "pebble install --emulator emery   # or a connected watch",
    ]
    if config:
        next_steps.insert(1, "npm install   # pull in pebble-clay for the config page")

    return {
        "path": project_dir,
        "uuid": uuid_str,
        "kind": kind,
        "language": language,
        "project_type": "moddable" if language == "javascript" else "native",
        "platforms": plats,
        "files_created": files,
        "warnings": warnings,
        "next_steps": next_steps,
    }


# --------------------------------------------------------------------------- #
# project_info
# --------------------------------------------------------------------------- #
def _detect_language(directory: str, pebble: dict[str, Any]) -> str:
    if pebble.get("projectType") == "moddable":
        return "javascript"
    if os.path.isdir(os.path.join(directory, "src", "embeddedjs")):
        return "javascript"
    if os.path.isdir(os.path.join(directory, "src", "rocky")):
        return "javascript"
    return "c"


def project_info(directory: str) -> dict[str, Any]:
    """Parse a scaffolded project's ``package.json`` into a summary dict."""
    data = _load_pkg(directory)
    pebble = data["pebble"]
    language = _detect_language(directory, pebble)
    watchapp = pebble.get("watchapp") or {}
    return {
        "path": os.path.abspath(directory),
        "name": data.get("name"),
        "displayName": pebble.get("displayName"),
        "uuid": pebble.get("uuid"),
        "kind": "watchface" if watchapp.get("watchface") else "watchapp",
        "watchface": bool(watchapp.get("watchface")),
        "language": language,
        "project_type": pebble.get("projectType", "native"),
        "platforms": pebble.get("targetPlatforms", []),
        "capabilities": pebble.get("capabilities", []),
        "messageKeys": pebble.get("messageKeys", []),
        "resources": (pebble.get("resources") or {}).get("media", []),
        "sdkVersion": pebble.get("sdkVersion"),
    }


# --------------------------------------------------------------------------- #
# project_add_resource
# --------------------------------------------------------------------------- #
_RESOURCE_TYPES = {"bitmap": "bitmap", "png": "bitmap", "font": "font", "raw": "raw"}


def project_add_resource(
    directory: str,
    source: str,
    name: str,
    resource_type: str = "bitmap",
    prep_target: str | None = None,
) -> dict[str, Any]:
    """Copy a resource into ``resources/`` and register it in package.json.

    ``source`` is a filesystem path (or, for bitmaps, base64 / a data URI).
    ``resource_type`` is ``"bitmap"`` (a.k.a. png), ``"font"``, or ``"raw"``.
    For bitmaps, if ``prep_target`` is given the image is routed through the
    design tier (:func:`pebble_mcp.images.prep`) -- resized/letterboxed to that
    target and quantized to the Pebble palette -- before being written.
    Returns the updated ``resources.media`` list.
    """
    if resource_type not in _RESOURCE_TYPES:
        raise ValueError(
            f"unknown resource_type {resource_type!r}; expected one of "
            f"{sorted(set(_RESOURCE_TYPES))}"
        )
    media_type = {"bitmap": "bitmap", "font": "font", "raw": "raw"}[_RESOURCE_TYPES[resource_type]]

    data = _load_pkg(directory)
    resources_dir = os.path.join(directory, "resources")
    os.makedirs(resources_dir, exist_ok=True)

    if media_type == "bitmap" and prep_target is not None:
        from pebble_mcp import images as _images

        img = _images.load_image(source)
        prepped = _images.prep(img, prep_target)
        base = os.path.basename(source) if os.sep in source or "/" in source else name
        stem = os.path.splitext(base)[0] or name
        filename = f"{stem}.png"
        dest = os.path.join(resources_dir, filename)
        prepped.save(dest, format="PNG")
    else:
        if not os.path.isfile(source):
            raise ValueError(f"resource source is not a readable file: {source!r}")
        filename = os.path.basename(source)
        dest = os.path.join(resources_dir, filename)
        shutil.copyfile(source, dest)

    rel_file = os.path.join("resources", filename).replace(os.sep, "/")
    entry = {"type": media_type, "name": name, "file": rel_file}

    pebble = data["pebble"]
    resources = pebble.setdefault("resources", {})
    media = resources.setdefault("media", [])
    # Replace an existing entry with the same name, else append.
    media[:] = [m for m in media if m.get("name") != name]
    media.append(entry)
    _save_pkg(directory, data)

    return {"file": rel_file, "media": media}


# --------------------------------------------------------------------------- #
# project_set_meta
# --------------------------------------------------------------------------- #
def project_set_meta(
    directory: str,
    uuid: str | None = None,
    add_platforms: list[str] | None = None,
    remove_platforms: list[str] | None = None,
    capabilities: list[str] | None = None,
    message_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Edit a project's ``pebble`` block in place; return the new block.

    ``uuid`` sets a specific UUID, or generate a fresh uuid4 when it is the
    literal string ``"new"``. ``add_platforms`` / ``remove_platforms`` adjust
    ``targetPlatforms`` (validated, kept in canonical order). ``capabilities``
    and ``message_keys`` replace those lists wholesale.
    """
    data = _load_pkg(directory)
    pebble = data["pebble"]

    if uuid is not None:
        pebble["uuid"] = str(_uuid.uuid4()) if uuid == "new" else uuid

    if add_platforms or remove_platforms:
        current = set(pebble.get("targetPlatforms", []))
        if add_platforms:
            _validate_platforms(add_platforms)
            current |= set(add_platforms)
        if remove_platforms:
            current -= set(remove_platforms)
        pebble["targetPlatforms"] = [p for p in ALL_PLATFORMS if p in current]

    if capabilities is not None:
        pebble["capabilities"] = list(capabilities)

    if message_keys is not None:
        pebble["messageKeys"] = list(message_keys)

    _save_pkg(directory, data)
    return pebble
