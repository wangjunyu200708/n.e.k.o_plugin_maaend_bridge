"""The task slot, the watch loop, and what reaches the conversation.

These are the behaviours the plugin exists for, and the ones whose failure
modes are invisible from the outside: a missing cue reads as "she forgot",
not as "a branch was skipped". So the assertions are on the *cue keys the
service emitted*, recorded by a fake logger/push sink, rather than only on
return values.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fakes import (
    FakePanelClient,
    busy_status,
    ready_status,
    terminal_status,
    write_token,
)
from maaend_bridge_plugin import prompts
from maaend_bridge_plugin.client import (
    MaaendAuthError,
    MaaendRejected,
    MaaendUnreachable,
)
from maaend_bridge_plugin.service import MaaendService, normalise_match_text

TASKS = [
    {"name": "DailyRewards", "label": "🎁每日奖励", "group": ["daily"]},
    {"name": "CreditShoppingN2", "label": "🛍️信用点购物", "group": ["other_menu"]},
    {"name": "DailyTask", "label": "📋每日任务", "group": ["daily"]},
]


class Cues:
    """Collects ``push_message`` calls so a test can assert on the narration."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.items.append(kwargs)

    def keys(self) -> List[str]:
        return [item.get("coalesce_key", "") for item in self.items]

    @property
    def count(self) -> int:
        return len(self.items)

    def only(self) -> Dict[str, Any]:
        assert self.count == 1, f"expected exactly one cue, got {self.count}: {self.items}"
        return self.items[0]

    def text(self, index: int = 0) -> str:
        return self.items[index]["parts"][0]["text"]


class Logs:
    """Records log calls without formatting them.

    The host logger takes ``{}`` braces; a plugin handed a stdlib ``%`` logger
    must also work. The service passes ``(msg, *args)`` straight through, so
    the fake stores them raw and renders on demand with a best-effort format —
    formatting eagerly would make this fake test the logging style rather than
    the service.
    """

    def __init__(self) -> None:
        self.lines: List[tuple] = []

    def info(self, msg: str, *args: Any) -> None:
        self.lines.append(("info", msg, args))

    def warning(self, msg: str, *args: Any) -> None:
        self.lines.append(("warning", msg, args))

    def rendered(self) -> str:
        out = []
        for level, msg, args in self.lines:
            text = str(msg)
            if args:
                try:
                    text = text.format(*args)
                except Exception:
                    try:
                        text = text % args
                    except Exception:
                        text = f"{text} {args!r}"
            out.append(f"{level}: {text}")
        return "\n".join(out)


def make_service(tmp_path: Path, *, token: str = "tok-1", **cfg: Any) -> tuple:
    token_file = tmp_path / "panel_token.txt"
    write_token(token_file, token)
    cues = Cues()
    logs = Logs()
    service = MaaendService(logger=logs, push_message_fn=cues)
    service.set_lang("en")
    service.configure(
        {
            "token_file": str(token_file),
            "poll_interval_seconds": 0.01,
            "progress_min_interval_seconds": 0.0,
            "task_wait_timeout_seconds": 5.0,
            **cfg,
        }
    )
    client = FakePanelClient()
    client.tasks_response = TASKS
    service._client = client
    return service, client, cues, logs


# ---------------------------------------------------------------------------
# Task-name resolution — the model speaks human, the panel wants a code.
# ---------------------------------------------------------------------------


def test_resolve_exact_code():
    assert MaaendService.resolve_task("CreditShoppingN2", TASKS) == ("CreditShoppingN2", [])


def test_resolve_exact_code_is_case_insensitive():
    assert MaaendService.resolve_task("creditshoppingn2", TASKS)[0] == "CreditShoppingN2"


def test_resolve_label_ignores_emoji_and_spacing():
    assert MaaendService.resolve_task("信用点购物", TASKS)[0] == "CreditShoppingN2"


def test_resolve_label_inside_a_sentence():
    assert MaaendService.resolve_task("帮我跑一下每日奖励", TASKS)[0] == "DailyRewards"


def test_resolve_ambiguous_returns_every_candidate_and_no_choice():
    name, candidates = MaaendService.resolve_task("每日", TASKS)
    assert name == ""
    assert {item["name"] for item in candidates} == {"DailyRewards", "DailyTask"}


def test_resolve_unknown_returns_nothing():
    assert MaaendService.resolve_task("不存在的东西", TASKS) == ("", [])


def test_resolve_empty_query_returns_nothing():
    assert MaaendService.resolve_task("   ", TASKS) == ("", [])


def test_normalise_match_text_strips_punctuation_and_case():
    assert normalise_match_text("🛍️ Credit-Shopping_N2 ") == "creditshoppingn2"


# ---------------------------------------------------------------------------
# Token handling — the credential appears at runtime and rotates per restart.
# ---------------------------------------------------------------------------


def test_token_path_prefers_explicit_file(tmp_path: Path):
    service = MaaendService()
    service.configure({"token_file": str(tmp_path / "custom.txt")})
    assert service.resolve_token_path() == tmp_path / "custom.txt"


def test_token_path_falls_back_to_panel_dir(tmp_path: Path):
    service = MaaendService()
    service.configure({"panel_dir": str(tmp_path)})
    assert service.resolve_token_path() == tmp_path / "panel_token.txt"


def test_token_path_is_none_without_a_hint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MAAEND_PANEL_DIR", raising=False)
    monkeypatch.setattr(
        "maaend_bridge_plugin.service.default_panel_dir", lambda: ""
    )
    assert MaaendService().resolve_token_path() is None


async def test_read_token_returns_empty_when_the_file_is_absent(tmp_path: Path):
    service = MaaendService()
    service.configure({"token_file": str(tmp_path / "nope.txt")})
    assert await service._read_token() == ""


async def test_read_token_picks_up_a_rotation(tmp_path: Path):
    """The panel rewrites the token on every start; we must not cache it."""
    token_file = tmp_path / "panel_token.txt"
    service = MaaendService()
    service.configure({"token_file": str(token_file)})
    write_token(token_file, "first")
    assert await service._read_token() == "first"
    write_token(token_file, "second")
    assert await service._read_token() == "second"


# ---------------------------------------------------------------------------
# Configuration clamping
# ---------------------------------------------------------------------------


def test_configure_clamps_poll_interval_and_timeouts():
    service = MaaendService()
    service.configure(
        {
            "poll_interval_seconds": 0,
            "http_timeout_seconds": -5,
            "default_timeout_minutes": -3,
            "max_timeout_minutes": 0,
        }
    )
    assert service._poll_interval >= 0.5
    assert service._http_timeout >= 1.0
    assert service._default_timeout_minutes == 0
    assert service._max_timeout_minutes >= 1


def test_resolve_timeout_defaults_to_configured_minutes():
    service = MaaendService()
    service.configure({"default_timeout_minutes": 30})
    assert service.resolve_timeout_s(None) == 1800.0


def test_resolve_timeout_zero_sends_nothing():
    service = MaaendService()
    service.configure({"default_timeout_minutes": 30})
    assert service.resolve_timeout_s(0) is None


def test_resolve_timeout_is_capped():
    service = MaaendService()
    service.configure({"max_timeout_minutes": 10})
    assert service.resolve_timeout_s(999) == 600.0


def test_wait_budget_always_outlasts_the_panel_deadline(tmp_path: Path):
    """Otherwise our watchdog fires first and calls a normal timeout unknown."""
    service, _client, _cues, _logs = make_service(tmp_path, task_wait_timeout_seconds=60.0)
    from maaend_bridge_plugin.service import ActiveTask

    active = ActiveTask(task_name="X", label="X", claimed_at=0.0, timeout_s=1800.0)
    assert service._wait_budget(active) > 1800.0


# ---------------------------------------------------------------------------
# Dispatch preconditions — read from data, never from the panel's prose.
# ---------------------------------------------------------------------------


async def test_dispatch_refuses_without_elevation(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [ready_status(elevated=False)]
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    envelope = await service.dispatch(active)
    assert envelope and envelope["is_error"] is True
    assert envelope["error"] == "NOT_ADMIN"
    assert service.has_active_task() is False
    assert cues.count == 0


async def test_dispatch_refuses_a_non_16_9_resolution_and_reports_the_data(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [
        ready_status(resolution={"w": 960, "h": 720, "ratio": 1.3333, "ok": False, "message": "对端散文"})
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    envelope = await service.dispatch(active)
    assert envelope["error"] == "RESOLUTION"
    text = envelope["output"]["error"]
    assert "960" in text and "720" in text
    # The panel's own operator-facing sentence must not be forwarded.
    assert "对端散文" not in text
    assert service.has_active_task() is False


async def test_dispatch_refuses_when_a_task_is_already_running(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [busy_status("CreditShoppingN2", label="信用点购物")]
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    envelope = await service.dispatch(active)
    assert envelope["error"] == "BUSY"
    assert "信用点购物" in envelope["output"]["error"]
    assert service.has_active_task() is False


async def test_dispatch_prepares_an_uninitialised_panel(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [ready_status(state="idle", ready=False, resolution=None)]
    client.prepare_response = ready_status()
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    envelope = await service.dispatch(active)
    assert envelope is None or envelope.get("is_error") is not True
    assert any(call[0] == "prepare" for call in client.calls)
    assert any(call[0] == "run" for call in client.calls)


async def test_dispatch_reports_a_failed_prepare_as_such(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [ready_status(state="idle", ready=False, resolution=None)]
    client.prepare_response = MaaendRejected("boom")
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    envelope = await service.dispatch(active)
    assert envelope["error"] == "PREPARE_FAILED"
    assert service.has_active_task() is False


async def test_dispatch_never_leaks_panel_prose_on_a_rejection(tmp_path: Path):
    service, client, _cues, logs = make_service(tmp_path)
    client.status_queue = [ready_status()]
    client.run_response = MaaendRejected(
        "panel said no", detail="RuntimeError: 127.0.0.1:12702 /api/run exploded"
    )
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    envelope = await service.dispatch(active)
    assert envelope["is_error"] is True
    blob = str(envelope)
    assert "127.0.0.1" not in blob and "/api/run" not in blob
    # ...but the detail is still available where a maintainer looks.
    assert "exploded" in logs.rendered()


async def test_two_claims_cannot_both_win(tmp_path: Path):
    service, _client, _cues, _logs = make_service(tmp_path)
    first = await service.try_claim("A", "A", 60.0)
    second = await service.try_claim("B", "B", 60.0)
    assert first is not None and second is None


# ---------------------------------------------------------------------------
# The watch loop — the polarity of the whole plugin lives here.
# ---------------------------------------------------------------------------


async def _drain(service: MaaendService) -> None:
    task = service._watch_task
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


async def test_watch_narrates_success(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path)
    client.run_response = busy_status("DailyRewards", started=1000.0)
    # The first queued status is the one ``dispatch`` reads to check its
    # preconditions; everything after it is what the watch loop observes.
    client.status_queue = [
        ready_status(),
        busy_status("DailyRewards", started=1000.0, elapsed=20.0),
        terminal_status("DailyRewards", started=1000.0, succeeded=True, failed=False, elapsed=67.2),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    assert await service.dispatch(active) is None
    await _drain(service)
    assert cues.count == 1
    assert cues.keys() == ["maaend_done:DailyRewards"]
    assert cues.items[0]["ai_behavior"] == "respond"
    assert "1m07s" in cues.text()
    assert service.has_active_task() is False


async def test_watch_narrates_failure(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [
        ready_status(),
        busy_status("DailyRewards", started=1000.0),
        terminal_status("DailyRewards", started=1000.0, succeeded=False, failed=True),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    assert cues.count == 1
    assert "failed" in cues.text().lower()


async def test_watch_calls_a_stopped_early_run_incomplete_not_failed(tmp_path: Path):
    """Neither flag set — the panel did not call it a failure, so we must not."""
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [
        ready_status(),
        busy_status("DailyRewards", started=1000.0),
        terminal_status("DailyRewards", started=1000.0, succeeded=False, failed=False),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    assert cues.count == 1
    text = cues.text().lower()
    assert "stopped before finishing" in text
    assert "failed" not in text


async def test_watch_ignores_a_terminal_result_for_another_task(tmp_path: Path):
    """Announcing someone else's outcome as ours would be worse than silence."""
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [
        ready_status(),
        busy_status("DailyRewards", started=1000.0),
        terminal_status("CreditShoppingN2", started=1000.0, succeeded=True),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    assert cues.count == 0
    assert service.has_active_task() is False


async def test_watch_is_silent_after_a_requested_stop(tmp_path: Path):
    """A stop is something the model asked for; narrating it invites re-dispatch."""
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [ready_status()] + [
        busy_status("DailyRewards", started=1000.0) for _ in range(200)
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    reply = await service.stop_current()
    assert reply["is_error"] is False
    assert active.stop_requested is True
    # Only now let the panel report the run as terminal, so the stop flag is
    # guaranteed to be set before the outcome is classified.
    client.status_queue = [
        terminal_status("DailyRewards", started=1000.0, succeeded=False, failed=False)
    ]
    await _drain(service)
    assert cues.count == 0
    assert service.has_active_task() is False


async def test_watch_reports_unknown_when_the_panel_restarts(tmp_path: Path):
    """A token change is proof the process that owned the task is gone."""
    service, client, cues, logs = make_service(tmp_path)
    client.status_queue = [ready_status(), busy_status("DailyRewards", started=1000.0)]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    # The panel restarts: a fresh token file. The status below is never read,
    # because the identity check trips first.
    write_token(Path(service._token_file), "tok-2")
    await _drain(service)
    assert cues.count == 1
    assert cues.keys() == ["maaend_lost:DailyRewards"]
    assert "do not re-run" in cues.text().lower()
    assert service.has_active_task() is False
    assert "panel instance changed" in logs.rendered()


async def test_watch_reports_unknown_when_the_panel_goes_idle(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [ready_status(state="idle", ready=False, resolution=None)]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    # A live panel is never idle while running a task; a fresh process is.
    client.status_queue = [ready_status(state="idle", ready=False, resolution=None)]
    await _drain(service)
    assert cues.keys() == ["maaend_lost:DailyRewards"]


async def test_watch_tolerates_a_single_failed_poll(tmp_path: Path):
    service, client, cues, logs = make_service(tmp_path)
    client.status_queue = [
        ready_status(),
        MaaendUnreachable("hiccup"),
        busy_status("DailyRewards", started=1000.0),
        terminal_status("DailyRewards", started=1000.0, succeeded=True),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    assert cues.keys() == ["maaend_done:DailyRewards"]
    assert "status poll failed" in logs.rendered()


async def test_watch_gives_up_after_repeated_failures(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path)
    client.status_queue = [
        ready_status(),
        MaaendUnreachable("gone"),
        MaaendUnreachable("gone"),
        MaaendUnreachable("gone"),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    assert cues.keys() == ["maaend_lost:DailyRewards"]
    assert service.has_active_task() is False


async def test_watch_narrates_new_nodes_and_throttles(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path, progress_min_interval_seconds=0.0)
    client.status_queue = [
        ready_status(),
        busy_status("DailyRewards", started=1000.0, nodes=["A"], elapsed=2.0),
        busy_status("DailyRewards", started=1000.0, nodes=["A", "B", "C"], elapsed=4.0),
        terminal_status("DailyRewards", started=1000.0, succeeded=True, nodes=["A", "B", "C"]),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    progress = [item for item in cues.items if item["coalesce_key"].startswith("maaend_progress")]
    assert len(progress) == 2
    assert "A" in progress[0]["parts"][0]["text"]
    assert "B" in progress[1]["parts"][0]["text"]
    assert progress[0]["ai_behavior"] == "read"
    assert progress[0]["visibility"] == []
    # The completion still lands last and outranks the narration.
    assert cues.items[-1]["coalesce_key"] == "maaend_done:DailyRewards"
    assert cues.items[-1]["priority"] > progress[0]["priority"]


async def test_progress_is_held_back_while_the_throttle_is_closed(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path, progress_min_interval_seconds=3600.0)
    client.status_queue = [
        ready_status(),
        busy_status("DailyRewards", started=1000.0, nodes=["A"]),
        busy_status("DailyRewards", started=1000.0, nodes=["A", "B"]),
        terminal_status("DailyRewards", started=1000.0, succeeded=True, nodes=["A", "B"]),
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await _drain(service)
    assert [item["coalesce_key"] for item in cues.items] == ["maaend_done:DailyRewards"]


async def test_watch_releases_the_slot_on_its_own_budget(tmp_path: Path):
    service, client, cues, _logs = make_service(tmp_path, task_wait_timeout_seconds=0.05)
    client.status_queue = [ready_status()] + [
        busy_status("DailyRewards", started=1000.0) for _ in range(50)
    ]
    active = await service.try_claim("DailyRewards", "每日奖励", 0.0)
    await service.dispatch(active)
    await _drain(service)
    assert cues.keys() == ["maaend_timeout:DailyRewards"]
    assert service.has_active_task() is False


# ---------------------------------------------------------------------------
# Stop — only an explicit success value counts.
# ---------------------------------------------------------------------------


async def test_stop_reports_nothing_when_the_panel_says_nothing_ran(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.stop_response = {"ok": False, "message": "当前没有运行中的任务"}
    reply = await service.stop_current()
    assert reply["is_error"] is True
    assert reply["error"] == "NOTHING_TO_STOP"
    assert "当前没有运行中的任务" not in str(reply)


async def test_stop_is_not_satisfied_by_a_missing_ok_field(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.stop_response = {"message": "done"}
    reply = await service.stop_current()
    assert reply["is_error"] is True


async def test_stop_reports_unreachable_without_naming_the_host(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.stop_response = MaaendUnreachable("ConnectError: 127.0.0.1:12702")
    reply = await service.stop_current()
    assert reply["is_error"] is True
    assert "127.0.0.1" not in str(reply)
    assert "12702" not in str(reply)


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------


async def test_get_status_summarises_a_live_panel(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [busy_status("DailyRewards", label="每日奖励")]
    snapshot = await service.get_status()
    assert snapshot["connected"] is True
    assert snapshot["state"] == "busy"
    assert snapshot["current_label"] == "每日奖励"
    assert "DailyRewards" in snapshot["summary"] or "每日奖励" in snapshot["summary"]


async def test_get_status_reports_a_down_panel_without_raising(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [MaaendUnreachable("down")]
    client.ping_result = False
    snapshot = await service.get_status()
    assert snapshot["connected"] is False
    assert snapshot["state"] == "unknown"
    assert snapshot["summary"]


async def test_get_status_flags_a_missing_token(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    Path(service._token_file).unlink()
    client.ping_result = True
    snapshot = await service.get_status()
    assert snapshot["connected"] is True  # the panel is up...
    assert snapshot["token_ok"] is False  # ...but we cannot talk to it yet


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


async def test_catalogue_is_cached_until_the_ttl_expires(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    first = await service.catalogue()
    second = await service.catalogue()
    assert first == second == TASKS
    assert [call[0] for call in client.calls].count("tasks") == 1


async def test_catalogue_refetches_on_demand(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    await service.catalogue()
    await service.catalogue(force=True)
    assert [call[0] for call in client.calls].count("tasks") == 2


async def test_catalogue_raises_rather_than_reporting_an_empty_world(tmp_path: Path):
    """"No tasks" and "no such task" must never be the same answer."""
    service, client, _cues, _logs = make_service(tmp_path)
    client.tasks_response = MaaendAuthError("nope")
    with pytest.raises(MaaendAuthError):
        await service.catalogue()


async def test_a_protocol_error_maps_to_a_different_code_than_a_dead_panel(tmp_path: Path):
    """"Something else is on that port" is not "the panel is down"."""
    service, _client, _cues, _logs = make_service(tmp_path)
    other = service.error_envelope(MaaendAuthError("x"))
    dead = service.error_envelope(MaaendUnreachable("x"))
    assert other["error"] != dead["error"]
    assert other["output"]["error"] != dead["output"]["error"]


# ---------------------------------------------------------------------------
# Config reload must not disturb the panel a live task is talking to.
# ---------------------------------------------------------------------------


async def test_reconfigure_does_not_lose_an_active_task(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    active = await service.try_claim("DailyRewards", "每日奖励", 60.0)
    service.configure({"token_file": service._token_file, "poll_interval_seconds": 0.5})
    assert service._active is active


async def test_reconfigure_parks_the_old_client_for_closing(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    service.configure({"token_file": service._token_file, "base_url": "http://127.0.0.1:19999"})
    assert client in service._stale_clients
    assert service._client is None


async def test_close_cancels_the_watch_and_closes_everything(tmp_path: Path):
    service, client, _cues, _logs = make_service(tmp_path)
    client.status_queue = [busy_status("DailyRewards", started=1000.0) for _ in range(10)]
    active = await service.try_claim("DailyRewards", "每日奖励", 1800.0)
    await service.dispatch(active)
    await asyncio.sleep(0)
    await service.close()
    assert service._watch_task is None
    assert client.is_open is False


def test_elapsed_prefers_the_panels_own_figure():
    from maaend_bridge_plugin.service import ActiveTask

    active = ActiveTask(task_name="X", label="X", claimed_at=time.monotonic())
    assert active.elapsed_s({"elapsed": 12.5}) == 12.5
    assert active.elapsed_s(None) >= 0.0


def test_prompt_keys_used_by_the_service_exist():
    for key in (
        "ERR_UNREACHABLE",
        "ERR_AUTH",
        "ERR_FORBIDDEN",
        "ERR_NOT_READY",
        "ERR_GENERIC",
        "ERR_NOT_ADMIN",
        "ERR_RESOLUTION",
        "WARN_NOT_FOCUSED",
        "ERR_BUSY",
        "ERR_PREPARE",
        "ERR_STOP_NOTHING",
        "ACK_STOPPED",
        "ACK_STOP_FAILED",
        "CUE_DONE_OK",
        "CUE_DONE_FAILED",
        "CUE_DONE_INCOMPLETE",
        "CUE_PROGRESS",
        "CUE_LOST",
        "CUE_TIMEOUT",
        "STATUS_SUMMARY",
        "STATUS_BUSY",
        "STATUS_READY",
        "STATUS_PREPARING",
        "STATUS_NOT_CONNECTED",
        "LABEL_IDLE_TASK",
    ):
        assert key in prompts.PROMPTS
