"""The three live panel surfaces: launch, screenshot, log mirror.

What is worth pinning down here is the *policy*, not the plumbing — the
plumbing is already covered by ``test_service.py``. Three decisions in
particular are easy to regress silently:

* **A running panel is never launched twice.** The user may simply be looking
  at a stale status; spawning a second elevated panel would race it for the
  port and for the game window's focus.
* **"Elevation accepted" is not "panel up".** ``ShellExecuteW`` returns as
  soon as Windows accepts the request, which can be seconds before the user
  clicks *yes*. A timeout therefore has to read as advice, not as failure.
* **The log cursor is ours, not the panel's.** The panel restarts its ids at
  1; handing its cursor back to it would silently produce an empty set. So
  the plugin issues its own ``seq``, and a cursor from a previous plugin
  process is reported as ``reset``.
"""
from __future__ import annotations

import asyncio
import base64
import io
import time
from pathlib import Path

import pytest
from fakes import FakePanelClient, ready_status, write_token
from maaend_bridge_plugin.client import MaaendAuthError, MaaendRejected
from maaend_bridge_plugin.service import MaaendService


def _service(tmp_path: Path, *, panel_up: bool = True) -> tuple[MaaendService, FakePanelClient]:
    service = MaaendService()
    service.set_plugin_dir(tmp_path)
    # ``panel_dir`` must point at tmp: the default is the real executor
    # directory, whose token file is a *live* panel's, and a test must never
    # read — or worse, be influenced by — a real one.
    service.configure({"base_url": "http://127.0.0.1:12702", "panel_dir": str(tmp_path)})
    write_token(tmp_path / "panel_token.txt", "tok-1")
    client = FakePanelClient()
    client.ping_result = panel_up
    service._client = client
    # Every launch path reports diagnostics, and the real ``describe`` spawns
    # ``import maa`` per candidate interpreter — right for the panel, ruinous
    # for a test run. Stubbed for every test built on this helper.
    _no_probe(service)
    return service, client


async def _fake_describe() -> dict:
    return {
        "executor_dir": "",
        "executor_present": True,
        "override_present": True,
        "node": "",
        "python": "python.exe",
        "python_ok": True,
        "python_reason": "",
        "python_hint": "",
        "plugin_elevated": False,
        "manual_launcher": "",
    }


def _no_probe(service: MaaendService) -> None:
    service.describe_executor = _fake_describe  # type: ignore[method-assign]


def _png(width: int = 1280, height: int = 720) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


def test_launch_does_not_start_a_second_panel(tmp_path):
    service, _ = _service(tmp_path, panel_up=True)

    result = asyncio.run(service.launch_panel())

    assert result["launched"] is False
    assert result["summary"]


def test_launch_without_an_executor_directory_is_refused(tmp_path):
    # Built through the shared helper so the liveness probe is faked: a real
    # panel listening on 127.0.0.1:12702 would answer it and the refusal this
    # test is about would never be reached.
    service, _ = _service(tmp_path, panel_up=False)
    service._plugin_dir = None  # what the facade looks like before wiring

    with pytest.raises(MaaendRejected):
        asyncio.run(service.launch_panel())


def test_launch_refuses_when_no_interpreter_can_import_maa(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, panel_up=False)
    (tmp_path / "executor").mkdir()
    (tmp_path / "executor" / "panel_server.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.resolve_interpreter",
        lambda **_kwargs: ("", "none of these can import maa"),
    )

    with pytest.raises(MaaendRejected) as excinfo:
        asyncio.run(service.launch_panel())

    # The reason travels to the user; it is not a path or an exception name.
    assert "none of these" in str(excinfo.value)


def test_launch_reports_a_declined_uac_prompt_in_plain_words(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, panel_up=False)
    (tmp_path / "executor").mkdir()
    (tmp_path / "executor" / "panel_server.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.resolve_interpreter",
        lambda **_kwargs: ("python.exe", ""),
    )
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.launch_elevated",
        lambda *_a, **_k: (False, "the elevation prompt was declined"),
    )

    with pytest.raises(MaaendRejected) as excinfo:
        asyncio.run(service.launch_panel())

    assert "declined" in str(excinfo.value)


def test_launch_times_out_into_advice_not_an_error(tmp_path, monkeypatch):
    """UAC accepted but the user has not clicked yet: say so, do not fail."""
    service, client = _service(tmp_path, panel_up=False)
    (tmp_path / "executor").mkdir()
    (tmp_path / "executor" / "panel_server.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.resolve_interpreter",
        lambda **_kwargs: ("python.exe", ""),
    )
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.launch_elevated",
        lambda *_a, **_k: (True, ""),
    )
    service.configure({"panel_launch_timeout_seconds": 2.0})
    # ``configure`` clamps this to a 10 s floor, which is right for the panel
    # and far too slow for a test; set it directly.
    service._launch_timeout = 1.0
    client.ping_result = False

    result = asyncio.run(service.launch_panel())

    assert result["launched"] is True
    assert result["diagnostics"]["executor_present"] is True


def test_launch_reports_success_once_the_panel_answers(tmp_path, monkeypatch):
    service, client = _service(tmp_path, panel_up=False)
    (tmp_path / "executor").mkdir()
    (tmp_path / "executor" / "panel_server.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.resolve_interpreter",
        lambda **_kwargs: ("python.exe", ""),
    )
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.launch_elevated",
        lambda *_a, **_k: (True, ""),
    )

    seen = {"n": 0}

    async def ping_later():
        # False twice (the probe before the launch, then the first poll), then
        # up — so the wait loop, not the initial probe, is what ends it.
        seen["n"] += 1
        if seen["n"] < 3:
            return False
        return True

    client.ping = ping_later  # type: ignore[method-assign]
    service._launch_timeout = 10.0

    result = asyncio.run(service.launch_panel())

    assert result["launched"] is True
    assert seen["n"] >= 3


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def test_screenshot_without_a_token_is_an_auth_error(tmp_path):
    service, _ = _service(tmp_path)
    (tmp_path / "panel_token.txt").unlink()

    with pytest.raises(MaaendAuthError):
        asyncio.run(service.screenshot())


def test_screenshot_is_downscaled_and_inlined_as_jpeg(tmp_path):
    service, client = _service(tmp_path)
    client.screenshot_response = _png(1280, 720)

    frame = asyncio.run(service.screenshot(max_width=560))

    assert frame["width"] == 560
    assert frame["height"] == 315
    assert frame["image"].startswith("data:image/jpeg;base64,")
    assert time.time() - frame["ts"] < 30
    # The point of downscaling: the payload must be a fraction of the frame.
    raw = base64.b64decode(frame["image"].split(",", 1)[1])
    assert len(raw) < len(client.screenshot_response)


def test_screenshot_keeps_small_frames_as_they_are(tmp_path):
    service, client = _service(tmp_path)
    client.screenshot_response = _png(320, 180)

    frame = asyncio.run(service.screenshot(max_width=560))

    assert (frame["width"], frame["height"]) == (320, 180)


def test_screenshot_propagates_a_panel_failure(tmp_path):
    service, client = _service(tmp_path)
    client.screenshot_response = RuntimeError("panel is not ready")

    with pytest.raises(RuntimeError):
        asyncio.run(service.screenshot())


# ---------------------------------------------------------------------------
# Log mirror
# ---------------------------------------------------------------------------


def test_log_lines_are_empty_before_anything_is_mirrored(tmp_path):
    service, _ = _service(tmp_path)

    result = service.log_lines(0, 100)

    assert result == {"lines": [], "next": 0, "reset": False}


def test_log_lines_are_issued_with_our_own_monotonic_seq(tmp_path):
    service, _ = _service(tmp_path)
    service._log_seq = 7
    service._log_lines.extend([
        {"seq": 6, "t": "10:00:00", "src": "runner", "text": "older"},
        {"seq": 7, "t": "10:00:01", "src": "node", "text": "newer"},
    ])

    result = service.log_lines(6, 100)

    assert [line["seq"] for line in result["lines"]] == [7]
    assert result["next"] == 7
    assert result["reset"] is False


def test_a_cursor_from_a_previous_plugin_process_asks_for_a_reset(tmp_path):
    service, _ = _service(tmp_path)
    service._log_seq = 3
    service._log_lines.append({"seq": 3, "t": "10:00:00", "src": "runner", "text": "x"})

    # The surface still holds seq 500 from before N.E.K.O. restarted.
    result = service.log_lines(500, 100)

    assert result["reset"] is True
    assert result["next"] == 3


def test_log_limit_keeps_the_newest_lines(tmp_path):
    service, _ = _service(tmp_path)
    service._log_seq = 5
    service._log_lines.extend(
        [{"seq": i, "t": "10:00:00", "src": "runner", "text": str(i)} for i in range(1, 6)]
    )

    result = service.log_lines(0, 2)

    assert [line["seq"] for line in result["lines"]] == [4, 5]


def test_the_mirror_pumps_frames_into_the_ring_buffer(tmp_path):
    service, client = _service(tmp_path)
    client.log_frames = [
        {"id": 1, "t": "10:00:00", "src": "runner", "text": "启动"},
        {"id": 2, "t": "10:00:01", "src": "node", "text": "CreditShoppingMain"},
    ]

    async def run() -> None:
        pump = asyncio.create_task(service._log_pump())
        for _ in range(60):
            await asyncio.sleep(0.05)
            if service._log_seq >= 2:
                break
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(run())

    texts = [line["text"] for line in service.log_lines(0, 50)["lines"]]
    assert texts == ["启动", "CreditShoppingMain"]
    # Panel ids are consumed as a cursor; ours are what the surface sees.
    assert [line["seq"] for line in service.log_lines(0, 50)["lines"]] == [1, 2]
    assert service._log_cursor == 2


def test_the_mirror_restarts_numbering_when_the_panel_is_replaced(tmp_path):
    service, _ = _service(tmp_path)
    service._log_instance = "tok-1"
    service._log_cursor = 42
    service._log_seq = 42
    service._log_lines.append({"seq": 42, "t": "10:00:00", "src": "runner", "text": "old"})

    (tmp_path / "panel_token.txt").write_text("tok-2", encoding="utf-8")

    async def run() -> None:
        pump = asyncio.create_task(service._log_pump())
        await asyncio.sleep(0.2)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(run())

    assert service._log_instance == "tok-2"
    assert service._log_seq == 0
    assert list(service._log_lines) == []
    assert service.log_lines(500, 10)["reset"] is True


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_executor_diagnostics_are_available_without_a_plugin_dir():
    service = MaaendService()

    info = asyncio.run(service.describe_executor())

    assert info["executor_present"] is False
    assert info["python_ok"] is False
    assert info["manual_launcher"] == ""


def test_status_only_carries_the_executor_block_when_asked(tmp_path, monkeypatch):
    service = MaaendService()
    service.set_plugin_dir(tmp_path)
    service.configure({"base_url": "http://127.0.0.1:12702", "panel_dir": str(tmp_path)})
    write_token(tmp_path / "panel_token.txt", "tok-1")
    client = FakePanelClient()
    client.status_queue = [ready_status()]
    service._client = client

    plain = asyncio.run(service.get_status())
    assert "executor" not in plain

    # Not via ``_service``: the point here is that ``get_status`` reaches
    # ``launcher.describe`` itself when asked, so the stub must be the real one.
    monkeypatch.setattr(
        "maaend_bridge_plugin.launcher.describe",
        lambda *_a, **_k: {"python_ok": True, "manual_launcher": "x.bat"},
    )
    client.status_queue = [ready_status()]
    detailed = asyncio.run(service.get_status(include_executor=True))
    assert detailed["executor"]["python_ok"] is True
    assert detailed["executor"]["manual_launcher"] == "x.bat"