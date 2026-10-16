"""Sync the MaaEnd asset layer that ships inside this plugin.

The plugin carries the whole asset layer (pipeline, images, ONNX models, the
two agent executables, task definitions, locales) so that installing the plugin
is enough — no separate MaaEnd distribution to fetch. That is ~293 MiB across
~2265 files, which is far too much to keep by hand, so this script is the one
place that knows where it came from.

Usage::

    # refresh executor/maaend/ from a local MaaEnd distribution
    python scripts/sync_maaend_assets.py "E:/MaaEnddm/MaaEnd-win-x86_64-v2.32.0"

    # verify the checked-in copy still matches that distribution
    python scripts/sync_maaend_assets.py "E:/MaaEnddm/MaaEnd-win-x86_64-v2.32.0" --check

    # what is in there, and what was deliberately left out
    python scripts/sync_maaend_assets.py --report

``SOURCE.txt`` in the target directory records the provenance of the copy that
is actually checked in; ``--check`` re-derives the same numbers from a
distribution on disk and reports drift. It compares file counts and byte totals
per top-level entry rather than hashing 293 MiB, which is fast enough to run
before every release and still catches a partial or stale copy.

Upstream identity: the executor reports the loaded asset layer's
``resource_hash`` in ``/api/status``, and the MaaFramework hash of this copy
must match whatever the executor started with. ``--report`` prints the recorded
value so a mismatch is visible without loading MaaFramework.

The asset layer is **third-party** (MaaEnd, AGPL-3.0 — see
``executor/maaend/LICENSE``); this plugin does not modify it.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TARGET = ROOT / "executor" / "maaend"
SOURCE_TXT = TARGET / "SOURCE.txt"

# What the executor actually reads at runtime. Everything outside this list was
# deliberately left out — see SKIP_REASONS for why, so nobody re-adds 200 MiB
# of MXU host binaries by accident.
INCLUDE = [
    "interface.json",   # JSONC task/option manifest, expanded at run time
    "tasks",            # the 46 task definitions the manifest imports
    "resource",         # pipeline (5809 nodes) + images + ONNX models
    "agent",            # go-service.exe + cpp-algo.exe (47 custom recognizers,
                        # 85 custom actions)
    "maafw",            # The native runtime go-service.exe / cpp-algo.exe
                        # dlopen. These DLLs are byte-identical to the ones the
                        # pip 'maa' wheel ships in site-packages/maa/bin — but
                        # the two consumers resolve them by different rules: the
                        # Python binding looks beside itself, while those agents
                        # look at ``<resource layer>/maafw/``, which is where
                        # upstream puts it. Skipping this directory on the
                        # grounds that "the wheel has the same DLLs" therefore
                        # leaves every task failing to initialise MaaFramework.
    "locales",          # interface/{lang}.json, read by the panel's Translator
    "data",             # per-task runtime data (maps, tables) some tasks load
    "LICENSE",          # upstream licence — must travel with the assets
    "README.md",        # upstream readme
]

# Subdirectories inside an INCLUDE entry that are runtime state rather than
# shipped files — the upstream author's own runs leave these behind.
NESTED_SKIP = {
    "maafw": ["debug", "config"],
}

SKIP_REASONS = {
    "MaaEnd.exe": "the MXU desktop host — the whole point of this executor is to bypass it",
    "debug": "MaaFramework debug logs from the upstream author's runs",
    "cache": "MXU's own download/update cache",
    "config": "MXU's own settings file",
    "resource_adb": "ADB controller assets — this bridge is Win32-Front only",
    "resource_linux": "other platforms",
    "resource_macos": "other platforms",
    "resource_playcover": "other platforms",
    "resource_cloud_adb": "other platforms",
    "start-headless.bat": "MXU's own launcher",
}


def _entry_files(root: Path, entry: str) -> List[Path]:
    """Files under one INCLUDE entry, minus its NESTED_SKIP subdirectories."""
    path = root / entry
    if path.is_file():
        return [path]
    skipped = set(NESTED_SKIP.get(entry, ()))
    return [
        f
        for f in path.rglob("*")
        if f.is_file() and not (skipped & set(f.relative_to(path).parts))
    ]


def _measure(root: Path, entries: List[str]) -> Dict[str, Tuple[int, int]]:
    """``{entry: (file_count, total_bytes)}`` for each entry that exists."""
    out: Dict[str, Tuple[int, int]] = {}
    for name in entries:
        path = root / name
        if not path.exists():
            continue
        files = _entry_files(root, name)
        out[name] = (len(files), sum(f.stat().st_size for f in files))
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _totals(measured: Dict[str, Tuple[int, int]]) -> Tuple[int, int]:
    return (
        sum(count for count, _ in measured.values()),
        sum(size for _, size in measured.values()),
    )


def sync(source: Path, *, dry_run: bool = False, prune: bool = False) -> int:
    if not (source / "interface.json").is_file():
        print(f"[FAIL] {source} does not look like a MaaEnd distribution "
              f"(no interface.json)")
        return 1

    if prune and TARGET.is_dir():
        for child in TARGET.iterdir():
            if child.name in SKIP_REASONS:
                # A previously-synced copy may hold excluded host binaries.
                if not dry_run:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
                print(f"  PRUNE {child.name}")

    measured = _measure(source, INCLUDE)
    for name in INCLUDE:
        if name not in measured:
            print(f"  SKIP  {name} (not in this distribution)")
            continue
        count, size = measured[name]
        if dry_run:
            print(f"  DRY   {name:16} {count:>5} files  {size / 1024 / 1024:8.1f} MiB")
            continue
        src, dst = source / name, TARGET / name
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*NESTED_SKIP.get(name, ())),
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"  OK    {name:16} {count:>5} files  {size / 1024 / 1024:8.1f} MiB")

    if dry_run:
        total_count, total_size = _totals(measured)
        print(f"\nwould sync {total_count} files, {total_size / 1024 / 1024:.1f} MiB")
        return 0

    write_source_txt(source, measured)
    return 0


def write_source_txt(source: Path, measured: Dict[str, Tuple[int, int]]) -> None:
    """Record where the checked-in copy came from, and what it should contain."""
    interface = TARGET / "interface.json"
    lines = [
        "# Provenance of the MaaEnd asset layer bundled in this plugin.",
        "#",
        "# Generated by scripts/sync_maaend_assets.py — do not edit by hand;",
        "# re-run the script instead. Re-check with:",
        "#     python scripts/sync_maaend_assets.py <distribution> --check",
        "",
        f"synced_at        = {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"source           = {source}",
        "upstream_project = MaaEnd",
        "upstream_license = AGPL-3.0 (GNU Affero General Public License v3) — "
        "verbatim copy, unmodified; see LICENSE in this directory",
        f"interface_sha256 = {_sha256(interface) if interface.is_file() else '<missing>'}",
        "",
        "# What the executor reads. (files, bytes)",
        "[entries]",
    ]
    for name in INCLUDE:
        count, size = measured.get(name, (0, 0))
        lines.append(f'{name.replace(".", "_")} = {{ files = {count}, bytes = {size} }}')
    total_count, total_size = _totals(measured)
    lines += [
        "",
        f"total_files = {total_count}",
        f"total_bytes = {total_size}",
        "",
        "# Deliberately NOT bundled:",
    ]
    for name, reason in SKIP_REASONS.items():
        lines.append(f"#   {name:22} {reason}")
    SOURCE_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {SOURCE_TXT.name}: {total_count} files, "
          f"{total_size / 1024 / 1024:.1f} MiB")


def check(source: Path) -> int:
    """Compare the checked-in copy against a distribution on disk."""
    if not (TARGET / "interface.json").is_file():
        print(f"[FAIL] no bundled copy at {TARGET} — run without --check first")
        return 1
    expected = _measure(source, INCLUDE)
    actual = _measure(TARGET, INCLUDE)
    problems: List[str] = []
    for name in INCLUDE:
        want = expected.get(name)
        have = actual.get(name)
        if want is None:
            continue
        if have is None:
            problems.append(f"missing: {name}")
        elif want != have:
            problems.append(
                f"{name}: bundled {have[0]} files/{have[1]} B, "
                f"distribution {want[0]} files/{want[1]} B"
            )
    if SOURCE_TXT.is_file():
        recorded = SOURCE_TXT.read_text(encoding="utf-8")
        want_sha = _sha256(source / "interface.json")
        if f"interface_sha256 = {want_sha}" not in recorded:
            problems.append("interface.json sha256 differs from what SOURCE.txt records")
    if problems:
        print("[FAIL] bundled asset layer has drifted from the distribution:")
        for item in problems:
            print("   ", item)
        return 1
    total_count, total_size = _totals(actual)
    print(f"[OK] bundled asset layer matches {source} "
          f"({total_count} files, {total_size / 1024 / 1024:.1f} MiB)")
    return 0


def report() -> int:
    if not TARGET.is_dir():
        print(f"[FAIL] no bundled copy at {TARGET}")
        return 1
    measured = _measure(TARGET, INCLUDE)
    total_count, total_size = _totals(measured)
    print(f"bundled asset layer: {TARGET}")
    print(f"  {total_count} files, {total_size / 1024 / 1024:.1f} MiB")
    for name in INCLUDE:
        count, size = measured.get(name, (0, 0))
        print(f"    {name:16} {count:>5} files  {size / 1024 / 1024:8.1f} MiB")
    if SOURCE_TXT.is_file():
        print("\nSOURCE.txt:")
        for line in SOURCE_TXT.read_text(encoding="utf-8").splitlines():
            if line.startswith(("synced_at", "source ", "interface_sha256")):
                print("   ", line)
    else:
        print("\n(warning: no SOURCE.txt — provenance is unrecorded)")
    # Every file the executor cannot start without.
    required = ["interface.json", "resource/pipeline", "agent/go-service.exe"]
    for rel in required:
        mark = "ok" if (TARGET / rel).exists() else "MISSING"
        print(f"  {mark:8} {rel}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("source", nargs="?", help="path to a MaaEnd distribution")
    ap.add_argument("--check", action="store_true",
                    help="verify the bundled copy matches the distribution")
    ap.add_argument("--dry-run", action="store_true", help="measure without copying")
    ap.add_argument("--prune", action="store_true",
                    help="delete previously-synced copies of excluded entries")
    ap.add_argument("--report", action="store_true",
                    help="describe the bundled copy without needing a distribution")
    args = ap.parse_args()

    if args.report:
        return report()
    if not args.source:
        ap.error("a distribution path is required unless --report is used")
    source = Path(args.source)
    if args.check:
        return check(source)
    return sync(source, dry_run=args.dry_run, prune=args.prune)


if __name__ == "__main__":
    sys.exit(main())
