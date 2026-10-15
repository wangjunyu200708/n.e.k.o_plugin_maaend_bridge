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


def candidate_interpreters(*, configured: str = "", hint: str = "") -> List[str]:
    """Interpreters worth probing, most specific first.

    ``py -3.11`` is kept as a single string rather than resolved to a path: the
    launcher is a small shim, and resolving it would lose the version pin that
    is the whole reason to prefer it.
    """
    candidates: List[str] = []
    for value in (configured, hint, os.environ.get("NEKO_MAAEND_PYTHON", "")):
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
    for candidate in candidate_interpreters(configured=configured, hint=hint):
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


def resolve_node() -> str:
    """``node`` for the override compiler, or ``""``.

    The compiler is the one part of the executor that shells out to Node
    (``override/override_gen.py``); Node 22.6+ is needed for native TypeScript.
    Only used for the diagnostics the panel shows, so no version check here —
    the compiler itself reports a version problem far better than we could.
    """
    for candidate in ("C:/Program Files/nodejs/node.exe", shutil.which("node") or ""):
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


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
    info: dict = {
        "executor_dir": str(executor),
        "executor_present": (Path(executor) / "panel_server.py").is_file(),
        "override_present": (Path(executor) / "override" / "cli.ts").is_file(),
        "node": resolve_node(),
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
    "DEFAULT_PORT",
    "PYTHON_HINT_FILENAME",
    "candidate_interpreters",
    "describe",
    "executor_dir",
    "hint_file_path",
    "is_elevated",
    "launch_elevated",
    "manual_launcher_path",
    "probe_interpreter",
    "read_python_hint",
    "resolve_interpreter",
    "resolve_node",
    "write_python_hint",
]
