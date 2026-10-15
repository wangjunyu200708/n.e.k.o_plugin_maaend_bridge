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
    # The repository is n.e.k.o_plugin_<id>; the plugin's own module is the
    # repository root, so the id has to be the suffix of the directory.
    assert ROOT.name == f"n.e.k.o_plugin_{PLUGIN_ID}"


def test_entry_points_at_this_plugin_class():
    manifest = _manifest()
    assert manifest["plugin"]["entry"] == f"plugin.plugins.{PLUGIN_ID}:MaaendBridgePlugin"


def test_runtime_timeout_is_within_the_accepted_range():
    runtime = _manifest().get("plugin_runtime", {})
    # The host defaults to 10 s when the key is absent.
    timeout = runtime.get("timeout", 10)
    assert 0 < timeout <= 300


def test_ui_surface_declares_a_context_that_has_a_provider():
    """Without a matching ``@ui.context`` the host refuses every action call."""
    sources = "\n".join(
        (ROOT / name).read_text(encoding="utf-8") for name in ("__init__.py",)
    )
    surfaces = _manifest()["plugin"]["ui"]["guide"]
    for surface in surfaces:
        entry = ROOT / surface["entry"]
        assert entry.is_file(), f"{surface['entry']} is missing"
        if "context" in surface:
            assert f'@ui.context(id="{surface["context"]}")' in sources


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
