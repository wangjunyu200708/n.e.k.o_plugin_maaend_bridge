"""Build (or verify) the Python runtime that ships inside the plugin.

The plugin carries its own interpreter so that no machine-wide install is
needed. This script is the one place that knows what goes in and where each
piece came from — see ``executor/vendor/README.md`` for why each choice was
made.

    # build from a distribution zip plus the packages already on this machine
    python scripts/vendor_python_runtime.py --python-zip <python-3.11.9-embed-amd64.zip> \\
        --site-packages %LOCALAPPDATA%/Python/pythoncore-3.11-64/Lib/site-packages \\
        --site-packages %APPDATA%/Python/Python311/site-packages

    # verify a built tree without needing the sources again
    python scripts/vendor_python_runtime.py --check

    # what is actually in there
    python scripts/vendor_python_runtime.py --report

Two things this script deliberately does **not** do: it does not download
anything (give it the zip), and it does not execute the vendored interpreter.
Building an environment and trusting it are separate steps with separate
evidence; the acceptance commands are printed at the end of a build and are
listed in ``executor/vendor/README.md``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VENDOR = ROOT / "executor" / "vendor"
PYDIR = VENDOR / "python"
SPDIR = VENDOR / "site-packages"
LICDIR = VENDOR / "licenses"
PTH_NAME = "python311._pth"

# ``(package directory, licence glob)``. The interpreter lives in one
# site-packages and the dependencies in another (pip puts dependencies in the
# per-user one), which is the single most common way a hand-built copy comes
# out half-full — hence ``--site-packages`` being repeatable and this list
# being the contract.
REQUIRED_PACKAGES = (
    ("maa", "maafw-*.dist-info"),          # MaaFramework's Python binding
    ("numpy", "numpy-*.dist-info"),
    ("numpy.libs", None),                  # sibling of numpy, NOT matched by numpy*
    ("strenum", "strenum-*.dist-info"),
    ("PIL", "pillow-*.dist-info"),         # panel_server's screenshot path
)

# The embeddable distribution ships its own licence; the rest come from the
# distributions so the shipped set stays attributable.
PTH_BODY = (
    # ASCII only: `._pth` is read before the stdlib is up, so nothing here may
    # depend on how the file's encoding gets decoded.
    "python311.zip\n"      # stdlib. NOT "python3.11.9.zip" - see vendor/README.md
    ".\n"
    "..\\site-packages\n"
    "..\\..\n"             # executor/          - the scripts, run by path
    "..\\..\\override\n"   # executor/override/ - their sibling modules
    "\n"
    "# site-packages is listed here rather than passed via PYTHONPATH:\n"
    "# an embeddable Python with a _pth file is isolated and silently ignores\n"
    "# PYTHONPATH. `import site` is deliberately absent - this site-packages\n"
    "# has no .pth files, so site processing has nothing to do.\n"
    "#\n"
    "# The last two entries exist for the same reason and one more. Isolated mode\n"
    "# also leaves the script's own directory off sys.path: sys.path[0] is this\n"
    "# zip, and the `.` above means the interpreter's directory, not the caller's.\n"
    "# Without them a script could only reach its own siblings by calling\n"
    "# sys.path.insert first - and that is a statement, so every import after it\n"
    "# trips E402. The plugin market's verify workflow runs ruff with\n"
    "# --ignore-noqa, which leaves no way to silence E402, so the paths belong\n"
    "# here rather than in the scripts.\n"
)


def _dir_size(path: Path) -> Tuple[int, int]:
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def _find_licences(roots: List[Path], pattern: str) -> List[Path]:
    out: List[Path] = []
    for root in roots:
        for dist_info in root.glob(pattern):
            out += list(dist_info.glob("LICENSE*")) + list(dist_info.glob("licenses/*"))
    return out


def build(python_zip: Path, site_packages: List[Path]) -> int:
    if not python_zip.is_file():
        print(f"[FAIL] no such zip: {python_zip}")
        return 1
    if not site_packages:
        print("[FAIL] at least one --site-packages is required")
        return 1

    for directory in (PYDIR, SPDIR, LICDIR):
        directory.mkdir(parents=True, exist_ok=True)

    # 1) the interpreter itself
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(PYDIR)
    print(f"  python/          <- {python_zip.name} ({len(archive.namelist())} entries)")

    interpreter = PYDIR / "python.exe"
    if not interpreter.is_file():
        print(f"[FAIL] {python_zip.name} is not an embeddable distribution "
              f"(no python.exe after extracting)")
        return 1

    # 2) the packages, from every roots given
    for name, _ in REQUIRED_PACKAGES:
        source = next((root / name for root in site_packages if (root / name).is_dir()), None)
        if source is None:
            print(f"[FAIL] {name} not found in any --site-packages; searched "
                  f"{[str(r) for r in site_packages]}")
            return 1
        shutil.copytree(source, SPDIR / name, dirs_exist_ok=True)
        files, size = _dir_size(SPDIR / name)
        print(f"  site-packages/{name:12} <- {source.parent}  {files:>5} files  {size/1024/1024:7.1f} MiB")

    # 3) metadata + licences
    for _, pattern in REQUIRED_PACKAGES:
        if pattern is None:
            continue
        for dist_info in [di for root in site_packages for di in root.glob(pattern)]:
            shutil.copytree(dist_info, SPDIR / dist_info.name, dirs_exist_ok=True)
        for item in _find_licences(site_packages, pattern):
            dest = LICDIR / f"{item.parent.parent.name.split('-')[0]}-{item.name}"
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    print(f"  licenses/        <- {len(list(LICDIR.iterdir()))} item(s)")

    # 4) the path file
    (PYDIR / PTH_NAME).write_text(PTH_BODY, encoding="utf-8")
    print(f"  python/{PTH_NAME} written")

    return report()


def check() -> int:
    """Is the checked-in tree complete? Static checks only — no execution."""
    problems: List[str] = []
    if not (PYDIR / "python.exe").is_file():
        problems.append("python/python.exe is missing")
    pth = PYDIR / PTH_NAME
    if not pth.is_file():
        problems.append(f"python/{PTH_NAME} is missing")
    else:
        body = pth.read_text(encoding="utf-8")
        entries = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if "python311.zip" not in entries:
            problems.append(f"{PTH_NAME} does not list python311.zip "
                            f"(a dotted version there breaks the stdlib silently)")
        if "..\\site-packages" not in entries:
            problems.append(f"{PTH_NAME} does not list site-packages "
                            f"(PYTHONPATH is ignored in isolated mode)")
        # Isolated mode also keeps the script's own directory off sys.path, so
        # the executor's two directories have to be named here or every script
        # needs a sys.path.insert — which then makes all its imports E402.
        for needed in ("..\\..", "..\\..\\override"):
            if needed not in entries:
                problems.append(f"{PTH_NAME} does not list {needed} "
                                f"(executor scripts could not import their siblings)")
    for name, _ in REQUIRED_PACKAGES:
        if not (SPDIR / name).is_dir():
            problems.append(f"site-packages/{name} is missing")
    if problems:
        print("[FAIL] the vendored runtime is incomplete:")
        for item in problems:
            print("   ", item)
        return 1
    files, size = _dir_size(VENDOR)
    print(f"[OK] vendored runtime looks complete ({files} files, {size/1024/1024:.1f} MiB)")
    print("     now run the acceptance commands in executor/vendor/README.md "
          "— with the vendored interpreter, not the machine's")
    return 0


def report() -> int:
    if not VENDOR.is_dir():
        print(f"[FAIL] no vendored runtime at {VENDOR}")
        return 1
    files, size = _dir_size(VENDOR)
    print(f"\nvendored runtime: {VENDOR}")
    print(f"  {files} files, {size/1024/1024:.1f} MiB")
    for name, _ in REQUIRED_PACKAGES:
        if (SPDIR / name).is_dir():
            count, bytes_ = _dir_size(SPDIR / name)
            print(f"    site-packages/{name:12} {count:>5} files  {bytes_/1024/1024:7.1f} MiB")
        else:
            print(f"    site-packages/{name:12} MISSING")
    print(f"    licenses/                  {len(list(LICDIR.iterdir())) if LICDIR.is_dir() else 0} item(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--python-zip", type=Path,
                    help="python-3.11.x-embed-amd64.zip from python.org")
    ap.add_argument("--site-packages", type=Path, action="append", default=[],
                    help="a site-packages to take packages from; repeatable, and "
                         "you usually need two (the interpreter's and the per-user one)")
    ap.add_argument("--check", action="store_true", help="verify without rebuilding")
    ap.add_argument("--report", action="store_true", help="list what is there")
    args = ap.parse_args()

    if args.report:
        return report()
    if args.check:
        return check()
    if not args.python_zip:
        ap.error("--python-zip is required to build (or use --check / --report)")
    return build(args.python_zip, args.site_packages)


if __name__ == "__main__":
    sys.exit(main())
