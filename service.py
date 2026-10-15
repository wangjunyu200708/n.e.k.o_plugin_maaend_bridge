"""Task semantics for the maaend_bridge plugin.

This module owns everything between "the dialog LLM asked for a task" and "the
character said what happened": the one-task slot, the panel's instance
identity, the poll loop that decides when a task is over, and the cues that
carry progress and outcomes back into the conversation.

It does **not** import the plugin SDK — the logger and the ``push_message``
sink are injected — so it runs unmodified in a plain pytest process with a
fake client.

Three things about the panel shape everything below.

1. **`/api/status` is the source of truth, and there is no event stream we
   trust.** The panel does expose ``/api/logs`` (SSE), but it is a raw,
   process-global log bus: ids are local to the process and restart at 1, there
   is no per-task framing, and the stream self-terminates after an hour.
   Everything needed here is in ``/api/status``: the live ``current.nodes``
   list, the terminal ``last_result``, and the panel's own resolution verdict.
   So the plugin polls; it never subscribes.

2. **The token *is* the panel's instance identity.** The API has no session
   id, but the panel regenerates ``panel_token.txt`` on every start. A token
   that differs from the one we dispatched under is proof that the process
   that was running our task is gone — and a gone process cannot report what
   happened to that task. So a token change mid-task turns into "unknown",
   never into "finished" and never into an automatic re-dispatch.

3. **A task ends with one of three verdicts, and only one of them is
   success.** ``last_result.succeeded`` is success; ``failed`` is failure;
   neither means the run stopped early (the panel's own timeout, a stop
   request, a lost window). Reporting the third as either of the first two is
   the defect this plugin is most careful about.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from . import launcher, prompts
from .client import (
    MaaendAuthError,
    MaaendClient,
    MaaendError,
    MaaendForbidden,
    MaaendNotReady,
    MaaendProtocolError,
    MaaendRejected,
    MaaendTimeout,
    MaaendUnreachable,
    normalise_base_url,
)

DEFAULT_BASE_URL = "http://127.0.0.1:12702"
TOKEN_FILENAME = "panel_token.txt"
PANEL_DIR_NAME = ".workbuddy"

# The panel reports ``idle`` (never initialised), ``ready`` and ``busy``.
STATE_IDLE = "idle"
STATE_READY = "ready"
STATE_BUSY = "busy"

# Cue priorities. Progress is narration and must lose to anything the user
# just said; a finished or broken task has to be able to speak over it.
PRIORITY_PROGRESS = 2
PRIORITY_COMPLETION = 6
PRIORITY_ALERT = 8

# Consecutive failed status polls before an in-flight task's outcome is
# declared unknown. A local panel that refuses connections twice in a row,
# two seconds apart, is gone; one miss is a scheduling hiccup.
LOST_AFTER_FAILURES = 3

# Node names are pipeline identifiers; more than a handful in one narration is
# noise rather than information.
PROGRESS_NODES_PER_CUE = 4
# Hard cap on nodes buffered while the throttle is closed, so a long silent
# stretch cannot grow the list without bound.
PROGRESS_NODES_BUFFER = 32

# Cache lifetime for the task catalogue. It only changes when the resource
# layer is updated, which is a restart-and-redeploy event.
CATALOGUE_TTL_S = 300.0

# Extra grace on top of the panel-side task timeout before our own wait budget
# gives up, so the panel always gets to report first when it stops a task.
WAIT_BUDGET_GRACE_S = 120.0


def default_panel_dir() -> str:
    """The panel's ``.workbuddy`` directory, to the extent it can be guessed.

    There is no registry entry for this — the executor is a working copy of
    the MaaEnd resource repository, and the path differs per machine. The
    guesses below cover the layouts seen in practice; ``panel_dir`` in the
    settings is the reliable answer and the hint that names it is worth more
    than any of these.
    """
    env = (os.environ.get("MAAEND_PANEL_DIR") or "").strip()
    if env:
        return env
    candidates = [
        Path("E:/MaaEnddm/MaaEnd-2/.workbuddy"),
        Path("D:/MaaEnddm/MaaEnd-2/.workbuddy"),
        Path.home() / "MaaEnd-2" / ".workbuddy",
    ]
    for candidate in candidates:
        try:
            if (candidate / TOKEN_FILENAME).is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    return default


# Emoji, punctuation and whitespace carry no identity in a task name. ``\w``
# under Unicode semantics keeps CJK and drops emoji, which is exactly the
# split wanted here; ``_`` is removed too so ``Daily_Rewards`` and the
# resource's ``DailyRewards`` collapse together.
_MATCH_DROP_RE = re.compile(r"[^\w]+|_+", re.UNICODE)


def normalise_match_text(value: Any) -> str:
    return _MATCH_DROP_RE.sub("", _as_str(value)).lower()


# The model relays the user's own phrasing, and real labels are not substrings
# of real sentences: 「帮我领取日常奖励」 vs the label 「日常奖励领取」, or
# 「刷一下信用点」 vs 「信用点购物」. Substring matching alone turns every one
# of those into "no such task, here are 46", which is a wasted round trip for
# the commonest way a user talks. So a scoring pass runs last.
#
# Two signals, because they fail differently. Character bigrams are precise but
# need two characters of agreement, so 「关掉游戏」 (which shares only 游戏 with
# 「关闭游戏（安卓端）」) scores too low. Set overlap over single characters
# catches that, at the price of matching on one shared character — so it is
# discounted to three quarters, which puts a one-character coincidence below
# the bar while a three-of-four match clears it.
_FUZZY_MIN_SCORE = 0.5
# The runner-up has to be clearly worse than the winner, or the answer is
# "ambiguous" rather than a favourite picked on a fractional difference.
_FUZZY_MARGIN = 1.25


def _bigrams(text: str) -> set:
    if len(text) < 2:
        return set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _fuzzy_score(query: str, candidate: str) -> float:
    """How much of the *query* the candidate accounts for, in ``[0, 1]``.

    Normalised by the query, not the candidate: the question is "which task is
    this sentence about", and a long label should not be penalised for having
    words the user did not say.
    """
    if not query or not candidate:
        return 0.0
    query_grams = _bigrams(query)
    cand_grams = _bigrams(candidate)
    best = 0.0
    if query_grams and cand_grams:
        best = len(query_grams & cand_grams) / len(query_grams)
    shared = len(set(query) & set(candidate)) / len(set(query))
    return max(best, shared * 0.75)


@dataclass
class ActiveTask:
    """The single in-flight task slot.

    ``started`` is the panel's own ``current.started`` timestamp, not ours —
    it is how the watch loop proves that the ``current`` block it is reading
    belongs to the task it dispatched, and not to something the browser panel
    started in the meantime.
    """

    task_name: str
    label: str
    claimed_at: float
    started: float = 0.0
    timeout_s: float = 0.0
    nodes_seen: int = 0
    pending_nodes: List[str] = field(default_factory=list)
    last_progress_cue: float = 0.0
    stop_requested: bool = False
    finished: bool = False

    def elapsed_s(self, now_status: Optional[Dict[str, Any]] = None) -> float:
        """Elapsed seconds, preferring the panel's own figure.

        The panel computes ``current.elapsed`` against its own clock; ours is
        only a fallback for the window between dispatch and the first poll.
        """
        if isinstance(now_status, dict):
            value = _as_float(now_status.get("elapsed"), -1.0)
            if value >= 0:
                return value
        return max(0.0, time.monotonic() - self.claimed_at)


class MaaendService:
    """Task slot + poll loop + narration, with the panel behind one client."""

    def __init__(
        self,
        *,
        logger: Any = None,
        push_message_fn: Optional[Callable[..., Any]] = None,
        plugin_dir: Optional[Path] = None,
    ) -> None:
        self._logger = logger
        self._push_message = push_message_fn
        self._lang = prompts.DEFAULT_LANG
        # Where the bundled executor lives (``<plugin>/executor``). Supplied by
        # the facade; only used to locate and launch the panel, never imported.
        self._plugin_dir = Path(plugin_dir) if plugin_dir else None

        self._base_url = DEFAULT_BASE_URL
        self._panel_dir = ""
        self._token_file = ""
        self._http_timeout = 10.0
        self._prepare_timeout = 90.0
        self._poll_interval = 2.0
        self._default_timeout_minutes = 30
        self._max_timeout_minutes = 120
        self._task_wait_timeout = 3600.0
        self._progress_min_interval = 10.0

        # Executor launch / diagnostics / preview / log mirror.
        # ``_maaend_dir`` empty means "let the panel use the asset layer it
        # ships with" (``executor/maaend``) — the plugin only passes
        # ``--maaend-dir`` when someone deliberately points it elsewhere.
        self._maaend_dir = ""
        self._executor_python = ""
        self._launch_timeout = 90.0
        self._screenshot_max_width = 560
        self._log_buffer_lines = 400
        self._log_stream_enabled = True

        self._client: Optional[MaaendClient] = None
        self._stale_clients: List[MaaendClient] = []
        self._active: Optional[ActiveTask] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._lock: Optional[asyncio.Lock] = None
        # The token the last successful exchange happened under. Doubles as the
        # panel's instance id — see the module docstring.
        self._panel_instance = ""
        self._catalogue: List[Dict[str, Any]] = []
        self._catalogue_at = 0.0
        self._last_status: Dict[str, Any] = {}
        self._last_error = ""

        # Log mirror. ``seq`` is *ours* and monotonic for the life of this
        # plugin process; the panel's own ``id`` restarts at 1 whenever the
        # panel process restarts, so it cannot be a cursor the panel hands back
        # to us. Kept separate from the task channel on purpose: two consumers
        # sharing one "instance id" would eat each other's restart signal.
        self._log_lines: Deque[Dict[str, Any]] = deque(maxlen=400)
        self._log_seq = 0
        self._log_instance = ""
        self._log_cursor = 0
        self._log_task: Optional[asyncio.Task] = None
        self._log_streaming = False

    def set_plugin_dir(self, plugin_dir: Any) -> None:
        """Where the bundled ``executor/`` sits. Called once by the facade."""
        if plugin_dir:
            self._plugin_dir = Path(plugin_dir)

    def executor_dir(self) -> Optional[Path]:
        if self._plugin_dir is None:
            return None
        return launcher.executor_dir(self._plugin_dir)

    # ==================================================================
    # Configuration
    # ==================================================================

    def set_lang(self, lang: str) -> None:
        self._lang = lang or prompts.DEFAULT_LANG

    def configure(self, cfg: Optional[Dict[str, Any]]) -> None:
        """Apply the ``[maaend]`` settings section. Pure, no I/O, no restart.

        Changing ``base_url`` here does rebuild the client on the next call
        rather than tearing down a live one: nothing is connected between
        calls, so there is no connection to invalidate. A change that lands
        while a task is running deliberately does not disturb that task's
        watch loop — it keeps talking to the panel it dispatched to.
        """
        section = cfg if isinstance(cfg, dict) else {}

        base_url = normalise_base_url(section.get("base_url", DEFAULT_BASE_URL))
        if base_url != self._base_url:
            # Nothing is connected between calls, so there is no live
            # connection to invalidate — only a pool to hand back. Parking it
            # here keeps ``configure`` synchronous for the config_change hook
            # while still closing the sockets, on the next async call.
            if self._client is not None:
                self._stale_clients.append(self._client)
            self._client = None
            self._catalogue = []
            self._catalogue_at = 0.0
        self._base_url = base_url

        self._panel_dir = _as_str(section.get("panel_dir")).strip()
        self._token_file = _as_str(section.get("token_file")).strip()
        self._http_timeout = max(1.0, _as_float(section.get("http_timeout_seconds"), 10.0))
        self._prepare_timeout = max(
            self._http_timeout, _as_float(section.get("prepare_timeout_seconds"), 90.0)
        )
        self._poll_interval = min(
            30.0, max(0.5, _as_float(section.get("poll_interval_seconds"), 2.0))
        )
        self._default_timeout_minutes = max(
            0, _as_int(section.get("default_timeout_minutes"), 30)
        )
        self._max_timeout_minutes = max(
            1, _as_int(section.get("max_timeout_minutes"), 120)
        )
        self._task_wait_timeout = max(
            self._poll_interval * 4,
            _as_float(section.get("task_wait_timeout_seconds"), 3600.0),
        )
        self._progress_min_interval = max(
            0.0, _as_float(section.get("progress_min_interval_seconds"), 10.0)
        )

        # Empty means "use the asset layer shipped inside the plugin".
        self._maaend_dir = _as_str(section.get("maaend_dir")).strip()
        self._executor_python = _as_str(section.get("executor_python")).strip()
        self._launch_timeout = min(
            300.0, max(10.0, _as_float(section.get("panel_launch_timeout_seconds"), 90.0))
        )
        self._screenshot_max_width = min(
            1280, max(160, _as_int(section.get("screenshot_max_width"), 560))
        )
        lines = min(5000, max(50, _as_int(section.get("log_buffer_lines"), 400)))
        if lines != self._log_lines.maxlen:
            # Rebuild rather than resize: ``deque`` has no resize, and dropping
            # the oldest is exactly what a smaller cap means anyway.
            kept = list(self._log_lines)[-lines:]
            self._log_lines = deque(kept, maxlen=lines)
        self._log_stream_enabled = _as_bool(section.get("log_stream_enabled"), True)

    # ==================================================================
    # Transport plumbing — the token is re-read every attempt, never cached
    # ==================================================================

    def resolve_token_path(self) -> Optional[Path]:
        """Where ``panel_token.txt`` should be, given the settings.

        Returning ``None`` and returning a path that does not exist are
        different states: the first is "this plugin was never told where the
        panel lives" (a settings problem), the second is "the panel has not
        started yet" (a routine problem). Only the first is worth a hint in
        the message the user sees.
        """
        explicit = self._token_file.strip()
        if explicit:
            return Path(explicit)
        panel_dir = self._panel_dir.strip() or default_panel_dir()
        if not panel_dir:
            return None
        return Path(panel_dir) / TOKEN_FILENAME

    async def _read_token(self) -> str:
        """Read the panel token from disk. Returns ``""`` when absent.

        Read per attempt rather than captured at startup: the token does not
        exist until the user first starts the panel, and it is replaced every
        time the panel restarts. A captured token would mean "restart N.E.K.O.
        after starting the panel", which is exactly the bootstrapping trap the
        per-attempt read removes.
        """
        path = self.resolve_token_path()
        if path is None:
            return ""
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError):
            return ""
        except OSError as exc:
            self._log_warning("token read failed: {}", type(exc).__name__)
            return ""
        return text.strip()

    async def _ensure_client(self) -> MaaendClient:
        if self._stale_clients:
            stale, self._stale_clients = self._stale_clients, []
            for client in stale:
                await client.close()
        if self._client is None:
            self._client = MaaendClient(
                self._base_url,
                http_timeout=self._http_timeout,
                connect_timeout=min(3.0, self._http_timeout),
            )
        if not self._client.is_open:
            await self._client.open()
        return self._client

    async def close(self) -> None:
        """Cancel the watch loop and close the HTTP client."""
        task, self._watch_task = self._watch_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        client, self._client = self._client, None
        if client is not None:
            await client.close()
        for stale in self._stale_clients:
            await stale.close()
        self._stale_clients.clear()

    async def probe_status(self) -> bool:
        """Cheap liveness probe (``/api/ping``, no token). Never raises."""
        try:
            client = await self._ensure_client()
        except Exception:
            return False
        return await client.ping()

    # ==================================================================
    # Reads
    # ==================================================================

    async def _status(self) -> Dict[str, Any]:
        """One authenticated ``/api/status``. Raises :class:`MaaendError`."""
        client = await self._ensure_client()
        token = await self._read_token()
        if not token:
            raise MaaendAuthError("no token file")
        body = await client.status(token)
        self._panel_instance = token
        return body

    async def catalogue(self, *, force: bool = False) -> List[Dict[str, Any]]:
        """The task catalogue, cached. Raises :class:`MaaendError`.

        An empty list is never a valid answer here — the resource layer always
        carries dozens of tasks — so a failure to fetch is raised rather than
        degraded to "no tasks", which a caller could not tell from "no such
        task".
        """
        now = time.monotonic()
        if not force and self._catalogue and (now - self._catalogue_at) < CATALOGUE_TTL_S:
            return self._catalogue
        client = await self._ensure_client()
        token = await self._read_token()
        if not token:
            raise MaaendAuthError("no token file")
        tasks = await client.tasks(token)
        self._panel_instance = token
        self._catalogue = tasks
        self._catalogue_at = now
        return tasks

    async def task_options(self, task_name: str) -> Dict[str, Any]:
        client = await self._ensure_client()
        token = await self._read_token()
        if not token:
            raise MaaendAuthError("no token file")
        return await client.options(token, task_name)

    @staticmethod
    def resolve_task(
        query: str, tasks: List[Dict[str, Any]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Match what the model said against the catalogue.

        Returns ``(name, [])`` on a unique match, or ``("", candidates)`` when
        the answer is ambiguous or absent. The name is the resource's own
        ``name`` field — never a guess assembled from the label — because the
        panel keys its task map on that exact string.

        Four passes, strongest evidence first: the exact code, the exact label,
        substring containment in either direction, then fuzzy overlap. The
        answer is strict even though the search is generous: two plausible
        tasks come back as ambiguous with both listed, never as a favourite
        chosen on a fractional difference.
        """
        raw = _as_str(query).strip()
        if not raw:
            return "", []
        lowered = raw.lower()
        for task in tasks:
            if _as_str(task.get("name")).strip().lower() == lowered:
                return _as_str(task.get("name")).strip(), []

        needle = normalise_match_text(raw)
        if not needle:
            return "", []

        exact = [t for t in tasks if normalise_match_text(t.get("label")) == needle]
        if len(exact) == 1:
            return _as_str(exact[0].get("name")).strip(), []
        if len(exact) > 1:
            return "", exact

        # Below two characters there is nothing to weigh: one shared character
        # is a coincidence, not a name, and dispatching the wrong automation is
        # far more expensive than asking again. Exact matches already ran.
        if len(needle) < 2:
            return "", []

        hits: List[Dict[str, Any]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            label_n = normalise_match_text(task.get("label"))
            name_n = normalise_match_text(task.get("name"))
            if not label_n and not name_n:
                continue
            if label_n == needle or name_n == needle:
                hits.append(task)
            elif needle in label_n or needle in name_n or (label_n and label_n in needle):
                hits.append(task)
        if len(hits) == 1:
            return _as_str(hits[0].get("name")).strip(), []
        if len(hits) > 1:
            return "", hits

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            score = _fuzzy_score(needle, normalise_match_text(task.get("label")))
            if score >= _FUZZY_MIN_SCORE:
                scored.append((score, task))
        if not scored:
            return "", []
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score = scored[0][0]
        contenders = [task for score, task in scored if score * _FUZZY_MARGIN >= best_score]
        if len(contenders) == 1:
            return _as_str(contenders[0].get("name")).strip(), []
        return "", contenders

    async def get_status(self) -> Dict[str, Any]:
        """Panel-facing snapshot. Never raises; reports failures as fields.

        This is what the UI context, the ``maaend_status`` entry, and every
        error hint read, so it has to answer when the panel is down as
        cheerfully as when it is up.
        """
        snapshot: Dict[str, Any] = {
            "connected": False,
            "token_ok": False,
            "state": "unknown",
            "ready": False,
            "elevated": None,
            "focused": None,
            "minimized": None,
            "resolution_ok": None,
            "resolution": None,
            "current_task": "",
            "current_label": "",
            "current_nodes": [],
            "elapsed": 0.0,
            "last_result": None,
            "task_count": 0,
            "panel_instance": self._panel_instance,
            "active_task": self._active.task_name if self._active else "",
            "last_error": self._last_error,
        }
        try:
            body = await self._status()
        except MaaendError as exc:
            snapshot["token_ok"] = not isinstance(exc, (MaaendAuthError, MaaendUnreachable))
            snapshot["last_error"] = exc.code
            snapshot["connected"] = await self.probe_status()
            snapshot["summary"] = prompts.t(
                "STATUS_SUMMARY",
                lang=self._lang,
                state_label=prompts.t(
                    "STATUS_PREPARING" if snapshot["connected"] else "STATUS_NOT_CONNECTED",
                    lang=self._lang,
                ),
            )
            return snapshot

        snapshot["connected"] = True
        snapshot["token_ok"] = True
        snapshot["last_error"] = ""
        self._last_status = body
        snapshot["state"] = _as_str(body.get("state"), "unknown") or "unknown"
        snapshot["ready"] = body.get("ready") is True
        snapshot["elevated"] = body.get("elevated") if isinstance(body.get("elevated"), bool) else None
        snapshot["focused"] = body.get("focused") if isinstance(body.get("focused"), bool) else None
        snapshot["minimized"] = (
            body.get("minimized") if isinstance(body.get("minimized"), bool) else None
        )
        resolution = body.get("resolution")
        snapshot["resolution"] = resolution if isinstance(resolution, dict) else None
        if isinstance(resolution, dict):
            snapshot["resolution_ok"] = resolution.get("ok") is True
        current = body.get("current")
        if isinstance(current, dict):
            snapshot["current_task"] = _as_str(current.get("task"))
            snapshot["current_label"] = _as_str(current.get("label")) or snapshot["current_task"]
            nodes = current.get("nodes")
            snapshot["current_nodes"] = [str(n) for n in nodes] if isinstance(nodes, list) else []
            snapshot["elapsed"] = _as_float(current.get("elapsed"), 0.0)
        last = body.get("last_result")
        snapshot["last_result"] = last if isinstance(last, dict) else None
        snapshot["task_count"] = _as_int(body.get("task_count"), 0)

        if snapshot["state"] == STATE_BUSY:
            state_label = prompts.t(
                "STATUS_BUSY",
                lang=self._lang,
                task=snapshot["current_label"] or snapshot["current_task"],
            )
        elif snapshot["state"] == STATE_READY:
            state_label = prompts.t("STATUS_READY", lang=self._lang)
        elif snapshot["state"] == STATE_IDLE:
            state_label = prompts.t("STATUS_PREPARING", lang=self._lang)
        else:
            state_label = snapshot["state"]
        summary = prompts.t("STATUS_SUMMARY", lang=self._lang, state_label=state_label)
        if snapshot["elevated"] is False:
            summary += prompts.t("STATUS_NOT_ADMIN_SUFFIX", lang=self._lang)
        snapshot["summary"] = summary
        return snapshot

    # ==================================================================
    # The task slot
    # ==================================================================

    def _get_lock(self) -> asyncio.Lock:
        # Created lazily so the service can be constructed outside a running
        # loop (unit tests, config validation) without binding to a dead one.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def try_claim(self, task_name: str, label: str, timeout_s: float) -> Optional[ActiveTask]:
        """Atomically take the task slot, or ``None`` when it is taken.

        Check-and-claim under one lock so two concurrent ``maaend_run`` calls
        cannot both see an empty slot. The panel refuses concurrent tasks
        anyway (409/400), but a local refusal would leave our watch loop
        pointing at somebody else's task.
        """
        async with self._get_lock():
            if self._active is not None:
                return None
            now = time.monotonic()
            active = ActiveTask(
                task_name=task_name,
                label=label or task_name,
                claimed_at=now,
                timeout_s=timeout_s,
                # Seeded with the claim time rather than 0.0 so the throttle
                # applies from the first poll: a task that just started does
                # not need a progress line before it has done anything.
                last_progress_cue=now,
            )
            self._active = active
            return active

    async def _release(self, active: ActiveTask) -> None:
        async with self._get_lock():
            if self._active is active:
                self._active = None
            active.finished = True

    def has_active_task(self) -> bool:
        return self._active is not None

    def current_label(self) -> str:
        return self._active.label if self._active else ""

    # ==================================================================
    # Dispatch
    # ==================================================================

    def _resolve_timeout_s(self, requested_minutes: Any) -> Optional[float]:
        """Turn the model's ``timeout_minutes`` into the panel's ``timeout``.

        ``None`` means "send nothing", which is how the panel is asked to use
        its own default. ``0`` or a missing value means the same thing here —
        the panel treats a falsy ``timeout`` as 300 s, so passing 0 through
        would silently be five minutes rather than "no limit".
        """
        if requested_minutes is None:
            minutes = self._default_timeout_minutes
        else:
            minutes = _as_int(requested_minutes, -1)
            if minutes < 0:
                return None
            if minutes == 0:
                return None
            minutes = min(minutes, self._max_timeout_minutes)
        if minutes <= 0:
            return None
        return float(minutes) * 60.0

    # Public alias: the facade must not have to reach past the service's
    # surface to ask the same question.
    resolve_timeout_s = _resolve_timeout_s

    async def prepare(self) -> Dict[str, Any]:
        """Initialise the panel's runtime (Resource/Controller/Agents)."""
        client = await self._ensure_client()
        token = await self._read_token()
        if not token:
            raise MaaendAuthError("no token file")
        body = await client.prepare(token)
        self._panel_instance = token
        return body

    def failure_message(self, exc: BaseException) -> Tuple[str, str]:
        """``(something the character can say, machine code)`` for any failure.

        A panel failure already has a mapped line; anything else is a bug on
        this side, which the model should hear about only as "something went
        wrong" — the traceback belongs in the log, not the transcript.
        """
        if isinstance(exc, MaaendError):
            envelope = self.error_envelope(exc)
            return envelope["output"]["error"], envelope.get("error", exc.code)
        self._log_warning("unexpected failure: {}: {}", type(exc).__name__, exc)
        return prompts.t("ERR_GENERIC", lang=self._lang), "INTERNAL"

    async def dispatch(self, active: ActiveTask) -> Optional[Dict[str, Any]]:
        """Send a claimed task. ``None`` means dispatched; a dict is an error envelope.

        The preconditions are checked here, against the panel's own status,
        rather than deciphered from a 400 body afterwards. That matters for
        more than tidiness: the panel's error strings are operator prose
        ("游戏窗口 960x720（比例 1.3333）不是 16:9，go-service 会强制停止任务。
        请把游戏分辨率调成 16:9 后重试。") and tool results are written into the
        conversation transcript forever, so the plugin composes its own line
        from the *data* (``resolution.w``, ``resolution.h``) and never forwards
        theirs.
        """
        try:
            token = await self._read_token()
            if not token:
                await self._release(active)
                self._note_error("NO_TOKEN")
                return self._envelope("ERR_NO_TOKEN", "NO_TOKEN")
            client = await self._ensure_client()
            status = await client.status(token)
            self._panel_instance = token
        except MaaendError as exc:
            await self._release(active)
            return self.error_envelope(exc)

        busy = await self._blocking_condition(status, client, token)
        if busy is not None:
            await self._release(active)
            return busy

        warnings: List[str] = []
        if status.get("focused") is False:
            # Focus is recoverable: the panel re-grabs it right before
            # post_task and rechecks every 5 s while running. Worth saying,
            # not worth refusing over.
            warnings.append(prompts.t("WARN_NOT_FOCUSED", lang=self._lang))

        # ``/api/run`` initialises the runtime itself, but its own init is
        # inside the request; doing it here first keeps the failure attributable
        # ("initialisation failed") instead of indistinguishable from a
        # rejected dispatch.
        if not status.get("ready"):
            try:
                status = await client.prepare(token)
            except MaaendError as exc:
                await self._release(active)
                if isinstance(exc, MaaendRejected):
                    return self._envelope("ERR_PREPARE", "PREPARE_FAILED")
                return self.error_envelope(exc)
            busy = await self._blocking_condition(status, client, token)
            if busy is not None:
                await self._release(active)
                return busy

        try:
            body = await client.run(
                token,
                active.task_name,
                task_timeout_s=active.timeout_s or None,
            )
        except MaaendError as exc:
            await self._release(active)
            if isinstance(exc, MaaendRejected):
                # A 400 this early is the panel contradicting the status we
                # just read (a task started from the browser panel in between,
                # or a resolution change). Retry the classification once.
                busy = await self._blocking_condition(
                    self._last_status, client, token, refetch=True
                )
                if busy is not None:
                    return busy
            return self.error_envelope(exc)

        current = body.get("current")
        if isinstance(current, dict):
            started = _as_float(current.get("started"), 0.0)
            if started > 0:
                active.started = started
            label = _as_str(current.get("label")).strip()
            if label:
                active.label = label
        if active.started <= 0:
            # The panel always reports ``current`` on a 202; without its
            # timestamp the watch loop cannot attribute later snapshots, so
            # fall back to our own clock and let the first poll re-anchor.
            active.started = time.time()

        self._log_info("task {} dispatched", active.task_name)
        self._spawn_watch(active)
        return warnings and {"warnings": warnings} or None

    async def _blocking_condition(
        self,
        status: Any,
        client: MaaendClient,
        token: str,
        *,
        refetch: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Return an error envelope when the panel cannot take a task now.

        ``None`` means clear. Only two conditions block a dispatch, and both
        are read from data (``state``, ``resolution``) rather than from prose.
        """
        if refetch:
            try:
                status = await client.status(token)
                self._panel_instance = token
            except MaaendError:
                status = None
        if not isinstance(status, dict):
            return None

        # Not elevated means Seize input never reaches the game — the panel
        # still reports the run as attempted, so the failure would surface
        # only as "the task succeeded and nothing happened". Refusing here is
        # the only place that can be said plainly.
        if status.get("elevated") is False:
            return self._envelope("ERR_NOT_ADMIN", "NOT_ADMIN")

        if _as_str(status.get("state")) == STATE_BUSY:
            current = status.get("current")
            label = ""
            if isinstance(current, dict):
                label = _as_str(current.get("label")) or _as_str(current.get("task"))
            return self._envelope(
                "ERR_BUSY", "BUSY", current=label or prompts.t("LABEL_IDLE_TASK", lang=self._lang)
            )

        resolution = status.get("resolution")
        if isinstance(resolution, dict) and resolution.get("ok") is False:
            return self._envelope(
                "ERR_RESOLUTION",
                "RESOLUTION",
                w=_as_int(resolution.get("w"), 0),
                h=_as_int(resolution.get("h"), 0),
                ratio=resolution.get("ratio"),
            )
        return None

    # ==================================================================
    # The watch loop
    # ==================================================================

    def _wait_budget(self, active: ActiveTask) -> float:
        """How long to keep watching before giving up on this task.

        Always wider than the panel's own deadline, so that when the panel
        stops a task at its timeout we still see the panel report it — the
        alternative is our watchdog firing first and calling a normal timeout
        "unknown".
        """
        if active.timeout_s > 0:
            return max(self._task_wait_timeout, active.timeout_s + WAIT_BUDGET_GRACE_S)
        return self._task_wait_timeout

    def _spawn_watch(self, active: ActiveTask) -> None:
        task = asyncio.create_task(self._watch(active), name="maaend_bridge.watch")
        self._watch_task = task

        def _done(finished: asyncio.Task) -> None:
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                self._log_warning("watch loop error: {}: {}", type(exc).__name__, exc)

        task.add_done_callback(_done)

    async def _watch(self, active: ActiveTask) -> None:
        """Poll until the task ends, narrating progress as it goes.

        Every exit path releases the slot exactly once and (except for a
        deliberate stop) says something. Silence after a task was handed over
        is the one outcome the character must never produce: the model told
        the user a task started, and nothing later contradicts or confirms it.
        """
        failures = 0
        budget = self._wait_budget(active)
        try:
            while not active.finished:
                await asyncio.sleep(self._poll_interval)
                if active.finished:
                    return
                if time.monotonic() - active.claimed_at > budget:
                    await self._release(active)
                    self._log_info("task {} exceeded the wait budget", active.task_name)
                    self._push_cue(
                        "CUE_TIMEOUT",
                        ai_behavior="respond",
                        priority=PRIORITY_ALERT,
                        coalesce_key=f"maaend_timeout:{active.task_name}",
                        label=active.label,
                    )
                    return

                token = await self._read_token()
                if not token:
                    failures += 1
                    if failures >= LOST_AFTER_FAILURES:
                        await self._lose(active, "token file gone")
                        return
                    continue
                if self._panel_instance and token != self._panel_instance:
                    # The panel restarted. The process that was running this
                    # task no longer exists, so the outcome is unknown — not
                    # a failure, and above all not something to re-run: the
                    # work may have already completed in the game.
                    await self._lose(active, "panel instance changed")
                    return

                try:
                    client = await self._ensure_client()
                    status = await client.status(token)
                except MaaendError as exc:
                    failures += 1
                    self._log_warning(
                        "status poll failed ({}/{}): {}",
                        failures, LOST_AFTER_FAILURES, exc.code,
                    )
                    if failures >= LOST_AFTER_FAILURES:
                        await self._lose(active, exc.code)
                        return
                    continue
                failures = 0

                state = _as_str(status.get("state"))
                current = status.get("current")
                if state == STATE_IDLE:
                    # A live panel is never idle while running a task; a fresh
                    # process is idle and has no memory of it.
                    await self._lose(active, "panel reported idle")
                    return
                if state != STATE_BUSY:
                    await self._finish(active, status)
                    return

                self._narrate(active, current)

            # ``active.finished`` was set by a deliberate stop; the outcome
            # is suppressed on purpose (see stop_current).
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._log_warning("watch loop crashed: {}: {}", type(exc).__name__, exc)
            await self._release(active)

    def _narrate(self, active: ActiveTask, current: Any) -> None:
        """Turn newly-seen pipeline nodes into a throttled progress cue."""
        if not isinstance(current, dict):
            return
        nodes = current.get("nodes")
        if not isinstance(nodes, list):
            return
        total = len(nodes)
        if total <= active.nodes_seen:
            return
        fresh = [str(n) for n in nodes[active.nodes_seen:]]
        active.nodes_seen = total
        active.pending_nodes.extend(fresh)
        if len(active.pending_nodes) > PROGRESS_NODES_BUFFER:
            del active.pending_nodes[:-PROGRESS_NODES_BUFFER]

        now = time.monotonic()
        if now - active.last_progress_cue < self._progress_min_interval:
            return
        if not active.pending_nodes:
            return
        batch = active.pending_nodes[-PROGRESS_NODES_PER_CUE:]
        active.pending_nodes = []
        active.last_progress_cue = now
        self._push_cue(
            "CUE_PROGRESS",
            ai_behavior="read",
            priority=PRIORITY_PROGRESS,
            coalesce_key=f"maaend_progress:{active.task_name}",
            internal_gag=prompts.gag(self._lang),
            label=active.label,
            nodes=", ".join(batch),
            elapsed=prompts.format_elapsed(active.elapsed_s(current), lang=self._lang),
        )

    async def _finish(self, active: ActiveTask, status: Dict[str, Any]) -> None:
        """Classify the terminal status and narrate it."""
        was_stopped = active.stop_requested
        await self._release(active)
        if was_stopped:
            # Stopping is something the model asked for and already knows
            # about. Cue'ing it invites a re-dispatch of abandoned work, and
            # the panel's ``succeeded``/``failed`` flags are meaningless for a
            # run that was cut short on purpose.
            self._log_info("task {} stopped on request; outcome not narrated", active.task_name)
            return

        current = status.get("current")
        last = status.get("last_result")
        elapsed = active.elapsed_s(current if isinstance(current, dict) else None)
        elapsed_text = prompts.format_elapsed(elapsed, lang=self._lang)

        # ``last_result.task`` is the panel's own record of which task ran. If
        # it names something else, this completion is not ours and silence is
        # the honest answer; announcing another task's outcome as ours would
        # be worse than saying nothing.
        if isinstance(last, dict):
            recorded = _as_str(last.get("task")).strip()
            if recorded and recorded != active.task_name:
                self._log_warning(
                    "terminal status names task {} but we dispatched {}",
                    recorded, active.task_name,
                )
                return

        if isinstance(last, dict) and last.get("succeeded") is True:
            key, priority = "CUE_DONE_OK", PRIORITY_COMPLETION
        elif isinstance(last, dict) and last.get("failed") is True:
            key, priority = "CUE_DONE_FAILED", PRIORITY_ALERT
        else:
            # Neither flag: the run stopped early — the panel's own timeout,
            # or the window went away. Not a success, and not reported as a
            # failure either, because the panel did not call it one.
            key, priority = "CUE_DONE_INCOMPLETE", PRIORITY_ALERT

        self._log_info("task {} finished: {}", active.task_name, key)
        self._push_cue(
            key,
            ai_behavior="respond",
            priority=priority,
            coalesce_key=f"maaend_done:{active.task_name}",
            label=active.label,
            elapsed=elapsed_text,
        )

    async def _lose(self, active: ActiveTask, reason: str) -> None:
        """Contact lost mid-task: the outcome is unknown, permanently."""
        was_stopped = active.stop_requested
        await self._release(active)
        self._log_warning("lost task {} ({})", active.task_name, reason)
        if was_stopped:
            return
        self._push_cue(
            "CUE_LOST",
            ai_behavior="respond",
            priority=PRIORITY_ALERT,
            coalesce_key=f"maaend_lost:{active.task_name}",
            label=active.label,
        )

    # ==================================================================
    # Stop
    # ==================================================================

    async def stop_current(self) -> Dict[str, Any]:
        """Ask the panel to stop the running task.

        The slot is released by the watch loop when it observes the panel
        leave ``busy``, not here — the panel keeps the task "running" until
        its own ``post_stop().wait()`` returns. The ``stop_requested`` flag is
        what makes that completion silent.
        """
        active = self._active
        try:
            client = await self._ensure_client()
            token = await self._read_token()
            if not token:
                return self._envelope("ERR_NO_TOKEN", "NO_TOKEN")
            body = await client.stop(token)
        except MaaendError as exc:
            return self.error_envelope(exc)

        if body.get("ok") is not True:
            # The panel answers 200 with ``ok: false`` when nothing was
            # running. Only an explicit true counts as a stop.
            if active is None:
                return self._envelope("ERR_STOP_NOTHING", "NOTHING_TO_STOP")
            return self._envelope("ACK_STOP_FAILED", "STOP_NOT_ACKNOWLEDGED")

        if active is not None:
            active.stop_requested = True
            label = active.label
        else:
            label = prompts.t("LABEL_IDLE_TASK", lang=self._lang)
        self._log_info("stop acknowledged")
        return self._envelope("ACK_STOPPED", "STOPPED", as_error=False, label=label)

    # ==================================================================
    # Model-facing error/success envelopes
    # ==================================================================

    def error_envelope(
        self, exc: MaaendError, *, query: str = ""
    ) -> Dict[str, Any]:
        """Translate a panel failure into a model-facing envelope.

        The exception's own text never appears: it is operator prose that can
        echo the request, and a tool result is written into the conversation
        history permanently. ``detail`` is logged instead.
        """
        if isinstance(exc, (MaaendUnreachable, MaaendTimeout)):
            key = "ERR_UNREACHABLE"
        elif isinstance(exc, MaaendAuthError):
            key = "ERR_AUTH"
        elif isinstance(exc, MaaendForbidden):
            key = "ERR_FORBIDDEN"
        elif isinstance(exc, MaaendNotReady):
            key = "ERR_NOT_READY"
        elif isinstance(exc, (MaaendRejected, MaaendProtocolError)):
            key = "ERR_GENERIC"
        else:
            key = "ERR_GENERIC"
        if exc.detail:
            self._log_warning("panel error {}: {}", exc.code, exc.detail)
        envelope = self._envelope(key, exc.code)
        if query:
            envelope["output"]["query"] = query[:200]
        return envelope

    def _envelope(
        self, key: str, code: str, *, as_error: bool = True, **fmt: Any
    ) -> Dict[str, Any]:
        try:
            text = prompts.t(key, lang=self._lang, **fmt)
        except Exception as exc:
            self._log_warning("prompt {} could not be formatted: {}", key, type(exc).__name__)
            text = code
        if not as_error:
            # A success carries no machine code: ``error`` on a non-error
            # envelope reads as a contradiction to anyone scanning the result.
            return {"output": {"message": text}, "is_error": False}
        return {"output": {"error": text}, "is_error": True, "error": code}

    def _note_error(self, code: str) -> None:
        self._last_error = code

    # ==================================================================
    # Cue emission
    # ==================================================================

    def _push_cue(
        self,
        key: str,
        *,
        ai_behavior: str,
        priority: int,
        coalesce_key: str,
        visibility: Optional[List[str]] = None,
        internal_gag: str = "",
        **fmt: Any,
    ) -> None:
        """Format and push one cue. Never raises into the caller.

        ``visibility=[]`` with ``ai_behavior="respond"`` is the standard shape
        here: the user does not see the cue text, but the dialog model absorbs
        it and voices the outcome in its own words on the turn the cue opens.
        That is the whole product — the character narrating her executor —
        rather than a system notification.

        Only the text reaches the model. There is no side channel for a task
        id or a state code, so anything the model must act on, or must be
        careful about, has to be written into the body.
        """
        if not key:
            return
        if internal_gag:
            fmt.setdefault("internal_gag", internal_gag)
        try:
            text = prompts.t(key, lang=self._lang, **fmt)
        except Exception as exc:
            self._log_warning("cue {} could not be formatted: {}", key, type(exc).__name__)
            return
        if not text or self._push_message is None:
            return
        try:
            self._push_message(
                source="maaend_bridge",
                visibility=list(visibility or []),
                ai_behavior=ai_behavior,
                parts=[{"type": "text", "text": text}],
                priority=priority,
                coalesce_key=coalesce_key,
            )
        except Exception as exc:
            self._log_warning("cue {} push failed: {}", key, type(exc).__name__)
            return
        self._log_info("cue {} pushed ({}, prio={}, {} chars)", key, ai_behavior, priority, len(text))

    # ==================================================================
    # Logging — metadata only. Task labels are resource strings, not user
    # text, but nothing conversational goes through the logger regardless.
    # ==================================================================

    def _log_info(self, msg: str, *args: Any) -> None:
        self._emit("info", msg, *args)

    def _log_warning(self, msg: str, *args: Any) -> None:
        self._emit("warning", msg, *args)

    def _emit(self, level: str, msg: str, *args: Any) -> None:
        if self._logger is None:
            return
        try:
            getattr(self._logger, level)("[MaaEnd] " + msg, *args)
        except Exception:
            pass


__all__ = [
    "MaaendService",
    "ActiveTask",
    "DEFAULT_BASE_URL",
    "default_panel_dir",
    "normalise_match_text",
    "STATE_BUSY",
    "STATE_IDLE",
    "STATE_READY",
]
