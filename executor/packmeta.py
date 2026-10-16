"""Read what a MaaFramework asset layer says about itself.

A MaaFramework project ships one ``interface.json`` describing everything the
executor needs to drive it: which window to attach to, which screencap/input
methods to use, which resource bundles and agent executables to load, what the
task groups are called, and what the project is (name, version, licence,
upstream repo).

``interface.json`` at the top level carries all of that; **only ``task`` is
split out** behind the 68 ``import`` entries (that is why the shipped file's
``task`` array is empty and `override/build_interface.py` exists). So this
module reads the raw file and deliberately does **not** expand imports — it must
stay cheap and Node-free, because it runs on the *status* path, not the task
path.

**Why this file exists.** The executor used to hardcode the window identity::

    WINDOW_CLASS = "UnityWndClass"
    WINDOW_NAME  = "Endfield"

Those two constants are the whole reason the asset layer could not be swapped.
They are also redundant — the shipped ``interface.json`` already declares them::

    "controller": [{
      "name": "Win32-Front", "type": "Win32", "permission_required": true,
      "win32": { "class_regex": "UnityWndClass", "window_regex": "Endfield",
                 "screencap": "ScreenDC", "mouse": "Seize", "keyboard": "Seize" }
    }]

Reading them instead of hardcoding them is what makes "delete this game's
assets and drop in another project's" an operation instead of a refactor.

Note there is deliberately **no fallback** to the old constants. Defaulting to
Endfield's window for a pack that does not declare a Win32 controller would
reproduce exactly the silent-wrong behaviour this module removes: the executor
would attach to the wrong window (or none) and report nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

INTERFACE_FILENAME = "interface.json"

# The controller entry this bridge uses. It is Win32-only by design: Seize
# (SendInput) is what makes the executor work on a desktop game, and it is why
# the executor must run elevated at all. An asset layer that only declares ADB
# controllers cannot be driven from here, and saying so plainly is better than
# trying.
PREFERRED_WIN32_CONTROLLER = "Win32-Front"
WIN32_CONTROLLER_TYPE = "Win32"


def load_jsonc(path: Path) -> Dict[str, Any]:
    """Parse a JSONC file (comments + trailing commas).

    Written as a character scan rather than a regex because ``//`` is legal
    *inside* a JSON string — ``"github": "https://github.com/MaaEnd/MaaEnd"``
    would be corrupted by a line-based strip. Handles ``//``, ``/* */`` and
    trailing commas.
    """
    text = Path(path).read_text(encoding="utf-8")
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            out.append(c)
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append(text[i : i + 2])
                    i += 2
                    continue
                if text[i] == '"':
                    break
                out.append(text[i])
                i += 1
            out.append('"')
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    stripped = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level is not an object")
    return data


@dataclass(frozen=True)
class Win32Controller:
    """The ``controller[].win32`` block: how to attach to the game window."""

    name: str
    class_regex: str
    window_regex: str
    screencap: str
    mouse: str
    keyboard: str

    @property
    def describes_a_window(self) -> bool:
        return bool(self.class_regex or self.window_regex)


@dataclass(frozen=True)
class PackMeta:
    """Everything a MaaFramework project declares about itself.

    ``problems`` is the point of the whole dataclass: an asset layer that is
    present but incomplete (no ``resource/``, no agent executable, no Win32
    controller) has to be reportable *as such*, not discovered later as a task
    failing for no visible reason.
    """

    directory: Path
    name: str = ""
    version: str = ""
    license_ref: str = ""
    github: str = ""
    languages: Tuple[str, ...] = ()
    win32: Optional[Win32Controller] = None
    resource_dirs: Tuple[str, ...] = ()
    resource_hash: str = ""
    agent_execs: Tuple[str, ...] = ()
    groups: Tuple[Dict[str, Any], ...] = ()
    problems: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def label(self) -> str:
        """What to call this asset layer in a sentence.

        The project's own ``name`` — a brand name, so it is not translated, and
        it is the one string that stays correct when the layer is swapped.
        """
        return self.name or self.directory.name

    def group_label(self, name: str) -> str:
        """The declared label for a task group, else the raw id.

        Labels are ``$group.<name>.label`` i18n references, so the caller gets
        the reference back unless it resolves them; returning the id keeps the
        UI honest instead of printing an unresolved ``$`` key.
        """
        for group in self.groups:
            if str(group.get("name")) == name:
                label = str(group.get("label") or "")
                return label if label and not label.startswith("$") else name
        return name


def _pick_win32_controller(controllers: Any) -> Optional[Win32Controller]:
    if not isinstance(controllers, list):
        return None
    win32 = [
        entry
        for entry in controllers
        if isinstance(entry, dict) and str(entry.get("type")) == WIN32_CONTROLLER_TYPE
    ]
    if not win32:
        return None
    chosen = next(
        (e for e in win32 if str(e.get("name")) == PREFERRED_WIN32_CONTROLLER), win32[0]
    )
    block = chosen.get("win32")
    if not isinstance(block, dict):
        block = {}
    return Win32Controller(
        name=str(chosen.get("name") or ""),
        class_regex=str(block.get("class_regex") or ""),
        window_regex=str(block.get("window_regex") or ""),
        screencap=str(block.get("screencap") or "ScreenDC"),
        mouse=str(block.get("mouse") or "Seize"),
        keyboard=str(block.get("keyboard") or "Seize"),
    )


def _resolve(asset_dir: Path, entry: Any) -> Optional[Path]:
    """Resolve a declared relative path, refusing to escape the pack."""
    if not isinstance(entry, str) or not entry.strip():
        return None
    candidate = (asset_dir / entry.lstrip("./")).resolve()
    try:
        candidate.relative_to(asset_dir.resolve())
    except ValueError:
        return None  # the declaration points outside the pack; ignore it
    return candidate


def _collect_problems(asset_dir: Path, win32: Optional[Win32Controller]) -> List[str]:
    problems: List[str] = []
    if win32 is None:
        problems.append(
            f"{INTERFACE_FILENAME} 没有声明 {WIN32_CONTROLLER_TYPE} 控制器"
            f"（本桥只走 {PREFERRED_WIN32_CONTROLLER}）"
        )
    elif not win32.describes_a_window:
        problems.append(
            f"控制器 {win32.name or '?'} 没有声明 class_regex / window_regex，"
            "无法定位游戏窗口"
        )
    if not (asset_dir / "resource").is_dir():
        problems.append("缺少 resource/ 目录")
    agents = asset_dir / "agent"
    if not agents.is_dir() or not any(agents.iterdir()):
        problems.append("缺少 agent/ 目录或其中没有可执行文件")
    return problems


def read_pack(asset_dir: Optional[Path]) -> Optional[PackMeta]:
    """Read the asset layer's self-description. ``None`` when there is no pack.

    "No pack" (nothing installed / the directory was removed) and "a pack that
    is broken" are different answers on purpose — the first is a normal state
    the UI explains, the second is a list of what is wrong.
    """
    if asset_dir is None:
        return None
    directory = Path(asset_dir)
    interface = directory / INTERFACE_FILENAME
    if not interface.is_file():
        return None
    try:
        data = load_jsonc(interface)
    except Exception:
        return PackMeta(
            directory=directory,
            problems=(f"{INTERFACE_FILENAME} 读不出来（JSONC 解析失败）",),
        )

    win32 = _pick_win32_controller(data.get("controller"))
    resources = data.get("resource")
    resource_dirs: List[str] = []
    resource_hash = ""
    if isinstance(resources, list):
        for entry in resources:
            if not isinstance(entry, dict):
                continue
            for path in entry.get("path") or []:
                if isinstance(path, str):
                    resource_dirs.append(path)
            if not resource_hash and isinstance(entry.get("hash"), str):
                resource_hash = entry["hash"]

    agents = data.get("agent")
    agent_execs: List[str] = []
    if isinstance(agents, list):
        for entry in agents:
            if isinstance(entry, dict) and isinstance(entry.get("child_exec"), str):
                agent_execs.append(entry["child_exec"])

    languages = data.get("languages")
    return PackMeta(
        directory=directory,
        name=str(data.get("name") or ""),
        version=str(data.get("version") or ""),
        license_ref=str(data.get("license") or ""),
        github=str(data.get("github") or ""),
        languages=tuple(sorted(languages)) if isinstance(languages, dict) else (),
        win32=win32,
        resource_dirs=tuple(resource_dirs),
        resource_hash=resource_hash,
        agent_execs=tuple(agent_execs),
        groups=tuple(g for g in (data.get("group") or []) if isinstance(g, dict)),
        problems=tuple(_collect_problems(directory, win32)),
    )


def declared_paths(pack: PackMeta) -> List[Path]:
    """The declared resource/agent paths, resolved inside the pack."""
    out: List[Path] = []
    for rel in (*pack.resource_dirs, *pack.agent_execs):
        resolved = _resolve(pack.directory, rel)
        if resolved is not None:
            out.append(resolved)
    return out


__all__ = [
    "INTERFACE_FILENAME",
    "PREFERRED_WIN32_CONTROLLER",
    "PackMeta",
    "Win32Controller",
    "declared_paths",
    "load_jsonc",
    "read_pack",
]
