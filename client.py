"""Transport for the MaaEnd self-built executor's control panel.

The executor lives outside N.E.K.O. — it is a separate Python process
(``.workbuddy/panel_server.py`` in the MaaEnd resource repository) that owns a
MaaFramework 5.x runtime and drives *Arknights: Endfield* through Seize
(synthetic keyboard/mouse input). It exposes a **loopback-only** HTTP API:

======================  ==========================================
``GET  /api/ping``      liveness; the only endpoint without a token
``GET  /api/status``    global state: ``idle|ready|busy``, current task,
                        its live ``nodes``, ``last_result``, resolution
``GET  /api/tasks``     the task catalogue (46 entries), already localised
``GET  /api/options``   one task's option tree (``?task=<name>``)
``GET  /api/screenshot`` 1280x720 PNG of the game window
``GET  /api/logs``      SSE log bus (``?since=<id>``)
``POST /api/prepare``   initialise Resource/Controller/Agents (3-5 s)
``POST /api/run``       start a task -> 202 + status, or 400
``POST /api/stop``      stop the running task
``POST /api/check-resolution``  re-read the game window's client rect
======================  ==========================================

Every ``/api/*`` endpoint except ``/api/ping`` needs ``X-Auth-Token``, and the
server additionally pins the ``Host`` (and ``Origin``, when present) header to
``127.0.0.1``/``localhost``. The token is regenerated with
``secrets.token_urlsafe(32)`` **on every backend start** and written to
``.workbuddy/panel_token.txt``. That has two consequences this plugin is built
around:

* A request may need a token that does not exist yet, and any cached token can
  be rotated out from under us at any moment. So the token is read from disk
  per attempt, by the service — never captured once at construction.
* **The token doubles as the panel's instance identity.** There is no session
  id in this API, but a change in the token file is proof that the panel
  process restarted, which is exactly the signal needed to stop trusting
  bookkeeping from the previous process (a task it was running cannot report
  back). See the ``MaaendService`` watch loop.

Scope of this module is **transport only**: it opens connections, speaks the
wire protocol, and normalises failures into typed errors. It holds no task
bookkeeping, no retry policy and no token-file knowledge, and it deliberately
does not import the plugin SDK, so it can be unit-tested standalone.

Two deliberate omissions:

* **No SSE.** ``/api/logs`` is a raw, process-global log bus whose ids restart
  at 1 whenever the panel restarts, with no per-task framing and a one-hour
  stream deadline. Everything the plugin needs — the live node list, the
  terminal ``last_result``, the panel's own resolution verdict — is in
  ``/api/status``, which is cheap enough to poll (it takes no lock, unlike
  ``/api/screenshot``). Consuming the log bus would mean re-deriving instance
  identity and cursor reset for a strictly noisier signal.
* **No ``/api/screenshot``.** 1280x720 PNGs are ~900 KB and the endpoint
  shares a mutex with the running task; nothing in the dialog path needs the
  picture.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

# The panel writes ``{"error": "..."}`` on every failure path. Its prose is
# written for an operator and can echo request details, so it is stashed on the
# exception for the log and never returned to the model.
PANEL_APP_ID = "maaend-panel"

# ``/api/prepare`` loads 5809 resource nodes and spawns two agent processes;
# the contract quotes 3-5 s, and the first call on a cold page file is slower.
PREPARE_TIMEOUT_MIN_S = 90.0

# ``/api/screenshot`` shares the panel's MaaFramework mutex with a running task,
# so a request can legitimately queue behind the executor for a while.
SCREENSHOT_TIMEOUT_S = 20.0

# The log stream emits ``: ping`` every second while idle. A read window of four
# heartbeats shrugs off a scheduling hiccup while still noticing a silently-dead
# connection instead of hanging on it.
SSE_READ_TIMEOUT_S = 5.0


class MaaendError(Exception):
    """Base class for every panel failure.

    ``code`` is a stable machine-readable category the service maps to a line
    the character can say; ``status`` is the HTTP status when there was one.
    ``detail`` is the panel's own prose, for logs only.
    """

    code = "MAAEND_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        status: Optional[int] = None,
        detail: str = "",
    ) -> None:
        super().__init__(message or self.code)
        self.status = status
        self.detail = detail


class MaaendUnreachable(MaaendError):
    """The panel is not listening (not started, or stopped, or a wrong port).

    This is the routine state, not a malfunction: the panel only runs while
    the user has started it, and a packaged N.E.K.O. session has no way to
    start it. Nothing here is retried automatically.
    """

    code = "UNREACHABLE"


class MaaendTimeout(MaaendError):
    """The panel accepted the connection but did not answer in budget."""

    code = "TIMEOUT"


class MaaendAuthError(MaaendError):
    """401 — the token is missing, stale, or the file has not been written yet."""

    code = "UNAUTHORIZED"


class MaaendForbidden(MaaendError):
    """403 — the panel's Host/Origin whitelist rejected the request.

    ``base_url`` must therefore stay a literal ``127.0.0.1``/``localhost``
    address; a hostname that resolves elsewhere trips this.
    """

    code = "FORBIDDEN"


class MaaendRejected(MaaendError):
    """400 — the panel understood the request and refused it.

    Covers "already running", "unknown task", a non-16:9 resolution and a
    failed ``/api/prepare``. The service never surfaces ``detail``.
    """

    code = "REJECTED"


class MaaendNotReady(MaaendError):
    """503 — the panel is up but its runtime is not; ``/api/screenshot`` only."""

    code = "NOT_READY"


class MaaendProtocolError(MaaendError):
    """The response was not the shape the contract promises.

    Kept separate from :class:`MaaendUnreachable` on purpose: "something else
    is on that port" and "the panel is down" need different words.
    """

    code = "PROTOCOL"


class MaaendClient:
    """One HTTP client, no policy. Create it, call it, close it.

    The caller owns connection reuse *and* retry: every method performs exactly
    one attempt and either returns the panel's parsed body or raises. That
    split is what lets the service re-read the token file between attempts
    instead of baking in a token it captured at startup.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_timeout: float = 10.0,
        # The panel is on loopback: a connect either completes in microseconds
        # or is refused immediately, so a long connect timeout only delays
        # noticing that it is down.
        connect_timeout: float = 1.5,
    ) -> None:
        self.base_url = normalise_base_url(base_url)
        self.http_timeout = max(0.5, float(http_timeout))
        self.connect_timeout = max(0.5, float(connect_timeout))
        self._http: Optional[httpx.AsyncClient] = None

    # -- lifecycle -----------------------------------------------------

    async def open(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.http_timeout, connect=self.connect_timeout),
                follow_redirects=False,
                # The panel pins Host to 127.0.0.1/localhost; httpx derives it
                # from the URL, which is already a literal loopback address.
                trust_env=False,
            )

    async def close(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            try:
                await http.aclose()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return self._http is not None

    # -- endpoints -----------------------------------------------------

    async def ping(self) -> bool:
        """Liveness probe. No token required; never raises.

        The body carries the panel's own app id, so a stray process on the
        port is reported as "not the panel" rather than as a live panel whose
        every later call mysteriously 404s.
        """
        http = self._http
        if http is None:
            return False
        try:
            response = await http.get("/api/ping")
        except (httpx.HTTPError, OSError):
            return False
        if response.status_code != 200:
            return False
        body = _maybe_json(response)
        return isinstance(body, dict) and body.get("app") == PANEL_APP_ID

    async def status(self, token: str) -> Dict[str, Any]:
        return await self._json("GET", "/api/status", token=token)

    async def tasks(self, token: str) -> List[Dict[str, Any]]:
        body = await self._json("GET", "/api/tasks", token=token)
        tasks = body.get("tasks") if isinstance(body, dict) else None
        if not isinstance(tasks, list):
            raise MaaendProtocolError(
                "GET /api/tasks did not return a task list", detail=repr(body)[:400]
            )
        return [item for item in tasks if isinstance(item, dict)]

    async def options(self, token: str, task_name: str) -> Dict[str, Any]:
        return await self._json(
            "GET", "/api/options", token=token, params={"task": task_name}
        )

    async def prepare(self, token: str) -> Dict[str, Any]:
        return await self._json("POST", "/api/prepare", token=token, timeout="prepare")

    async def check_resolution(self, token: str) -> Dict[str, Any]:
        return await self._json("POST", "/api/check-resolution", token=token)

    async def run(
        self,
        token: str,
        task_name: str,
        *,
        task_timeout_s: Optional[float] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Start a task. Returns the panel's status snapshot (HTTP 202).

        ``options`` is passed through verbatim: the panel's four value shapes
        (``switch``/``select``/``checkbox``/``input``/``hotkey``) are the
        contract, and omitting an option is how you ask for its default.
        """
        payload: Dict[str, Any] = {"task": task_name}
        if task_timeout_s is not None:
            payload["timeout"] = task_timeout_s
        if options:
            payload["options"] = options
        return await self._json("POST", "/api/run", token=token, json_body=payload)

    async def stop(self, token: str) -> Dict[str, Any]:
        """Request a stop. Returns the panel's ``{"ok": bool, ...}`` body.

        Note the status code is 200 even when there was nothing to stop, so
        success is ``body["ok"] is True`` and nothing else.
        """
        return await self._json("POST", "/api/stop", token=token)

    async def screenshot(self, token: str) -> bytes:
        """The game window as PNG (1280x720, ~900 KiB).

        Shares a mutex with a running task on the panel side, so this can queue
        behind the executor; the read timeout is widened for that reason.
        """
        http = self._http
        if http is None:
            raise MaaendUnreachable("client is not open")
        try:
            response = await http.request(
                "GET",
                "/api/screenshot",
                headers={"X-Auth-Token": token} if token else {},
                timeout=httpx.Timeout(SCREENSHOT_TIMEOUT_S, connect=self.connect_timeout),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise MaaendUnreachable(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise MaaendTimeout(str(exc)) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise MaaendUnreachable(str(exc)) from exc

        if response.status_code == 401:
            raise MaaendAuthError("panel rejected the token", status=401)
        if response.status_code == 403:
            raise MaaendForbidden("panel rejected the Host/Origin header", status=403)
        if response.status_code == 503:
            raise MaaendNotReady("panel is not ready", status=503, detail=_detail(response))
        if response.status_code != 200:
            raise MaaendRejected(
                "panel refused the screenshot", status=response.status_code,
                detail=_detail(response),
            )
        data = response.content
        if not data:
            raise MaaendProtocolError("panel returned an empty screenshot")
        return data

    async def open_log_stream(
        self, token: str, *, since: int = 0
    ) -> AsyncIterator[Dict[str, Any]]:
        """One ``/api/logs`` connection, yielding parsed frames until it ends.

        **One attempt, then return** — the reconnect loop and the cursor policy
        live in the service (the same split as everywhere else in this module),
        because only the service can decide whether the cursor it holds is still
        meaningful after a panel restart.

        The panel keeps the connection open for an hour and emits ``: ping``
        every second while idle, so the read timeout is short: a stream that
        goes quiet for that long is dead, and reconnecting is cheap.

        Frames whose payload is not the documented JSON object are skipped
        rather than raised on: this is a log display, and one malformed line
        must not cost the whole stream.
        """
        http = self._http
        if http is None:
            raise MaaendUnreachable("client is not open")
        params: Dict[str, Any] = {"since": int(max(0, since))}
        headers = {"X-Auth-Token": token} if token else {}
        try:
            async with http.stream(
                "GET",
                "/api/logs",
                params=params,
                headers=headers,
                timeout=httpx.Timeout(SSE_READ_TIMEOUT_S, connect=self.connect_timeout),
            ) as response:
                if response.status_code == 401:
                    raise MaaendAuthError("panel rejected the token", status=401)
                if response.status_code == 403:
                    raise MaaendForbidden("panel rejected the Host/Origin header", status=403)
                if response.status_code != 200:
                    raise MaaendError(
                        f"log stream refused ({response.status_code})",
                        status=response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue  # keepalive comment
                    if not line.startswith("data:"):
                        continue
                    payload = _parse_json(line[5:].strip())
                    if isinstance(payload, dict):
                        yield payload
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise MaaendUnreachable(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise MaaendTimeout(str(exc)) from exc
        except (MaaendError,):
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise MaaendUnreachable(str(exc)) from exc

    # -- plumbing ------------------------------------------------------

    async def _json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[Any] = None,
    ) -> Dict[str, Any]:
        http = self._http
        if http is None:
            raise MaaendUnreachable("client is not open")
        kwargs: Dict[str, Any] = {}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        if token:
            kwargs["headers"] = {"X-Auth-Token": token}
        if timeout == "prepare":
            # /api/prepare loads 5809 resource nodes and spawns two agent
            # processes; the contract quotes 3-5 s and a cold first call is
            # slower, so it gets its own floor rather than the REST budget.
            kwargs["timeout"] = httpx.Timeout(
                max(self.http_timeout, PREPARE_TIMEOUT_MIN_S),
                connect=self.connect_timeout,
            )
        try:
            response = await http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise MaaendUnreachable(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise MaaendTimeout(str(exc)) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise MaaendUnreachable(str(exc)) from exc

        code = response.status_code
        if code in (401,):
            raise MaaendAuthError("panel rejected the token", status=code)
        if code == 403:
            raise MaaendForbidden(
                "panel rejected the Host/Origin header", status=code, detail=_detail(response)
            )
        if code == 503:
            raise MaaendNotReady("panel is not ready", status=code, detail=_detail(response))
        if code == 202 or code == 200:
            body = _maybe_json(response)
            if not isinstance(body, dict):
                raise MaaendProtocolError(
                    f"{method} {path} did not return JSON", status=code,
                    detail=_text(response)[:400],
                )
            return body
        if code == 400:
            raise MaaendRejected(
                "panel refused the request", status=code, detail=_detail(response)
            )
        raise MaaendError(
            f"unexpected status {code}", status=code, detail=_detail(response)
        )


def normalise_base_url(raw: Any) -> str:
    """Trim a configured base URL to something ``httpx`` can join paths onto.

    ``httpx`` appends the request path to ``base_url``, so a trailing slash
    produces ``//api/status``. The panel's router matches exact paths — a
    doubled slash 404s. A missing scheme is defaulted rather than rejected so
    that a bare ``127.0.0.1:12702`` in the settings pane still works.
    """
    text = str(raw or "").strip() or "http://127.0.0.1:12702"
    if "://" not in text:
        text = "http://" + text
    return text.rstrip("/")


def _text(response: httpx.Response) -> str:
    try:
        return response.text
    except Exception:
        return ""


def _maybe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _parse_json(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except Exception:
        return None


def _detail(response: httpx.Response) -> str:
    """Pull the panel's own error prose out of a body, for logs only."""
    body = _maybe_json(response)
    if isinstance(body, dict):
        message = body.get("error") or body.get("message")
        if isinstance(message, str):
            return message[:400]
    return _text(response)[:400]


__all__ = [
    "MaaendClient",
    "MaaendError",
    "MaaendUnreachable",
    "MaaendTimeout",
    "MaaendAuthError",
    "MaaendForbidden",
    "MaaendRejected",
    "MaaendNotReady",
    "MaaendProtocolError",
    "normalise_base_url",
    "PANEL_APP_ID",
]
