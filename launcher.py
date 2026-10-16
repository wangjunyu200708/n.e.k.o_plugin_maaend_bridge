"""Locating and starting the executor that ships inside this plugin.

The executor is a *separate process*, not a library this plugin imports. That
boundary is deliberate and load-bearing:

* It must run **elevated**. Seize input (``SendInput``) cannot reach the game
  window from a non-elevated process — the log shows successful clicks while
  the game does nothing. The plugin runs inside N.E.K.O., which is not
  elevated, so the plugin can never be the process that drives the game. All it
  can do is *ask Windows* to start one, which is what :func:`launch_elevated`
  does (and why a UAC prompt appears).
* It has its own interpreter requirements (MaaFramework, numpy) that the host's
  bundled Python does not necessarily satisfy, so the interpreter is found by
  probing, not assumed.
* Keeping it as a directory of scripts rather than an importable package means
  a newer executor can be dropped in without touching plugin code.

Everything here is standard library only, and Windows-specific calls are
guarded so the module still imports (and its pure helpers still test) on other
platforms.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

DEFAULT_PORT = 12702
PYTHON_HINT_FILENAME = "python_path.txt"

# Probing costs a real ``import maa``: MaaFramework loads native DLLs, and numpy
# comes with it. That is hundreds of milliseconds, so the probe result is cached
# per interpreter path for the lifetime of the plugin process.
_PROBE_TIMEOUT_S = 25.0
_probe_cache: dict[str, Tuple[bool, str]] = {}


def executor_dir(plugin_dir: Path) -> Path:
    """The bundled executor's working directory (``executor/``)."""
    return Path(plugin_dir) / "executor"


def hint_file_path(executor: Path) -> Path:
    return Path(executor) / PYTHON_HINT_FILENAME


def read_python_hint(executor: Path) -> str:
    """The interpreter path recorded next to the executor, if any.

    Written when someone points the setting at a working interpreter, so the
    manual ``start_panel.bat`` path and the plugin's own button agree on which
    Python to use without the setting having to be read by the batch file.
    """
    path = hint_file_path(executor)
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    # A stray newline or quotes in a hand-edited file would break the .bat.
    return text.strip().strip('"').strip()


def write_python_hint(executor: Path, interpreter: str) -> None:
    try:
        hint_file_path(executor).write_text(interpreter + "\n", encoding="utf-8")
    except OSError:
        pass


VENDOR_DIRNAME = "vendor"
VENDOR_PYTHON_SUBPATH = Path(VENDOR_DIRNAME) / "python" / "python.exe"


def bundled_interpreter(executor: Optional[Path]) -> str:
    """The interpreter that ships inside the plugin, or ``""`` when absent.

    This is what makes the plugin self-contained. MaaFramework, numpy and
    Pillow all travel with it (see ``executor/vendor/README.md``), so a machine
    with no Python installed at all can still run tasks — which also means the
    version of every one of those is ours, not whatever the user happened to
    have.

    Absent is a normal state, not an error: a build may deliberately ship
    without it (it is ~150 MiB), and then a machine-wide interpreter is used.
    """
    if executor is None:
        return ""
    candidate = Path(executor) / VENDOR_PYTHON_SUBPATH
    try:
        return str(candidate) if candidate.is_file() else ""
    except OSError:
        return ""


def candidate_interpreters(
    *, configured: str = "", hint: str = "", executor: Optional[Path] = None
) -> List[str]:
    """Interpreters worth probing, in the order we would rather use them.

    Precedence is deliberate — **explicit > shipped > recorded > ambient**:

    1. ``executor_python`` (the setting) and ``NEKO_MAAEND_PYTHON``: somebody
       said so on purpose.
    2. the interpreter bundled in ``executor/vendor``: the one this build was
       verified against, and the only one that exists on a machine with no
       Python at all.
    3. ``python_path.txt``: recorded from an earlier *successful* probe. Below
       the bundled one on purpose — an install that upgrades to a
       self-contained build has a stale hint pointing at a system interpreter,
       and that must not shadow the runtime we ship.
    4. common install locations, ``py -3.11``, then whatever is on ``PATH``.

    ``py -3.11`` stays a single string rather than being resolved to a path:
    it is a shim, and resolving it would lose the version pin that is the whole
    reason to prefer it.
    """
    candidates: List[str] = []
    for value in (configured, os.environ.get("NEKO_MAAEND_PYTHON", "")):
        text = (value or "").strip().strip('"')
        if text and text not in candidates:
            candidates.append(text)

    for value in (bundled_interpreter(executor), hint):
        text = (value or "").strip().strip('"')
        if text and text not in candidates:
            candidates.append(text)

    local_appdata = os.environ.get("LOCALAPPDATA") or ""
    if local_appdata:
        for suffix in (
            r"Python\pythoncore-3.11-64\python.exe",
            r"Programs\Python\Python311\python.exe",
            r"Programs\Python\Python312\python.exe",
        ):
            candidates.append(str(Path(local_appdata) / suffix))
    candidates.append(r"C:\Python311\python.exe")

    found = shutil.which("py")
    if found:
        candidates.append("py -3.11")
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return candidates


def probe_interpreter(interpreter: str, *, use_cache: bool = True) -> Tuple[bool, str]:
    """Can this interpreter ``import maa``? Returns ``(ok, detail)``.

    Importing is the only honest test: an interpreter existing on PATH says
    nothing about whether MaaFramework was pip-installed into *it*, and that is
    the single most common way this bridge fails to start.
    """
    key = interpreter.strip()
    if use_cache and key in _probe_cache:
        return _probe_cache[key]
    if not key:
        return False, "empty interpreter path"

    argv = _split_launcher(key) + ["-c", "import maa"]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=_PROBE_TIMEOUT_S,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result = (False, f"{type(exc).__name__}: {exc}")
        _probe_cache[key] = result
        return result

    if proc.returncode == 0:
        result = (True, "")
    else:
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        result = (False, tail.splitlines()[-1][:200] if tail else f"exit {proc.returncode}")
    _probe_cache[key] = result
    return result


def _split_launcher(interpreter: str) -> List[str]:
    """``"py -3.11"`` is a command *and* an argument, not a path."""
    text = interpreter.strip()
    if " " in text and not Path(text).exists():
        return text.split()
    return [text]


def resolve_interpreter(
    *,
    configured: str = "",
    executor: Optional[Path] = None,
    use_cache: bool = True,
) -> Tuple[str, str]:
    """First interpreter that passes the probe. ``("", reason)`` when none does.

    ``reason`` is for the log and for the setting's help text; it is never shown
    to the model, which gets a sentence from ``prompts`` instead.
    """
    hint = read_python_hint(executor) if executor is not None else ""
    tried: List[str] = []
    for candidate in candidate_interpreters(
        configured=configured, hint=hint, executor=executor
    ):
        ok, detail = probe_interpreter(candidate, use_cache=use_cache)
        if ok:
            if executor is not None and candidate != hint:
                # Record it so the manual start_panel.bat finds the same one.
                write_python_hint(executor, candidate)
            return candidate, ""
        tried.append(f"{candidate} ({detail})" if detail else candidate)
    if not tried:
        return "", "no candidate interpreters found"
    return "", "none of these can import maa: " + "; ".join(tried[:4])


# The override compiler's runtime files. It used to shell out to Node, which
# made "is Node installed" the question worth asking; it is a pure-Python port
# now (see executor/override/SOURCE.txt), so the question became "are its own
# files there" — a better one, because that can actually be wrong in a way that
# stays invisible: a partial copy or a hand-deleted module makes every task run
# with default options and report nothing.
OVERRIDE_RUNTIME_FILES = (
    "pipeline_override.py",
    "pretask_builder.py",
    "data.json",
    "build_interface.py",
)


def override_compiler_problems(executor: Path) -> List[str]:
    """Missing pieces of the override compiler. Empty means it looks complete."""
    override = Path(executor) / "override"
    return [
        f"缺少 override/{name}"
        for name in OVERRIDE_RUNTIME_FILES
        if not (override / name).is_file()
    ]


def is_elevated() -> bool:
    """Whether *this* process is elevated (the plugin's own, not the panel's).

    The plugin never needs this to decide anything — the panel reports its own
    elevation in ``/api/status`` and that is the only answer that matters. It is
    here so the diagnostics line in the panel can say plainly that N.E.K.O. is
    not the elevated process, which is what the UAC prompt is for.
    """
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def launch_elevated(
    interpreter: str,
    executor: Path,
    *,
    port: int = DEFAULT_PORT,
    maaend_dir: str = "",
    logger: Any = None,
) -> Tuple[bool, str]:
    """Start the panel backend elevated. Returns ``(started, message)``.

    ``ShellExecuteW(..., "runas", ...)`` is the only way to raise a process's
    integrity level from a non-elevated one, and it is asynchronous: it returns
    as soon as Windows has accepted the request (or the user has answered the
    UAC prompt). A success here means *the request was accepted*, not that the
    panel is up — the caller waits for ``/api/ping`` to say that.

    ``python.exe`` rather than ``pythonw.exe``: the panel sets
    ``sys.stdout`` to its log tee and prints to it, and under ``pythonw`` there
    is no stdout to wrap. The console window it opens is also the user's only
    handle on a process they are about to hand the keyboard to.
    """
    if sys.platform != "win32":
        return False, "elevated launch is Windows-only"
    if not interpreter:
        return False, "no interpreter"
    script = Path(executor) / "panel_server.py"
    if not script.is_file():
        return False, f"panel_server.py not found under {executor}"

    argv = _split_launcher(interpreter) + [
        str(script),
        "--port",
        str(int(port)),
    ]
    if maaend_dir:
        argv += ["--maaend-dir", str(maaend_dir)]
    # No --open: the control UI is this plugin's own panel, not a browser tab.
    params = subprocess.list2cmdline(argv)

    try:
        import ctypes

        rc = int(
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", argv[0], params, str(executor), 1  # SW_SHOWNORMAL
            )
        )
    except Exception as exc:  # pragma: no cover - platform specific
        if logger is not None:
            logger.warning("ShellExecuteW raised: {}: {}", type(exc).__name__, exc)
        return False, f"{type(exc).__name__}: {exc}"

    # ShellExecuteW returns >32 on success; 5 is the documented "UAC declined".
    if rc <= 32:
        reason = "the elevation prompt was declined" if rc == 5 else f"ShellExecuteW rc={rc}"
        return False, reason
    if logger is not None:
        logger.info("requested an elevated executor launch (port {})", port)
    return True, ""


ASSET_PACK_DIRNAME = "maaend"
ASSET_PACK_INTERFACE = "interface.json"


def asset_pack_dir(executor: Path, *, configured: str = "") -> Path:
    """Where the asset layer lives: the configured path, else the bundled one."""
    text = (configured or "").strip()
    return Path(text) if text else Path(executor) / ASSET_PACK_DIRNAME


def asset_pack_problems(asset_dir: Path) -> List[str]:
    """What is missing from the asset layer — filesystem only, no parsing.

    This deliberately does **not** read ``interface.json``. What that file says
    (project name, version, task groups, licence) is the executor's own answer
    over ``/api/status``, and parsing it here would mean a second copy of the
    JSONC reader living in the plugin — two implementations of a subtle parser
    is how they drift.

    What this answers instead is the one question the executor *cannot* answer
    for itself: **is a layer even installed**, which matters exactly when the
    executor is not running.

    An empty list means "a layer is there and looks complete enough to start".
    """
    problems: List[str] = []
    if not (asset_dir / ASSET_PACK_INTERFACE).is_file():
        problems.append(f"缺少 {ASSET_PACK_INTERFACE}")
        return problems  # nothing else about this directory is meaningful
    if not (asset_dir / "resource").is_dir():
        problems.append("缺少 resource/")
    agents = asset_dir / "agent"
    try:
        has_agent = agents.is_dir() and next(agents.iterdir(), None) is not None
    except OSError:
        has_agent = False
    if not has_agent:
        problems.append("缺少 agent/ 或其中没有可执行文件")
    return problems


def manual_launcher_path(executor: Path) -> str:
    """The batch file a user can double-click, or ``""`` when absent.

    Shown next to the plugin's own "launch" button so that a user whose UAC
    prompt never appeared (a policy that blocks elevation, a remote session)
    has a concrete next step instead of only an error.
    """
    candidate = Path(executor) / "start_panel.bat"
    try:
        return str(candidate) if candidate.is_file() else ""
    except OSError:
        return ""


def describe(executor: Path, *, configured: str = "") -> dict:
    """A diagnostics blob for the plugin panel. Never raises."""
    override_problems = override_compiler_problems(executor)
    info: dict = {
        "executor_dir": str(executor),
        "executor_present": (Path(executor) / "panel_server.py").is_file(),
        # The compiler is pure Python now, so "present" means *its* files are
        # there. Checking the old Node entry point (`override/cli.ts`) would
        # report a healthy compiler for a directory that cannot compile.
        "override_present": not override_problems,
        "override_problems": override_problems,
        "python_hint": read_python_hint(executor),
        "python_ok": False,
        "python_reason": "",
        "plugin_elevated": is_elevated(),
        "manual_launcher": manual_launcher_path(executor),
    }
    try:
        interpreter, reason = resolve_interpreter(configured=configured, executor=Path(executor))
    except Exception as exc:  # pragma: no cover - defensive
        interpreter, reason = "", f"{type(exc).__name__}: {exc}"
    info["python"] = interpreter
    info["python_ok"] = bool(interpreter)
    info["python_reason"] = reason
    return info


__all__ = [
    "ASSET_PACK_DIRNAME",
    "ASSET_PACK_INTERFACE",
    "DEFAULT_PORT",
    "PYTHON_HINT_FILENAME",
    "VENDOR_DIRNAME",
    "VENDOR_PYTHON_SUBPATH",
    "asset_pack_dir",
    "asset_pack_problems",
    "bundled_interpreter",
    "candidate_interpreters",
    "describe",
    "executor_dir",
    "hint_file_path",
    "is_elevated",
    "launch_elevated",
    "manual_launcher_path",
    "override_compiler_problems",
    "probe_interpreter",
    "read_python_hint",
    "resolve_interpreter",
    "write_python_hint",
]
