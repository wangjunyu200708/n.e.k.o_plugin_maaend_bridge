"""Locating and describing the executor, and what it says about an asset layer.

Most of this module is about *absence*: the plugin has to answer "is the
executor installed / is an asset layer installed / is the override compiler
complete" while the executor is not running — which is exactly when nothing else
can answer for it. So the assertions are mostly about what a missing or swapped
piece looks like from here.

Two properties are pinned deliberately, both of them things a later change could
undo without any test going red otherwise:

* **Nothing assumes which game the asset layer is for.** Swapping in another
  MaaFramework project has to be a file operation, not a code change.
* **Nothing in the runtime path shells out to Node.** The override compiler was
  ported to Python; a stray `node` reference would only surface as a machine
  that cannot dispatch.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from maaend_bridge_plugin import launcher

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "executor"
BUNDLED_PACK = EXECUTOR / "maaend"


@pytest.fixture(autouse=True)
def _no_real_probe(monkeypatch):
    """Never run the real interpreter probe.

    ``resolve_interpreter`` shells out to ``<python> -c "import maa"`` for every
    candidate, with a 25 s timeout each. A test that lets it run does not fail —
    it just takes minutes, which is worse, because it gets normalised.
    """
    monkeypatch.setattr(
        launcher, "probe_interpreter", lambda *_a, **_k: (False, "stubbed")
    )


# ---------------------------------------------------------------------------
# Where the asset layer is
# ---------------------------------------------------------------------------


def test_the_bundled_layer_is_the_default():
    assert launcher.asset_pack_dir(EXECUTOR) == BUNDLED_PACK


def test_a_configured_path_wins_over_the_bundled_layer():
    """The escape hatch that makes removing the shipped assets a config change.

    Pointing this at a directory outside the plugin is how a build ships with no
    game assets at all, while everything else keeps working.
    """
    configured = launcher.asset_pack_dir(EXECUTOR, configured="  D:/packs/other  ")
    assert configured == Path("D:/packs/other")


def test_the_shipped_layer_is_complete():
    assert launcher.asset_pack_problems(BUNDLED_PACK) == []


def test_an_empty_directory_reports_the_missing_manifest(tmp_path):
    problems = launcher.asset_pack_problems(tmp_path)
    assert problems, "an empty directory is not a complete asset layer"
    assert launcher.ASSET_PACK_INTERFACE in problems[0]
    # Everything else about the directory is unknowable without the manifest,
    # so it must not pile on guesses.
    assert len(problems) == 1


def test_a_manifest_without_resource_or_agent_is_reported(tmp_path):
    (tmp_path / launcher.ASSET_PACK_INTERFACE).write_text("{}", encoding="utf-8")
    problems = launcher.asset_pack_problems(tmp_path)
    assert any("resource" in p for p in problems)
    assert any("agent" in p for p in problems)


def test_an_empty_agent_directory_counts_as_missing(tmp_path):
    (tmp_path / launcher.ASSET_PACK_INTERFACE).write_text("{}", encoding="utf-8")
    (tmp_path / "resource").mkdir()
    (tmp_path / "agent").mkdir()
    assert any("agent" in p for p in launcher.asset_pack_problems(tmp_path))


def test_a_different_projects_layer_is_not_a_special_case(tmp_path):
    """Lay out another game's layer and the plugin sees a perfectly good one.

    This is the swappability requirement as a test: the plugin must not know or
    care whose asset layer it is. The window identity that used to be hardcoded
    lives in *this* file's `controller[].win32` block, read by the executor.
    """
    (tmp_path / launcher.ASSET_PACK_INTERFACE).write_text(
        '{\n  // another project entirely\n'
        '  "name": "SomeOtherGame", "version": "v1",\n'
        '  "controller": [{"name": "Win32-Front", "type": "Win32",\n'
        '    "win32": {"class_regex": "OtherClass", "window_regex": "OtherGame"}}],\n'
        '  "resource": [{"name": "official", "path": ["./resource"]}],\n'
        '  "agent": [{"child_exec": "agent/thing"}],\n}',
        encoding="utf-8",
    )
    (tmp_path / "resource").mkdir()
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "thing.exe").write_bytes(b"")
    assert launcher.asset_pack_problems(tmp_path) == []


# ---------------------------------------------------------------------------
# The override compiler: pure Python, and complete
# ---------------------------------------------------------------------------


def test_the_shipped_override_compiler_is_complete():
    assert launcher.override_compiler_problems(EXECUTOR) == []


def test_the_compiler_check_names_the_files_it_wants(tmp_path):
    (tmp_path / "override").mkdir()
    problems = launcher.override_compiler_problems(tmp_path)
    assert len(problems) == len(launcher.OVERRIDE_RUNTIME_FILES)
    for name in launcher.OVERRIDE_RUNTIME_FILES:
        assert any(name in p for p in problems)


def test_the_compiler_check_looks_for_python_not_the_node_entry_point():
    """``cli.ts`` is a maintenance-period control group, not a runtime file.

    It is still in the tree on purpose; what matters is that its presence is not
    what makes the compiler "ready".
    """
    assert "cli.ts" not in launcher.OVERRIDE_RUNTIME_FILES
    assert (EXECUTOR / "override" / "cli.ts").is_file(), "cli.ts should still ship"


# ---------------------------------------------------------------------------
# The interpreter that ships with the plugin
# ---------------------------------------------------------------------------


def test_the_bundled_interpreter_is_found_where_it_should_be():
    found = launcher.bundled_interpreter(EXECUTOR)
    assert found.endswith("python.exe")
    assert "vendor" in found


def test_no_bundled_interpreter_is_a_normal_answer(tmp_path):
    """A build may deliberately ship without it (~150 MiB), and the plugin has
    to fall back to a machine-wide interpreter rather than claiming a problem."""
    assert launcher.bundled_interpreter(tmp_path) == ""
    assert launcher.bundled_interpreter(None) == ""


def test_a_build_without_the_bundled_runtime_still_proposes_candidates(tmp_path):
    assert launcher.candidate_interpreters(executor=tmp_path), (
        "without the bundled runtime there must still be something to try"
    )


def test_explicit_beats_shipped_beats_recorded():
    """The precedence that makes an upgrade to a self-contained build take.

    A machine that already ran an earlier, non-self-contained version has a
    ``python_path.txt`` pointing at a system interpreter. Putting the recorded
    hint above the bundled one would keep using it forever, which defeats the
    point of shipping the runtime at all.
    """
    candidates = launcher.candidate_interpreters(
        configured="D:/explicit/python.exe",
        hint="D:/recorded/python.exe",
        executor=EXECUTOR,
    )
    bundled = launcher.bundled_interpreter(EXECUTOR)
    assert candidates[0] == "D:/explicit/python.exe"
    assert bundled in candidates
    assert candidates.index(bundled) < candidates.index("D:/recorded/python.exe")


# ---------------------------------------------------------------------------
# The vendored runtime covers what the executor actually imports
# ---------------------------------------------------------------------------


def _third_party_imports() -> set:
    """Every non-stdlib, non-local module the executor's runtime modules import.

    Derived from the source on purpose. A hand-maintained list of "packages we
    vendor" is exactly what let Pillow be missed: ``smoke_resource`` exercises
    the resource layer but never ``panel_server.screenshot_png()``, so a missing
    Pillow passed every acceptance run and would have surfaced much later as a
    503 the first time somebody asked for a picture. Scanning imports cannot go
    stale.
    """
    import ast
    import sys

    local = {
        "runner", "packmeta", "override_gen", "pipeline_override",
        "pretask_builder", "build_interface",
    }
    stdlib = set(sys.stdlib_module_names)
    needed: set = set()
    for rel in ("panel_server.py", "runner.py", "packmeta.py"):
        tree = ast.parse((EXECUTOR / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in stdlib or name in local or name == "__future__":
                    continue
                needed.add(name)
    return needed


def test_every_third_party_import_the_executor_needs_is_vendored():
    needed = _third_party_imports()
    assert needed, "the scan found nothing — it is probably broken"
    vendored = {
        entry.name
        for entry in (EXECUTOR / "vendor" / "site-packages").iterdir()
        if entry.is_dir()
    }
    missing = sorted(name for name in needed if name not in vendored)
    assert not missing, (
        f"the vendored runtime is missing {missing}; the executor imports them "
        f"but nothing ships them"
    )


def test_numpy_is_not_optional_for_the_binding():
    """Pins the finding that ruled out the "cut numpy, save 53 MiB" option.

    numpy is imported at module level by ``maa`` itself, so it cannot be
    dropped without patching the wheel — the screenshot conversion is just the
    reason *we* import it too.
    """
    vendored_maa = EXECUTOR / "vendor" / "site-packages" / "maa"
    if not vendored_maa.is_dir():
        pytest.skip("vendored runtime not present")
    module_level = []
    for path in vendored_maa.rglob("*.py"):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(("import numpy", "from numpy")):
                module_level.append(path.name)
    assert len(module_level) >= 5, (
        f"only {len(module_level)} module-level numpy imports found in maa; if the "
        f"binding stopped importing numpy at import time, revisit whether it must ship"
    )


def test_the_vendored_runtime_has_numpy_libs_as_a_sibling():
    """``numpy.libs`` is not matched by ``numpy*`` and not importable.

    Dropping it gives ``DLL load failed while importing _multiarray_umath`` —
    a failure that names a completely different module than the one missing.
    """
    libs = EXECUTOR / "vendor" / "site-packages" / "numpy.libs"
    if not (EXECUTOR / "vendor" / "site-packages" / "numpy").is_dir():
        pytest.skip("vendored runtime not present")
    assert libs.is_dir()


def test_the_path_file_points_at_site_packages_and_the_stdlib_zip():
    """Both halves of the ``_pth`` gotcha, which fails silently either way."""
    pth = EXECUTOR / "vendor" / "python" / "python311._pth"
    if not pth.is_file():
        pytest.skip("vendored runtime not present")
    body = pth.read_text(encoding="utf-8")
    assert "python311.zip" in body, (
        "a dotted version there (python3.11.9.zip) makes the stdlib unfindable "
        "and python.exe dies with '<no Python frame>'"
    )
    assert "site-packages" in body, (
        "an embeddable Python with a _pth is isolated: PYTHONPATH is ignored, "
        "so the path has to be listed here"
    )


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------


def test_describe_reports_the_python_compiler_and_never_mentions_node():
    """Regression guard: Node must not creep back into the runtime path."""
    info = launcher.describe(EXECUTOR)
    assert "node" not in info
    assert info["override_problems"] == []
    assert info["override_present"] is True
    assert info["executor_present"] is True
    assert info["manual_launcher"].endswith("start_panel.bat")


def test_describe_on_an_empty_directory_stays_answerable(tmp_path):
    """Every field is something the panel can print, or the panel blanks out."""
    info = launcher.describe(tmp_path)
    assert info["executor_present"] is False
    assert info["override_present"] is False
    assert info["override_problems"]
    assert info["manual_launcher"] == ""
    assert info["python_ok"] is False


def test_describe_never_raises_even_on_a_nonexistent_directory():
    info = launcher.describe(Path("E:/definitely/not/here"))
    assert info["executor_present"] is False
