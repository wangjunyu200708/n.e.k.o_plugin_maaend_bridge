"""Shared fakes for the maaend_bridge tests.

Kept out of the test modules so the client and service suites can both use
them: a fake panel HTTP client, and a small helper for the token file that
doubles as the panel's instance id.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class FakeResponse:
    """The slice of ``httpx.Response`` the client actually touches."""

    def __init__(self, status_code: int, body: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or ("" if body is None else str(body))

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHttp:
    """Stands in for ``httpx.AsyncClient`` inside :class:`MaaendClient`.

    Records every request so a test can assert on the *wire* — path, params
    and the ``X-Auth-Token`` header — rather than only on the return value.
    """

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.responses: List[Any] = []
        self.raise_on_request: Optional[BaseException] = None

    def queue(self, response: Any) -> None:
        self.responses.append(response)

    async def request(self, method: str, path: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "path": path, **kwargs})
        if self.raise_on_request is not None:
            raise self.raise_on_request
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {path}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get(self, path: str, **kwargs: Any) -> FakeResponse:
        return await self.request("GET", path, **kwargs)

    async def aclose(self) -> None:
        return None


class FakePanelClient:
    """Stands in for :class:`~maaend_bridge_plugin.client.MaaendClient`.

    Each endpoint is backed by a queue of answers, so a test can script a
    whole task lifetime (busy … busy … ready) and then assert on the cues.
    """

    def __init__(self) -> None:
        self.is_open = True
        self.status_queue: List[Any] = []
        self.tasks_response: Any = []
        self.prepare_response: Any = {}
        self.run_response: Any = {}
        self.stop_response: Dict[str, Any] = {"ok": True}
        self.ping_result = True
        self.calls: List[tuple] = []
        self.default_status: Dict[str, Any] = ready_status()

    # -- scripted endpoints -------------------------------------------

    async def open(self) -> None:
        self.is_open = True

    async def close(self) -> None:
        self.is_open = False

    async def ping(self) -> bool:
        self.calls.append(("ping",))
        return self.ping_result

    async def status(self, token: str) -> Dict[str, Any]:
        self.calls.append(("status", token))
        if not self.status_queue:
            return dict(self.default_status)
        item = self.status_queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, dict) else dict(self.default_status)

    async def tasks(self, token: str) -> List[Dict[str, Any]]:
        self.calls.append(("tasks", token))
        if isinstance(self.tasks_response, BaseException):
            raise self.tasks_response
        return list(self.tasks_response)

    async def options(self, token: str, task_name: str) -> Dict[str, Any]:
        self.calls.append(("options", token, task_name))
        return {"task": task_name, "tree": []}

    async def prepare(self, token: str) -> Dict[str, Any]:
        self.calls.append(("prepare", token))
        if isinstance(self.prepare_response, BaseException):
            raise self.prepare_response
        return dict(self.prepare_response) if self.prepare_response else ready_status()

    async def run(self, token: str, task_name: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("run", token, task_name, kwargs))
        if isinstance(self.run_response, BaseException):
            raise self.run_response
        return dict(self.run_response) if self.run_response else busy_status(task_name)

    async def stop(self, token: str) -> Dict[str, Any]:
        self.calls.append(("stop", token))
        if isinstance(self.stop_response, BaseException):
            raise self.stop_response
        return dict(self.stop_response)


def ready_status(**overrides: Any) -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "state": "ready",
        "ready": True,
        "elevated": True,
        "hwnd": 1,
        "focused": True,
        "minimized": False,
        "resolution": {"w": 1280, "h": 720, "ratio": 1.7778, "required": "16:9", "ok": True, "message": ""},
        "current": None,
        "last_result": None,
        "error": None,
        "task_count": 46,
    }
    status.update(overrides)
    return status


def busy_status(
    task: str = "DailyRewards",
    *,
    started: float = 1000.0,
    nodes: Optional[List[str]] = None,
    elapsed: float = 5.0,
    label: str = "每日奖励",
) -> Dict[str, Any]:
    return ready_status(
        state="busy",
        current={
            "task": task,
            "label": label,
            "entry": "DailyRewardsMain",
            "started": started,
            "timeout": 1800,
            "elapsed": elapsed,
            "nodes": list(nodes or []),
        },
    )


def terminal_status(
    task: str = "DailyRewards",
    *,
    started: float = 1000.0,
    succeeded: bool = True,
    failed: bool = False,
    elapsed: float = 67.2,
    nodes: Optional[List[str]] = None,
    label: str = "每日奖励",
) -> Dict[str, Any]:
    """A ``ready`` snapshot whose ``current`` carries ``ended``.

    This is exactly the shape the panel produces after ``_run_task`` returns:
    the ``current`` block stays, ``ended`` is stamped, and ``last_result``
    holds the verdict.
    """
    status = busy_status(task, started=started, nodes=nodes, elapsed=elapsed, label=label)
    status["state"] = "ready"
    status["current"]["ended"] = 1.0
    status["last_result"] = {
        "task": task,
        "succeeded": succeeded,
        "failed": failed,
        "elapsed": elapsed,
    }
    return status


def write_token(path: Path, token: str) -> None:
    path.write_text(token, encoding="utf-8")
