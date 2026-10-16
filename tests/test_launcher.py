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
