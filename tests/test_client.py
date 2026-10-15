"""Transport-layer mapping: HTTP shapes in, typed errors out.

Nothing here knows about tasks or cues — that is the point of the split. What
is asserted is the wire (path, params, the auth header) and the taxonomy, so a
panel that starts answering 409, or moves an endpoint, fails loudly rather
than being silently absorbed as "unreachable".
"""
from __future__ import annotations

import httpx
import pytest
from fakes import FakeHttp, FakeResponse
from maaend_bridge_plugin.client import (
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


def make_client(**kwargs) -> tuple:
    client = MaaendClient("http://127.0.0.1:12702", **kwargs)
    http = FakeHttp()
    client._http = http
    return client, http


# ---------------------------------------------------------------------------
# URL hygiene — a doubled slash 404s on the panel's exact-path router.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:12702", "http://127.0.0.1:12702"),
        ("http://127.0.0.1:12702/", "http://127.0.0.1:12702"),
        ("http://127.0.0.1:12702///", "http://127.0.0.1:12702"),
        ("127.0.0.1:12702", "http://127.0.0.1:12702"),
        ("  http://localhost:12702/  ", "http://localhost:12702"),
        ("", "http://127.0.0.1:12702"),
        (None, "http://127.0.0.1:12702"),
    ],
)
def test_normalise_base_url(raw, expected):
    assert normalise_base_url(raw) == expected


# ---------------------------------------------------------------------------
# Happy path: the token travels in the header, never in a URL.
# ---------------------------------------------------------------------------


async def test_status_sends_the_token_header_and_parses_the_body():
    client, http = make_client()
    http.queue(FakeResponse(200, {"state": "ready", "task_count": 46}))
    body = await client.status("secret-token")
    assert body["state"] == "ready"
    request = http.requests[0]
    assert request["method"] == "GET"
    assert request["path"] == "/api/status"
    assert request["headers"] == {"X-Auth-Token": "secret-token"}


async def test_root_path_has_no_trailing_slash():
    client, http = make_client()
    http.queue(FakeResponse(200, {"ok": True}))
    await client.stop("t")
    assert http.requests[0]["path"] == "/api/stop"


async def test_options_task_goes_in_the_query():
    client, http = make_client()
    http.queue(FakeResponse(200, {"tree": []}))
    await client.options("t", "CreditShoppingN2")
    assert http.requests[0]["params"] == {"task": "CreditShoppingN2"}


async def test_run_posts_the_panel_vocabulary_unchanged():
    client, http = make_client()
    http.queue(FakeResponse(202, {"state": "busy"}))
    await client.run("t", "DailyRewards", task_timeout_s=1800.0)
    body = http.requests[0]["json"]
    # ``task`` and ``timeout`` are the panel's own field names, not ours.
    assert body == {"task": "DailyRewards", "timeout": 1800.0}


async def test_run_omits_the_timeout_when_unspecified():
    client, http = make_client()
    http.queue(FakeResponse(202, {"state": "busy"}))
    await client.run("t", "DailyRewards")
    assert "timeout" not in http.requests[0]["json"]


async def test_run_passes_options_through_verbatim():
    client, http = make_client()
    http.queue(FakeResponse(202, {"state": "busy"}))
    options = {"CreditShoppingReserve": {"type": "switch", "value": True}}
    await client.run("t", "CreditShoppingN2", options=options)
    assert http.requests[0]["json"]["options"] == options


async def test_ping_accepts_only_the_panels_own_app_id():
    client, http = make_client()
    http.queue(FakeResponse(200, {"ok": True, "app": "maaend-panel"}))
    assert await client.ping() is True
    http.queue(FakeResponse(200, {"ok": True, "app": "something-else"}))
    assert await client.ping() is False


async def test_ping_never_raises_when_nothing_is_listening():
    client, http = make_client()
    http.raise_on_request = httpx.ConnectError("refused")
    assert await client.ping() is False


# ---------------------------------------------------------------------------
# Failure taxonomy — each category needs different words for the user.
# ---------------------------------------------------------------------------


async def test_401_is_an_auth_error():
    client, http = make_client()
    http.queue(FakeResponse(401, {"error": "缺少或错误的 token"}))
    with pytest.raises(MaaendAuthError) as info:
        await client.status("stale")
    assert info.value.status == 401
    assert "缺少或错误的 token" not in str(info.value)


async def test_403_is_a_forbidden_error_keeping_the_detail_for_logs():
    client, http = make_client()
    http.queue(FakeResponse(403, {"error": "Host 不被允许"}))
    with pytest.raises(MaaendForbidden) as info:
        await client.status("t")
    assert info.value.detail == "Host 不被允许"


async def test_400_is_a_rejection_not_a_crash():
    client, http = make_client()
    http.queue(FakeResponse(400, {"error": "RuntimeError: 已有任务在运行"}))
    with pytest.raises(MaaendRejected) as info:
        await client.run("t", "X")
    assert info.value.status == 400
    assert "已有任务在运行" in info.value.detail


async def test_503_is_not_ready_rather_than_unreachable():
    client, http = make_client()
    http.queue(FakeResponse(503, {"error": "未初始化"}))
    with pytest.raises(MaaendNotReady):
        await client.status("t")


async def test_a_refused_connection_is_unreachable():
    client, http = make_client()
    http.raise_on_request = httpx.ConnectError("connection refused")
    with pytest.raises(MaaendUnreachable):
        await client.status("t")


async def test_a_read_timeout_is_a_timeout_not_a_rejection():
    client, http = make_client()
    http.raise_on_request = httpx.ReadTimeout("too slow")
    with pytest.raises(MaaendTimeout):
        await client.status("t")


async def test_non_json_200_is_a_protocol_error():
    """Something else is on the port — that is not the same as "panel is down"."""
    client, http = make_client()
    http.queue(FakeResponse(200, None, text="<html>hello</html>"))
    with pytest.raises(MaaendProtocolError):
        await client.status("t")


async def test_tasks_requires_the_tasks_key():
    client, http = make_client()
    http.queue(FakeResponse(200, {"items": []}))
    with pytest.raises(MaaendProtocolError):
        await client.tasks("t")


async def test_tasks_drops_non_dict_entries():
    client, http = make_client()
    http.queue(FakeResponse(200, {"tasks": [{"name": "A"}, "junk", None]}))
    assert await client.tasks("t") == [{"name": "A"}]


async def test_an_unknown_status_code_stays_generic():
    client, http = make_client()
    http.queue(FakeResponse(418, {"error": "teapot"}))
    with pytest.raises(MaaendError) as info:
        await client.status("t")
    assert type(info.value) is MaaendError
    assert info.value.status == 418


async def test_requesting_through_a_closed_client_is_unreachable():
    client = MaaendClient("http://127.0.0.1:12702")
    with pytest.raises(MaaendUnreachable):
        await client.status("t")


async def test_client_idempotently_opens_and_closes():
    client = MaaendClient("http://127.0.0.1:12702")
    assert client.is_open is False
    await client.open()
    assert client.is_open is True
    await client.close()
    assert client.is_open is False
    await client.close()  # idempotent
