"""MCP tool registrations for the project-scaffolding tier.

Wraps :mod:`pebble_mcp.project` as FastMCP tools. Exposes ``register(mcp)``
per the established pattern so ``server.py`` can wire this module in without
this file touching ``server.py`` itself.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from pebble_mcp import project as _project


def register(mcp: FastMCP) -> None:
    """Register the project create/manage tools on ``mcp``."""

    @mcp.tool()
    def project_new(
        name: str,
        kind: str = "watchface",
        language: str = "c",
        platforms: list[str] | None = None,
        companion: bool = False,
        config: bool = False,
        dest_dir: str = ".",
    ) -> dict:
        """Scaffold a new, immediately buildable Pebble project.

        Writes the files directly (pure Python -- no ``pebble new-project``
        shell-out), so it works on hosts without the CLI. The result compiles
        to a ``.pbw`` with ``pebble build`` as-is.

        ``kind`` -- ``"watchface"`` (default; sets ``watchapp.watchface``) or
        ``"watchapp"``. ``language`` -- ``"c"`` (default) emits
        ``src/c/<name>.c`` (a working digital-clock TextLayer for a watchface,
        or a Window+TextLayer for a watchapp) + ``wscript``; ``"javascript"``
        emits an **Alloy** project (``projectType: "moddable"``, the modern
        on-watch JS engine): ``src/c/mdbl.c`` glue + ``src/embeddedjs/main.js``
        + ``manifest.json`` + ``src/pkjs/index.js``.

        ``platforms`` -- defaults to all seven for C
        (aplite/basalt/chalk/diorite/emery/flint/gabbro). Alloy runs only on
        the Moddable-XS platforms **emery** (Time 2) and **gabbro** (Round 2),
        so JavaScript projects default to and are clamped to those (dropped
        platforms are reported in ``warnings``).

        ``companion`` adds a phone-side ``src/pkjs/index.js`` (implied for
        Alloy). ``config`` adds a pebble-clay settings page
        (``src/pkjs/config.js`` + the dependency + companion). ``dest_dir`` is
        the parent directory; the project is created in
        ``<dest_dir>/<slug(name)>``.

        Returns ``path``, ``uuid`` (a fresh uuid4), ``kind``, ``language``,
        ``platforms``, ``files_created``, ``warnings``, and ``next_steps``.
        """
        return _project.project_new(
            name,
            kind=kind,
            language=language,
            platforms=platforms,
            companion=companion,
            config=config,
            dest_dir=dest_dir,
        )

    @mcp.tool()
    def project_info(dir: str) -> dict:
        """Summarize an existing Pebble project from its ``package.json``.

        Returns ``name``, ``displayName``, ``uuid``, ``kind``/``watchface``,
        detected ``language`` (C vs JavaScript/Alloy, inferred from
        ``projectType`` and ``src/`` layout), ``project_type``,
        ``platforms``, ``capabilities``, ``messageKeys``, ``resources`` (the
        media list), and ``sdkVersion``. Raises a clear error if ``dir`` has
        no ``package.json`` or no ``pebble`` block.
        """
        return _project.project_info(dir)

    @mcp.tool()
    def project_add_resource(
        dir: str,
        source: str,
        name: str,
        resource_type: str = "bitmap",
        prep_target: str | None = None,
    ) -> dict:
        """Add a resource to a project and register it in ``package.json``.

        Copies ``source`` into ``<dir>/resources/`` (created if missing) and
        appends/updates a ``resources.media`` entry with the given ``name`` and
        ``resource_type`` (``"bitmap"`` | ``"font"`` | ``"raw"``). For bitmaps,
        ``source`` may also be base64 / a ``data:`` URI, and passing
        ``prep_target`` (e.g. ``"emery"``, ``"menu-icon"``) routes the image
        through the design tier -- resize/letterbox + palette quantize -- before
        writing it. Returns the updated ``media`` list and the written ``file``.
        """
        return _project.project_add_resource(
            dir,
            source,
            name,
            resource_type=resource_type,
            prep_target=prep_target,
        )

    @mcp.tool()
    def project_set_meta(
        dir: str,
        uuid: str | None = None,
        add_platforms: list[str] | None = None,
        remove_platforms: list[str] | None = None,
        capabilities: list[str] | None = None,
        message_keys: list[str] | None = None,
    ) -> dict:
        """Edit a project's ``pebble`` metadata block in place.

        ``uuid`` sets a specific UUID (or the literal ``"new"`` to generate a
        fresh uuid4). ``add_platforms`` / ``remove_platforms`` adjust
        ``targetPlatforms`` (validated against the seven known platforms, kept
        in canonical order). ``capabilities`` and ``message_keys`` replace those
        lists wholesale. Returns the updated ``pebble`` block.
        """
        return _project.project_set_meta(
            dir,
            uuid=uuid,
            add_platforms=add_platforms,
            remove_platforms=remove_platforms,
            capabilities=capabilities,
            message_keys=message_keys,
        )
