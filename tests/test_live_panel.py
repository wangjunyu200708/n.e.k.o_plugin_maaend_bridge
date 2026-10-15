"""Live contract test against a running MaaEnd panel backend.

Skipped unless ``MAAEND_LIVE=1``. Everything else in this suite runs against
fakes, which is right for CI but blind to the one failure that matters most in
a connector: **the panel's real field names**. A contract test that talks to
the actual server is the only thing that catches "the endpoint moved" or "the
status field is called something else".

Read-only on purpose. It never calls ``POST /api/run`` or ``POST /api/prepare``
— both initialise MaaFramework (3-5 s, two agent processes) and the run path
additionally seizes keyboard/mouse input and grabs the game window's focus.
A test has no business doing that to somebody's machine or their game.

Usage::

    # in the panel repository
    python panel_server.py --port 12702 --no-elevate-check
    # then, with the panel reachable and idle:
    MAAEND_LIVE=1 uv run --project <N.E.K.O> pytest tests/test_live_panel.py -q

The panel must be started normally for anything task-related; this file only
proves the read surface.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from maaend_bridge_plugin.client import MaaendAuthError, MaaendClient, MaaendRejected
from maaend_bridge_plugin.service import MaaendService, default_panel_dir

pytestmark = pytest.mark.skipif(
    os.environ.get("MAAEND_LIVE") != "1",
    reason="set MAAEND_LIVE=1 with a panel backend running on 127.0.0.1:12702",
)

BASE_URL = os.environ.get("MAAEND_LIVE_URL", "http://127.0.0.1:12702")


def _token() -> str:
    override = os.environ.get("MAAEND_LIVE_TOKEN_FILE")
    if override:
        return Path(override).read_text(encoding="utf-8").strip()
    panel_dir = os.environ.get("MAAEND_PANEL_DIR") or default_panel_dir()
    assert panel_dir, "panel_dir unresolved — set MAAEND_PANEL_DIR"
    return (Path(panel_dir) / "panel_token.txt").read_text(encoding="utf-8").strip()


async def test_ping_identifies_the_panel():
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        assert await client.ping() is True
    finally:
        await client.close()


async def test_a_token_from_disk_opens_the_status_endpoint():
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        status = await client.status(_token())
    finally:
        await client.close()
    # Every field the service reads, asserted against the live document.
    for key in ("state", "ready", "elevated", "focused", "minimized", "current", "last_result", "task_count"):
        assert key in status, f"the panel stopped reporting {key!r}"
    assert status["state"] in ("idle", "ready", "busy")


async def test_a_wrong_token_is_rejected_as_an_auth_error():
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        with pytest.raises(MaaendAuthError):
            await client.status("definitely-not-the-token")
    finally:
        await client.close()


async def test_the_task_catalogue_carries_the_fields_the_matcher_uses():
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        tasks = await client.tasks(_token())
    finally:
        await client.close()
    assert len(tasks) > 10, "the resource layer should ship dozens of tasks"
    for task in tasks:
        assert isinstance(task.get("name"), str) and task["name"]
        assert isinstance(task.get("label"), str) and task["label"]


def test_a_real_label_resolves_to_a_real_code():
    """The matcher is fed the same labels the executor ships."""
    import asyncio

    async def fetch():
        client = MaaendClient(BASE_URL)
        await client.open()
        try:
            return await client.tasks(_token())
        finally:
            await client.close()

    tasks = asyncio.new_event_loop().run_until_complete(fetch())
    resolved = []
    for task in tasks:
        name, candidates = MaaendService.resolve_task(task["label"], tasks)
        assert name == task["name"], (
            f"label {task['label']!r} resolved to {name!r} (candidates "
            f"{[c.get('name') for c in candidates]}) instead of {task['name']!r}"
        )
        resolved.append(name)
    assert len(set(resolved)) == len(resolved), "two tasks resolved to the same code"


async def test_option_tree_for_a_real_task_has_the_documented_shape():
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        tasks = await client.tasks(_token())
        targets = [t for t in tasks if t.get("options")]
        if not targets:
            pytest.skip("no task on this resource layer declares options")
        payload = await client.options(_token(), targets[0]["name"])
    finally:
        await client.close()
    assert payload["task"] == targets[0]["name"]
    assert isinstance(payload["tree"], list)
    for node in payload["tree"]:
        assert "name" in node and "type" in node


async def test_an_unknown_task_is_a_rejection_not_a_crash():
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        with pytest.raises(MaaendRejected) as info:
            await client.options(_token(), "NoSuchTask__bridge_probe")
    finally:
        await client.close()
    # The panel's prose is kept for logs; nothing else about it is used.
    assert info.value.detail


async def test_stop_with_nothing_running_is_not_acknowledged():
    """The panel answers 200 either way; only an explicit ``ok: true`` counts."""
    client = MaaendClient(BASE_URL)
    await client.open()
    try:
        status = await client.status(_token())
        if status.get("state") == "busy":
            pytest.skip("a task is running — not touching it")
        body = await client.stop(_token())
    finally:
        await client.close()
    assert body.get("ok") is not True


async def test_service_reports_the_live_panel_without_raising():
    service = MaaendService()
    service.set_lang("zh")
    service.configure({"base_url": BASE_URL, "panel_dir": os.environ.get("MAAEND_PANEL_DIR", "")})
    try:
        snapshot = await service.get_status()
        assert snapshot["connected"] is True
        assert snapshot["token_ok"] is True
        assert snapshot["task_count"] > 10
        assert snapshot["summary"]
    finally:
        await service.close()
