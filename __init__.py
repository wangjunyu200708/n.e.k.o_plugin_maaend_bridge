"""maaend_bridge — drive the local MaaEnd executor from the conversation.

The executor is a separate Python process that plays *Arknights: Endfield*
through Seize, with its own loopback HTTP API (see :mod:`.client`). This
plugin is the thin N.E.K.O. side of that bridge: the character can start a
task, stop it, and narrate what the executor is doing.

Where the capabilities live, and why:

* :func:`maaend_run` and :func:`maaend_stop` are ``@llm_tool`` — the *dialog*
  LLM calls them directly. "帮我把今天的日常跑了" has to be decided by
  whichever model is actually mid-conversation, and dispatch needs the
  model's own typed arguments with a synchronous answer.
* :func:`maaend_status`, :func:`maaend_list_tasks`, :func:`maaend_task_options`
  and :func:`maaend_reload_config` are ``@plugin_entry`` — facts and settings,
  which the analyzer asks for and the plugin panel shows. Dispatching a status
  query as if it were an action is the canonical way to make an agent loop
  against itself, and a fact query does not deserve a slot in the dialog
  model's tool list.
* :func:`maaend_start_task` exists because the hosted panel's Run button
  cannot reach an ``@llm_tool``: a surface can only ``props.api.call`` an
  entry. It is the same dispatch, on the other audience's channel — and
  because ``@plugin_entry`` never reaches main_server's tool registry, it
  costs the dialog model nothing.

The two audiences are mutually exclusive by construction: ``@plugin_entry``
never reaches main_server's tool registry (only the ``LLM_TOOL_REGISTER``
notification, sent for ``@llm_tool``, does), and the analyzer's plugin view has
every ``__llm_tool__``-prefixed entry stripped out
(``app/agent_server/api_runtime.py``). The sibling ``mcbot_bridge`` and
``game_agent_minecraft`` plugins use the same split.

The third channel is the reverse one: the executor's progress is translated
into ``push_message`` cues, so the character reacts on her own without anyone
polling for news.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import prompts
from .service import MaaendService

# ---------------------------------------------------------------------------
# JSON Schemas are module-level constants rather than inline literals so the
# plugin's own introspection (unit tests, the tool contract test) can reference
# the exact contract the model sees.
# ---------------------------------------------------------------------------

MAAEND_RUN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "Which task to run. The executor's own task name or label, or "
                "the user's own wording for it — matching is done for you "
                "against the executor's catalogue, so '信用点购物' and "
                "'CreditShoppingN2' both work. Leave it empty only when you "
                "genuinely do not know what is available: the result will then "
                "list every task, and you should pick one and call again "
                "rather than guessing."
            ),
        },
        "timeout_minutes": {
            "type": "integer",
            "description": (
                "Optional. How long the executor may spend on this task, in "
                "minutes. Set it only when the user asked for a specific "
                "length; omit it otherwise and the configured default is "
                "used. If the task is still running at the limit the executor "
                "stops it, so a small number here means a half-finished run."
            ),
        },
    },
    "required": [],
}

MAAEND_RUN_DESCRIPTION = (
    "Start one task on the local Arknights: Endfield executor and return as "
    "soon as it is accepted. Fire-and-forget: the outcome arrives later on "
    "its own as a cue, so never state, guess or promise a result from this "
    "call alone, and never call it again just because a progress cue showed "
    "up.\n\n"
    "This tool dispatches work; it is not an awareness or status source. "
    "What the executor is doing, and whether the last run succeeded, come "
    "back as cues — react to those, do not poll for them.\n\n"
    "Use it when the user asks for a task that is not already running. The "
    "executor runs one task at a time: if the result says one is already "
    "running, that is not a transient error — either stop the current one "
    "first or tell the user what is in the way. Do NOT use it for questions "
    "about state or progress, and do not invent a task name — if the task is "
    "not found the result lists what the executor can do.\n\n"
    "Parameters:\n"
    "  task (string): the task to run, in the user's own wording or the "
    "executor's own name for it.\n"
    "  timeout_minutes (integer, optional): only when the user named a "
    "length; the executor stops the task at that limit."
)

MAAEND_STOP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {},
}

MAAEND_STOP_DESCRIPTION = (
    "Stop whatever the local Endfield executor is running right now, and "
    "return as soon as the executor acknowledges it. Use it when the user "
    "says to stop, cancel or abort the run. The stopped run is deliberately "
    "not announced as an outcome — you asked for it, so nothing else is "
    "reported about it. Do not use it when nothing is running; the result "
    "will say so."
)


@neko_plugin
class MaaendBridgePlugin(NekoPluginBase):
    """Plugin facade — lifecycle wiring and the two channel surfaces.

    All executor-facing behaviour lives in :class:`MaaendService`; this class
    holds only SDK integration, so the service stays testable without the
    plugin SDK.
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        # Follow the global NEKO_LOG_LEVEL so DEBUG actually shows up when
        # someone is diagnosing a bridge that will not connect.
        _log_level = (os.environ.get("NEKO_LOG_LEVEL") or "INFO").strip().upper()
        try:
            self.file_logger = self.enable_file_logging(log_level=_log_level)
        except ValueError:
            self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._cfg: Dict[str, Any] = {}
        self._lang: str = prompts.DEFAULT_LANG
        self._service = MaaendService(
            logger=self.logger,
            push_message_fn=self.push_message,
        )
        # The service locates the bundled ``executor/`` from this. Without it
        # ``executor_dir()`` is ``None`` and the "launch executor" button has
        # nothing to launch.
        plugin_dir = getattr(self, "plugin_dir", None)
        if plugin_dir:
            self._service.set_plugin_dir(plugin_dir)
        else:  # pragma: no cover - depends on the host's SDK surface
            self.logger.warning(
                "[init] plugin_dir unavailable — the executor launch button will not work"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        # Configuration only. The executor makes no connection of its own, so
        # there is nothing to start here and no long-lived task to seed — the
        # watch loop is spawned per dispatch, on the host's command loop.
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("maaend")
        self._cfg = section if isinstance(section, dict) else {}
        self._lang = prompts.user_lang()
        self._service.set_lang(self._lang)
        self._service.configure(self._cfg)
        path = self._service.resolve_token_path()
        self.logger.info(
            "[startup] base_url={} token_file={}",
            self._cfg.get("base_url", ""),
            str(path) if path else "<unresolved: set panel_dir>",
        )
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        try:
            await self._service.close()
        except Exception as exc:
            self.logger.warning("[shutdown] close raised — {}: {}", type(exc).__name__, exc)
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        # The framework passes neither the old nor the new values; re-dump.
        # Nothing is connected between calls, so a reconfigured client is
        # simply rebuilt on the next one.
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("maaend")
        self._cfg = section if isinstance(section, dict) else {}
        self._service.configure(self._cfg)
        return Ok({"status": "config_updated"})

    # ------------------------------------------------------------------
    # Shared dispatch path — the two surfaces differ only in how they
    # report, so the decision logic lives here exactly once.
    # ------------------------------------------------------------------

    async def _resolve_and_dispatch(
        self, task: Any, timeout_minutes: Any
    ) -> Dict[str, Any]:
        """Resolve a task name and hand it to the executor.

        Returns ``{"ok": bool, "text": str, "code": str, "data": dict}``.
        ``text`` is always something the character can say; ``code`` is the
        machine-readable category for the error path.
        """
        if not isinstance(task, str):
            task = "" if task is None else str(task)
        query = task.strip()

        try:
            tasks = await self._service.catalogue()
        except Exception as exc:
            text, code = self._service.failure_message(exc)
            return {"ok": False, "text": text, "code": code, "data": {}}

        if not query:
            catalogue = prompts.format_catalogue(tasks)
            if not catalogue:
                return {
                    "ok": False,
                    "text": prompts.t("CATALOGUE_EMPTY", lang=self._lang),
                    "code": "CATALOGUE_EMPTY",
                    "data": {},
                }
            return {
                "ok": False,
                "text": prompts.t("CATALOGUE", lang=self._lang, catalogue=catalogue),
                "code": "CATALOGUE",
                "data": {"tasks": tasks},
            }

        name, candidates = MaaendService.resolve_task(query, tasks)
        if not name:
            key = "ERR_AMBIGUOUS" if candidates else "ERR_UNKNOWN_TASK"
            return {
                "ok": False,
                "text": prompts.t(
                    key,
                    lang=self._lang,
                    query=query[:80],
                    candidates=prompts.format_candidates(candidates) or "",
                ),
                "code": key,
                "data": {"candidates": candidates[:8]},
            }

        label = name
        for item in tasks:
            if isinstance(item, dict) and item.get("name") == name:
                label = str(item.get("label") or name)
                break

        timeout_s = self._service.resolve_timeout_s(timeout_minutes)
        active = await self._service.try_claim(name, label, timeout_s or 0.0)
        if active is None:
            current = self._service.current_label() or prompts.t(
                "LABEL_IDLE_TASK", lang=self._lang
            )
            return {
                "ok": False,
                "text": prompts.t("ERR_BUSY", lang=self._lang, current=current[:80]),
                "code": "BUSY",
                "data": {},
            }

        outcome = await self._service.dispatch(active)
        if outcome is not None and outcome.get("is_error"):
            return {
                "ok": False,
                "text": outcome["output"]["error"],
                "code": outcome.get("error", "DISPATCH_FAILED"),
                "data": {},
            }

        minutes = int((timeout_s or 0) // 60)
        if minutes > 0:
            text = prompts.t("ACK_DISPATCHED", lang=self._lang, label=label, minutes=minutes)
        else:
            text = prompts.t("ACK_DISPATCHED_NO_TIMEOUT", lang=self._lang, label=label)
        warnings = []
        if isinstance(outcome, dict):
            warnings = [w for w in outcome.get("warnings", []) if isinstance(w, str)]
        if warnings:
            text = text + " " + " ".join(warnings)
        return {"ok": True, "text": text, "code": "", "data": {"task": name, "label": label}}

    # ------------------------------------------------------------------
    # @llm_tool — the dialog LLM's direct surface
    # ------------------------------------------------------------------

    @llm_tool(
        name="maaend_run",
        description=MAAEND_RUN_DESCRIPTION,
        parameters=MAAEND_RUN_SCHEMA,
        # The handler returns in milliseconds (dispatch is fire-and-forget);
        # the ceiling is held high so a cold ``/api/prepare`` inside the
        # dispatch path can never trip a wrapper timeout instead of
        # surfacing as a real message.
        timeout=300.0,
    )
    async def maaend_run(self, *, task: Any = None, timeout_minutes: Any = None, **_):
        result = await self._resolve_and_dispatch(task, timeout_minutes)
        if result["ok"]:
            return result["text"]
        return {
            "output": {"error": result["text"], "code": result["code"]},
            "is_error": True,
            "error": result["code"],
        }

    @llm_tool(
        name="maaend_stop",
        description=MAAEND_STOP_DESCRIPTION,
        parameters=MAAEND_STOP_SCHEMA,
        timeout=60.0,
    )
    async def maaend_stop(self, **_):
        envelope = await self._service.stop_current()
        if envelope.get("is_error"):
            return {
                "output": {"error": envelope["output"]["error"]},
                "is_error": True,
                "error": envelope.get("error", "STOP_FAILED"),
            }
        return envelope["output"]["message"]

    # ------------------------------------------------------------------
    # @plugin_entry — analyzer / panel / CLI surface
    # ------------------------------------------------------------------

    @ui.action(id="maaend_status", label="刷新状态")
    @plugin_entry(
        id="maaend_status",
        name="查询终末地执行器状态",
        description=(
            "查询本地 MaaEnd 执行器的状态：面板是否在运行、执行器是否已就绪、"
            "当前在跑哪个任务、跑到哪一步、有没有管理员权限、分辨率是否满足 16:9。"
            "面向插件面板与分析器；对话模型不通过它派活。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def maaend_status(self, **_):
        try:
            # include_executor: this entry is the one the panel reads when it
            # is *not* connected, which is exactly when the interpreter
            # diagnostics are worth their subprocess. The 5-second status
            # refresh in maaend_ui_context deliberately leaves it out.
            snapshot = await self._service.get_status(include_executor=True)
            return Ok(snapshot)
        except Exception as exc:
            return Err(f"{type(exc).__name__}: {exc}")

    @ui.action(id="maaend_list_tasks", label="刷新任务列表")
    @plugin_entry(
        id="maaend_list_tasks",
        name="列出终末地可执行任务",
        description=(
            "列出执行器当前能跑的自动化任务（名称、显示名、说明、分组），"
            "供面板或分析器选择。任务列表只在资源层更新时变化。"
        ),
        llm_result_fields=["count"],
        input_schema={
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "为真时绕过缓存，重新向执行器拉取。",
                },
            },
        },
    )
    async def maaend_list_tasks(self, refresh: Any = False, **_):
        try:
            tasks = await self._service.catalogue(force=refresh is True)
            return Ok({"count": len(tasks), "tasks": tasks})
        except Exception as exc:
            return Err(self._service.failure_message(exc)[0])

    @ui.action(id="maaend_task_options", label="加载选项")
    @plugin_entry(
        id="maaend_task_options",
        name="查询终末地任务的可选项",
        description=(
            "取某个任务的选项树（开关 / 下拉 / 多选 / 输入框 / 快捷键，含嵌套子项），"
            "面板据此渲染选项编辑器。"
        ),
        llm_result_fields=["task"],
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "任务代号（/api/tasks 里的 name 字段）。",
                },
            },
            "required": ["task"],
        },
    )
    async def maaend_task_options(self, task: Any = "", **_):
        name = str(task or "").strip()
        if not name:
            return Err("task 不能为空")
        try:
            return Ok(await self._service.task_options(name))
        except Exception as exc:
            return Err(self._service.failure_message(exc)[0])

    @ui.action(id="maaend_start_task", label="开始任务")
    @plugin_entry(
        id="maaend_start_task",
        name="让终末地执行器开始一个任务",
        description=(
            "按任务名（或名字的一部分）让执行器开始一个自动化任务。"
            "面向插件面板与分析器；对话模型用的是 maaend_run 工具。"
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "任务名或它的一部分。"},
                "timeout_minutes": {
                    "type": "integer",
                    "description": "可选，任务时长上限（分钟）。",
                },
            },
            "required": ["task"],
        },
    )
    async def maaend_start_task(self, task: Any = "", timeout_minutes: Any = None, **_):
        result = await self._resolve_and_dispatch(task, timeout_minutes)
        if result["ok"]:
            return Ok({"summary": result["text"], "result": await self._service.get_status()})
        return Err(result["text"])

    @ui.action(id="maaend_stop_task", label="停止任务")
    @plugin_entry(
        id="maaend_stop_task",
        name="停止终末地执行器的当前任务",
        description="让执行器停下当前正在跑的任务。没有任务在跑时会如实说明。",
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def maaend_stop_task(self, **_):
        envelope = await self._service.stop_current()
        if envelope.get("is_error"):
            return Err(envelope["output"]["error"])
        return Ok({"summary": envelope["output"]["message"], "result": await self._service.get_status()})

    @ui.action(id="maaend_prepare", label="初始化执行环境")
    @plugin_entry(
        id="maaend_prepare",
        name="初始化终末地执行器环境",
        description=(
            "让面板后端加载资源、拉起控制器与 agent（约 3~5 秒）。"
            "执行器通常自己会在第一次跑任务时初始化，这里只是给面板一个手动入口。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def maaend_prepare(self, **_):
        try:
            await self._service.prepare()
            snapshot = await self._service.get_status()
            return Ok({"summary": snapshot.get("summary", ""), "result": snapshot})
        except Exception as exc:
            return Err(self._service.failure_message(exc)[0])

    @ui.action(id="maaend_reload_config", label="重载配置")
    @plugin_entry(
        id="maaend_reload_config",
        name="重载终末地执行器配置",
        description=(
            "重新读取 plugin.toml [maaend] 配置。base_url 改动会重建 HTTP 客户端，"
            "token 地址、轮询间隔、超时等即时生效。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def maaend_reload_config(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
            section = cfg.get("maaend")
            self._cfg = section if isinstance(section, dict) else {}
            self._service.set_lang(prompts.user_lang())
            self._service.configure(self._cfg)
            path = self._service.resolve_token_path()
            return Ok({
                "summary": "config reloaded",
                "token_file": str(path) if path else "",
                "result": await self._service.get_status(),
            })
        except Exception as exc:
            return Err(f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # @ui.action — the hosted panel's three live surfaces
    #
    # None of these is a dialog-model tool. The panel shows all three
    # continuously (a launch button, a 2.5 s frame loop, a 2 s log poll), and
    # a log poll or a 30 KB base64 frame has no business in a tool schema.
    # ------------------------------------------------------------------

    @ui.action(id="maaend_launch_panel", label="启动执行器")
    @plugin_entry(
        id="maaend_launch_panel",
        name="启动终末地执行器",
        description=(
            "让插件请求 Windows 以管理员权限启动随附的执行器，并等它接上。"
            "执行器必须提权才能把按键喂给游戏；提权确认框由系统弹出。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def maaend_launch_panel(self, **_):
        try:
            result = await self._service.launch_panel()
            return Ok(result)
        except Exception as exc:
            text, _code = self._service.failure_message(exc)
            return Err(text)

    @ui.action(id="maaend_screenshot", label="刷新画面")
    @plugin_entry(
        id="maaend_screenshot",
        name="取一张终末地游戏画面",
        description=(
            "抓取游戏窗口当前画面并缩成小图返回，供面板右侧显示。"
            "执行器未初始化时如实报错。"
        ),
        llm_result_fields=[],
        input_schema={
            "type": "object",
            "properties": {
                "max_width": {
                    "type": "integer",
                    "description": "可选，缩图后的最大宽度（像素）。",
                },
            },
        },
    )
    async def maaend_screenshot(self, max_width: Any = None, **_):
        try:
            return Ok(await self._service.screenshot(max_width))
        except Exception as exc:
            text, _code = self._service.failure_message(exc)
            return Err(text)

    @ui.action(id="maaend_logs", label="拉取日志")
    @plugin_entry(
        id="maaend_logs",
        name="拉取终末地执行器的运行日志",
        description=(
            "取执行器自上次游标之后的新日志行。游标由插件自己发号，"
            "面板只需把返回的 next 存下来下次传回。"
        ),
        llm_result_fields=[],
        input_schema={
            "type": "object",
            "properties": {
                "since": {
                    "type": "integer",
                    "description": "上次拿到的 next；首次传 0。",
                },
                "limit": {
                    "type": "integer",
                    "description": "可选，最多返回多少行。",
                },
            },
        },
    )
    async def maaend_logs(self, since: Any = 0, limit: Any = None, **_):
        try:
            return Ok(self._service.log_lines(since, limit))
        except Exception as exc:
            text, _code = self._service.failure_message(exc)
            return Err(text)

    # ------------------------------------------------------------------
    # @ui.context — the hosted panel's data source
    # ------------------------------------------------------------------

    @ui.context(id="maaend")
    async def maaend_ui_context(self, **_):
        # Required by the hosted panel's props.api.call: without a provider
        # for this context id the host refuses every action call from the
        # surface. Never raises — the panel has to be able to show "not
        # connected" as a state rather than an error.
        #
        # **This runs under the host's watchdog** (`plugin/core/host.py`,
        # `_run_with_watchdog`, ~5 s). So it is kept to a single bounded network
        # attempt and reads everything else from memory: the catalogue comes
        # from the cache, not a fetch (the panel calls `maaend_list_tasks`
        # itself), and `get_status` is called without `include_executor` because
        # that one spawns a subprocess. Blowing the watchdog does not surface
        # here — it surfaces as the host reporting "Failed to query plugin UI
        # action context", which says nothing about the panel being down.
        status = await self._service.get_status()
        tasks = self._service.cached_catalogue()
        return {
            "status": status,
            "tasks": [
                {
                    "name": t.get("name", ""),
                    "label": t.get("label", "") or t.get("name", ""),
                    "description": t.get("description", ""),
                    "group": t.get("group", []),
                }
                for t in tasks
                if isinstance(t, dict)
            ],
            "active_task": self._service._active.task_name if self._service._active else "",
        }


__all__ = ["MaaendBridgePlugin", "MAAEND_RUN_SCHEMA", "MAAEND_STOP_SCHEMA"]
