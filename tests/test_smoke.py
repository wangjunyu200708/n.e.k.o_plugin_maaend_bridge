"""Manifest and layout invariants.

``plugin.id``, the source directory name and the ``entry`` package all have to
stay aligned: the loader tolerates a mismatch during development, but packaging
and production installs do not.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "maaend_bridge"


def _manifest() -> dict:
    return tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))


def test_manifest_exists_and_parses():
    assert (ROOT / "plugin.toml").is_file()
    assert _manifest()


def test_plugin_id_matches_the_source_directory():
    manifest = _manifest()
    assert manifest["plugin"]["id"] == PLUGIN_ID
    # The plugin is loaded from whatever directory it sits in, and there are two
    # legitimate names for it:
    #
    #   n.e.k.o_plugin_<id>   the standalone repository, which is how it is
    #                         published and cloned
    #   <id>                  mounted into the host as plugin/plugins/<id>,
    #                         which is what `neko-plugin sync` does to a dev
    #                         checkout and what the market's verify workflow
    #                         does before it runs this suite
    #
    # The invariant worth keeping is that the id and the directory agree — a
    # third name means the manifest and the layout have drifted apart.
    assert ROOT.name in (PLUGIN_ID, f"n.e.k.o_plugin_{PLUGIN_ID}"), (
        f"directory is named {ROOT.name!r}, which matches neither the plugin id "
        f"({PLUGIN_ID}) nor the standalone repository convention"
    )


def test_entry_points_at_this_plugin_class():
    manifest = _manifest()
    assert manifest["plugin"]["entry"] == f"plugin.plugins.{PLUGIN_ID}:MaaendBridgePlugin"


def test_runtime_timeout_is_within_the_accepted_range():
    runtime = _manifest().get("plugin_runtime", {})
    # The host defaults to 10 s when the key is absent.
    timeout = runtime.get("timeout", 10)
    assert 0 < timeout <= 300


def _all_surfaces():
    """Every declared UI surface, with the manifest section it came from.

    The host only walks ``panel`` / ``guide`` / ``docs``
    (``ui_query_service._build_manifest_surfaces``); anything else is silently
    never rendered.
    """
    ui = _manifest()["plugin"]["ui"]
    for kind in ("panel", "guide", "docs"):
        for surface in ui.get(kind, []):
            yield kind, surface


def test_ui_surfaces_use_only_kinds_the_host_renders():
    ui = _manifest()["plugin"]["ui"]
    known = {"enabled", "panel", "guide", "docs"}
    assert set(ui) <= known, f"unknown ui sections are never rendered: {set(ui) - known}"


def test_the_control_surface_is_a_panel_not_a_tutorial():
    """``guide`` / ``docs`` render under the plugin's tutorial tab."""
    kinds = [kind for kind, _surface in _all_surfaces()]
    assert kinds, "no UI surface declared at all"
    assert "panel" in kinds, (
        "the control surface is not declared under [[plugin.ui.panel]], so it "
        f"lands in the tutorial tab (declared: {kinds})"
    )


def test_hosted_tsx_surfaces_declare_their_mode():
    """The host defaults ``mode`` to ``static``.

    With that default a ``.tsx`` entry is handed to the iframe as an http static
    asset — the browser requests a TypeScript file and renders nothing. Only
    ``hosted-tsx`` goes through the ``@neko/plugin-ui`` compiler, and nothing in
    the manifest infers it from the file extension.
    """
    for kind, surface in _all_surfaces():
        entry = surface["entry"]
        if entry.endswith((".tsx", ".jsx")):
            assert surface.get("mode") == "hosted-tsx", (
                f"plugin.ui.{kind} entry {entry!r} must set mode = \"hosted-tsx\" "
                f"(got {surface.get('mode')!r}, which renders a blank iframe)"
            )


def test_every_surface_entry_file_exists():
    for kind, surface in _all_surfaces():
        assert (ROOT / surface["entry"]).is_file(), (
            f"plugin.ui.{kind} entry {surface['entry']!r} is missing"
        )


def test_ui_surface_declares_a_context_that_has_a_provider():
    """Without a matching ``@ui.context`` the host refuses every action call."""
    sources = (ROOT / "__init__.py").read_text(encoding="utf-8")
    for kind, surface in _all_surfaces():
        if "context" not in surface:
            continue
        assert f'@ui.context(id="{surface["context"]}")' in sources, (
            f"plugin.ui.{kind} declares context {surface['context']!r} with no "
            f"@ui.context provider — every props.api.call would fail"
        )


def test_every_locale_file_is_valid_json_with_the_manifest_keys():
    import json

    i18n = _manifest()["plugin"]["i18n"]
    locales_dir = ROOT / i18n["locales_dir"]
    files = sorted(locales_dir.glob("*.json"))
    assert files, "no locale files found"
    assert i18n["default_locale"] + ".json" in [f.name for f in files]
    for path in files:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        assert "plugin.name" in bundle, f"{path.name} has no plugin.name"
        assert "plugin.description" in bundle, f"{path.name} has no plugin.description"


def test_the_surface_has_no_bare_imports():
    """Hosted TSX may only import ``@neko/plugin-ui`` and sibling files."""
    import re

    surface = ROOT / "surfaces" / "maaend.tsx"
    text = surface.read_text(encoding="utf-8")
    specifiers = re.findall(r"""\bfrom\s+["']([^"']+)["']""", text)
    specifiers += re.findall(r"""^\s*import\s+["']([^"']+)["']""", text, re.MULTILINE)
    assert specifiers, "no imports found — is the surface still there?"
    for specifier in specifiers:
        assert specifier == "@neko/plugin-ui" or specifier.startswith("."), (
            f"unsupported import in the hosted surface: {specifier}"
        )


def test_no_python_file_has_a_byte_order_mark():
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "vendor" in path.parts:
            continue
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), f"{path} has a BOM"
