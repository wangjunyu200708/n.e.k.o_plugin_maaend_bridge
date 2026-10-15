"""The two surfaces, and the boundary between them.

The split is structural, not stylistic: ``@plugin_entry`` never reaches
main_server's tool registry and the analyzer's view has every ``__llm_tool__``
entry stripped, so an ability placed on the wrong channel is not merely
inconvenient — it is invisible to the caller that needs it. These tests pin the
placement down, along with the tool schemas and the wording the model reads.
"""
from __future__ import annotations

import pytest
from fakes import FakePanelClient, ready_status, write_token
from maaend_bridge_plugin import prompts

# Handlers the dialog LLM reaches through its tool registry.
LLM_TOOLS = ("maaend_run", "maaend_stop")
# Handlers for the analyzer / plugin panel / CLI. ``maaend_start_task`` and
# ``maaend_stop_task`` are the same capabilities as the tools above, on the
# other audience's channel, because a hosted panel can only call an entry.
PLUGIN_ENTRIES = (
    "maaend_status",
    "maaend_list_tasks",
    "maaend_task_options",
    "maaend_start_task",
    "maaend_stop_task",
    "maaend_prepare",
    "maaend_reload_config",
)


def test_the_plugin_class_is_exposed(plugin_module):
    assert hasattr(plugin_module, "MaaendBridgePlugin")


def test_the_class_is_a_neko_plugin_base(plugin_module):
    from plugin.sdk.plugin import NekoPluginBase

    assert issubclass(plugin_module.MaaendBridgePlugin, NekoPluginBase)


@pytest.mark.parametrize("name", LLM_TOOLS + PLUGIN_ENTRIES)
def test_every_surface_handler_exists(plugin_module, name):
    assert callable(getattr(plugin_module.MaaendBridgePlugin, name, None)), f"{name} is missing"


def test_the_ui_context_provider_exists(plugin_module):
    assert callable(getattr(plugin_module.MaaendBridgePlugin, "maaend_ui_context", None))


# ---------------------------------------------------------------------------
# What the *real* SDK registered.
#
# These run only where the host SDK is importable (the source tree, or the
# release check); under the standalone stub they are skipped, because the stub
# decorators are identity functions and have no metadata to inspect.
#
# They are worth having anyway: a ``@lifecycle`` id outside the legal set is
# **silently never called** — no error, no log line — so a typo there produces
# a plugin that looks fine and never initialises.
# ---------------------------------------------------------------------------


def _real_sdk() -> bool:
    from plugin.sdk.plugin import NekoPluginBase

    return hasattr(NekoPluginBase, "collect_entries")


needs_real_sdk = pytest.mark.skipif(
    not _real_sdk(), reason="host SDK unavailable (standalone stub in use)"
)

LEGAL_LIFECYCLE_IDS = {"startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"}


def _metadata(cls):
    from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR
    from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR, UI_CONTEXT_META_ATTR
    from plugin.sdk.shared.constants import EVENT_META_ATTR

    found = {"tools": [], "entries": [], "actions": [], "contexts": [], "lifecycles": []}
    for member in vars(cls).values():
        if not callable(member):
            continue
        tool = getattr(member, LLM_TOOL_META_ATTR, None)
        if tool is not None:
            found["tools"].append(tool)
        event = getattr(member, EVENT_META_ATTR, None)
        if event is not None:
            entry_id = event.get("id") if isinstance(event, dict) else getattr(event, "id", None)
            kind = event.get("kind") if isinstance(event, dict) else getattr(event, "kind", None)
            if kind == "lifecycle" or str(entry_id) in LEGAL_LIFECYCLE_IDS:
                found["lifecycles"].append(str(entry_id))
            else:
                found["entries"].append(str(entry_id))
        action = getattr(member, UI_ACTION_META_ATTR, None)
        if action is not None:
            found["actions"].append(action)
        context = getattr(member, UI_CONTEXT_META_ATTR, None)
        if context is not None:
            found["contexts"].append(context)
    return found


@needs_real_sdk
def test_the_real_sdk_receives_both_llm_tools(plugin_module):
    tools = {tool.name: tool for tool in _metadata(plugin_module.MaaendBridgePlugin)["tools"]}
    assert set(tools) == set(LLM_TOOLS)
    assert set(tools["maaend_run"].parameters["properties"]) == {"task", "timeout_minutes"}
    # The wrapper ceiling must sit above anything the handler can take.
    assert tools["maaend_run"].timeout_seconds >= 300.0


@needs_real_sdk
def test_the_real_sdk_receives_every_plugin_entry(plugin_module):
    found = _metadata(plugin_module.MaaendBridgePlugin)
    assert set(found["entries"]) == set(PLUGIN_ENTRIES)


@needs_real_sdk
def test_every_lifecycle_id_is_one_the_host_actually_dispatches(plugin_module):
    found = _metadata(plugin_module.MaaendBridgePlugin)["lifecycles"]
    assert found, "no lifecycle hook registered at all"
    assert set(found) <= LEGAL_LIFECYCLE_IDS, f"illegal ids (never called): {found}"


@needs_real_sdk
def test_the_panel_surface_id_has_a_matching_context_provider(plugin_module):
    """``plugin.toml``'s ``context`` and the ``@ui.context`` id must agree."""
    import tomllib
    from pathlib import Path

    root = Path(plugin_module.__file__).resolve().parent
    manifest = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
    declared = {
        surface["context"]
        for surface in manifest["plugin"]["ui"]["guide"]
        if "context" in surface
    }
    provided = {
        context.get("id") if isinstance(context, dict) else getattr(context, "id", None)
        for context in _metadata(plugin_module.MaaendBridgePlugin)["contexts"]
    }
    assert declared <= provided, f"plugin.toml declares {declared}, provider offers {provided}"


@needs_real_sdk
def test_every_panel_button_maps_to_a_registered_action(plugin_module):
    found = _metadata(plugin_module.MaaendBridgePlugin)
    action_ids = {
        action.get("id") if isinstance(action, dict) else getattr(action, "id", None)
        for action in found["actions"]
    }
    # Every action must also be reachable as a plugin entry, or the hosted
    # panel's api.call would fail with "entry not found".
    assert action_ids <= set(found["entries"]), f"actions without an entry: {action_ids - set(found['entries'])}"


# ---------------------------------------------------------------------------
# Tool schemas — the contract the model is handed.
# ---------------------------------------------------------------------------


def test_run_tool_schema_names_both_parameters(plugin_module):
    schema = plugin_module.MAAEND_RUN_SCHEMA
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"task", "timeout_minutes"}
    # ``required`` is empty on purpose: calling with no ``task`` is how the
    # model asks to see what is available, which is the search-first workflow.
    assert schema.get("required", []) == []
    assert schema["properties"]["timeout_minutes"]["type"] == "integer"


def test_stop_tool_takes_no_arguments(plugin_module):
    assert plugin_module.MAAEND_STOP_SCHEMA["properties"] == {}


def test_run_description_states_that_it_is_fire_and_forget(plugin_module):
    text = plugin_module.MAAEND_RUN_DESCRIPTION.lower()
    assert "fire-and-forget" in text
    assert "never state, guess or promise a result" in text
    # The single-task constraint has to be stated, or the model retries into
    # a guaranteed "already running" answer.
    assert "one task at a time" in text


def test_run_description_forbids_inventing_a_task_name(plugin_module):
    assert "do not invent a task name" in plugin_module.MAAEND_RUN_DESCRIPTION.lower()


def test_stop_description_says_the_outcome_is_not_narrated(plugin_module):
    text = plugin_module.MAAEND_STOP_DESCRIPTION.lower()
    assert "not announced as an outcome" in text


# ---------------------------------------------------------------------------
# The facade's shared dispatch path, driven end to end through the entry.
# ---------------------------------------------------------------------------


class _Cues:
    def __init__(self) -> None:
        self.items = []

    def __call__(self, **kwargs):
        self.items.append(kwargs)


def make_plugin(tmp_path, *, token: str = "tok-1"):
    from maaend_bridge_plugin import MaaendBridgePlugin

    plugin = MaaendBridgePlugin(None)
    plugin._lang = "en"
    token_file = tmp_path / "panel_token.txt"
    write_token(token_file, token)
    plugin._service.set_lang("en")
    plugin._service.configure(
        {
            "token_file": str(token_file),
            "poll_interval_seconds": 0.01,
            "progress_min_interval_seconds": 0.0,
        }
    )
    client = FakePanelClient()
    client.tasks_response = [
        {"name": "DailyRewards", "label": "🎁每日奖励"},
        {"name": "DailyTask", "label": "📋每日任务"},
        {"name": "CreditShoppingN2", "label": "🛍️信用点购物"},
    ]
    plugin._service._client = client
    cues = _Cues()
    plugin._service._push_message = cues
    return plugin, client, cues


async def test_dispatch_resolves_a_human_name_to_the_task_code(tmp_path):
    plugin, client, _cues = make_plugin(tmp_path)
    result = await plugin._resolve_and_dispatch("帮我跑每日奖励", None)
    assert result["ok"] is True
    assert result["data"]["task"] == "DailyRewards"
    assert ("run", "tok-1", "DailyRewards", {"task_timeout_s": 1800.0}) in client.calls
    assert "每日奖励" in result["text"]


async def test_dispatch_without_a_name_returns_the_catalogue(tmp_path):
    plugin, _client, _cues = make_plugin(tmp_path)
    result = await plugin._resolve_and_dispatch("", None)
    assert result["ok"] is False
    assert result["code"] == "CATALOGUE"
    assert "DailyRewards" in result["text"]
    assert "CreditShoppingN2" in result["text"]


async def test_dispatch_reports_an_ambiguous_name_with_both_options(tmp_path):
    plugin, client, _cues = make_plugin(tmp_path)
    result = await plugin._resolve_and_dispatch("每日", None)
    assert result["ok"] is False
    assert result["code"] == "ERR_AMBIGUOUS"
    assert "每日奖励" in result["text"] and "每日任务" in result["text"]
    # Ambiguity must never be resolved by picking a favourite.
    assert not any(call[0] == "run" for call in client.calls)


async def test_dispatch_of_an_unknown_name_does_not_reach_the_panel(tmp_path):
    plugin, client, _cues = make_plugin(tmp_path)
    result = await plugin._resolve_and_dispatch("打螺丝", None)
    assert result["ok"] is False
    assert not any(call[0] == "run" for call in client.calls)


async def test_dispatch_reports_unreachability_in_its_own_words(tmp_path):
    from maaend_bridge_plugin.client import MaaendUnreachable

    plugin, client, _cues = make_plugin(tmp_path)
    client.tasks_response = MaaendUnreachable("ConnectError: 127.0.0.1:12702")
    result = await plugin._resolve_and_dispatch("每日奖励", None)
    assert result["ok"] is False
    assert "127.0.0.1" not in result["text"]
    assert "12702" not in result["text"]


async def test_a_second_dispatch_is_refused_while_one_is_active(tmp_path):
    plugin, client, _cues = make_plugin(tmp_path)
    client.status_queue = [ready_status()]
    first = await plugin._resolve_and_dispatch("每日奖励", None)
    assert first["ok"] is True
    second = await plugin._resolve_and_dispatch("信用点购物", None)
    assert second["ok"] is False
    assert second["code"] == "BUSY"
    await plugin._service.close()


async def test_run_tool_returns_prose_on_success_and_an_envelope_on_failure(tmp_path):
    plugin, _client, _cues = make_plugin(tmp_path)
    ok = await plugin.maaend_run(task="每日奖励")
    assert isinstance(ok, str)
    assert "每日奖励" in ok
    bad = await plugin.maaend_run(task="打螺丝")
    assert isinstance(bad, dict)
    assert bad["is_error"] is True
    assert "error" in bad["output"]
    await plugin._service.close()


async def test_stop_tool_reports_nothing_to_stop_as_an_error(tmp_path):
    plugin, client, _cues = make_plugin(tmp_path)
    client.stop_response = {"ok": False, "message": "当前没有运行中的任务"}
    result = await plugin.maaend_stop()
    assert isinstance(result, dict)
    assert result["is_error"] is True
    assert "当前没有运行中的任务" not in str(result)
    await plugin._service.close()


async def test_status_entry_answers_while_the_panel_is_down(tmp_path):
    from maaend_bridge_plugin.client import MaaendUnreachable

    plugin, client, _cues = make_plugin(tmp_path)
    client.status_queue = [MaaendUnreachable("down")]
    client.ping_result = False
    ok = await plugin.maaend_status()
    assert ok.is_ok()
    assert ok.value["connected"] is False
    assert ok.value["summary"]


async def test_list_tasks_entry_reports_a_failure_rather_than_an_empty_list(tmp_path):
    from maaend_bridge_plugin.client import MaaendUnreachable

    plugin, client, _cues = make_plugin(tmp_path)
    client.tasks_response = MaaendUnreachable("down")
    result = await plugin.maaend_list_tasks()
    assert result.is_ok() is False
    assert "127.0.0.1" not in str(result.error)


async def test_ui_context_carries_the_catalogue_for_the_panel(tmp_path):
    plugin, _client, _cues = make_plugin(tmp_path)
    context = await plugin.maaend_ui_context()
    assert {item["name"] for item in context["tasks"]} == {
        "DailyRewards",
        "DailyTask",
        "CreditShoppingN2",
    }
    assert "status" in context
    await plugin._service.close()


async def test_every_prompt_key_the_facade_uses_is_present():
    for key in (
        "CATALOGUE",
        "CATALOGUE_EMPTY",
        "ERR_UNKNOWN_TASK",
        "ERR_AMBIGUOUS",
        "ERR_BUSY",
        "ACK_DISPATCHED",
        "ACK_DISPATCHED_NO_TIMEOUT",
        "ERR_TASK_SCHEMA",
        "LABEL_IDLE_TASK",
    ):
        assert key in prompts.PROMPTS
