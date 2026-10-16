#!/usr/bin/env python3
"""MaaEnd Control Panel —— 后端服务（长驻提权进程）。

设计要点（来自专家评估）
------------------------
1. **长驻进程**：Resource / Controller / Agent 只初始化一次并常驻，
   每个任务作为进程内的一个线程运行。避免每次重载 5809 个节点、重启 agent。
2. **只绑定 127.0.0.1**：不监听任何外部地址。
3. **随机 token**：启动时生成，写入 panel_token.txt；所有 /api/* 必须携带。
4. **Host / Origin 校验**：阻止别的网页对本机提权进程发 drive-by 请求
   （该进程能发送键鼠输入，必须防 CSRF）。
5. **16:9 预检**：分辨率不达标时**在 API 层就明确报错**，
   而不是让 go-service 静默在 stdout 里叫停。
6. **周期重新聚焦**：Seize 用 SendInput，只投递给前台窗口。
   长任务期间每 5 秒检查一次焦点，跑了就抢回来。
7. **日志用 SSE 推送**：节点流转从 maafw.log 增量解析，实时显示"当前在哪个节点"。

注意：本进程需要**管理员权限**（Seize 输入）。用 start_panel.bat 启动。

用法
----
    python panel_server.py                 # 默认 127.0.0.1:12702
    python panel_server.py --port 12702 --open
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import io
import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import packmeta  # 资源层自描述：窗口 / 项目身份 / 完整性
import runner as R  # 复用已有执行器
from override_gen import gen_override_object, load_interface

HERE = Path(__file__).resolve().parent

# 资源层随插件分发（executor/maaend/）。--maaend-dir 可覆盖。
DEFAULT_MAAEND = HERE / "maaend"
DEFAULT_PORT = 12702
TARGET_RATIO = 16 / 9
RATIO_TOLERANCE = 0.02
FOCUS_CHECK_INTERVAL = 5.0
NODE_RE = re.compile(r"Node\.PipelineNode\.Starting")
NODE_NAME_RE = re.compile(r'"name":"([^"]+)"')


# --------------------------------------------------------------------------
# 日志总线：把 runner 的 print 与节点流转统一成可增量拉取的日志流
# --------------------------------------------------------------------------
class LogBus:
    def __init__(self, cap: int = 3000) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._lines: list[dict] = []
        self._seq = 0
        self._cap = cap

    def emit(self, text: str, src: str = "runner") -> None:
        with self._cond:
            self._seq += 1
            self._lines.append(
                {
                    "id": self._seq,
                    "t": time.strftime("%H:%M:%S"),
                    "src": src,
                    "text": text,
                }
            )
            if len(self._lines) > self._cap:
                self._lines = self._lines[-self._cap :]
            self._cond.notify_all()

    def since(self, last_id: int) -> list[dict]:
        with self._lock:
            return [ln for ln in self._lines if ln["id"] > last_id]

    def wait(self, last_id: int, timeout: float = 1.0) -> list[dict]:
        with self._cond:
            if any(ln["id"] > last_id for ln in self._lines):
                return [ln for ln in self._lines if ln["id"] > last_id]
            self._cond.wait(timeout)
            return [ln for ln in self._lines if ln["id"] > last_id]

    def tail(self, n: int = 200) -> list[dict]:
        with self._lock:
            return self._lines[-n:]


class Tee(io.TextIOBase):
    """把 stdout 同时写到真实终端与日志总线。"""

    def __init__(self, bus: LogBus) -> None:
        self._bus = bus

    def write(self, s: str) -> int:  # noqa: D102
        s = s.rstrip("\r\n")
        if s.strip():
            self._bus.emit(s, "runner")
        return len(s)

    def flush(self) -> None:  # noqa: D102
        pass


# --------------------------------------------------------------------------
# i18n：interface.json 的 label 全是 $key，语言包是扁平键
# --------------------------------------------------------------------------
def load_i18n(assets: Path, lang: str = "zh_cn") -> dict:
    p = assets / "locales" / "interface" / f"{lang}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


class Translator:
    def __init__(self, table: dict) -> None:
        self.t = table

    def __call__(self, s, default=None):
        if isinstance(s, str) and s.startswith("$"):
            key = s[1:]
            val = self.t.get(key)
            if isinstance(val, str):
                return val
            return default if default is not None else key
        return s if s is not None else (default if default is not None else "")


# --------------------------------------------------------------------------
# 选项树：把 interface.json 的 option 定义整理成前端可直接渲染的结构
# --------------------------------------------------------------------------
def build_option_tree(names, option_defs, T, depth: int = 0):
    out = []
    for name in names or []:
        d = option_defs.get(name)
        if not isinstance(d, dict):
            continue
        t = d.get("type")
        node = {
            "name": name,
            "type": t,
            "label": T(d.get("label"), name),
            "depth": depth,
        }
        desc = d.get("description")
        if desc:
            node["description"] = T(desc, "")

        if t in ("switch", "select", "checkbox"):
            cases = []
            for c in d.get("cases", []) or []:
                cases.append(
                    {
                        "name": c.get("name"),
                        "label": T(c.get("label"), c.get("name")),
                        "option": build_option_tree(c.get("option"), option_defs, T, depth + 1),
                    }
                )
            node["cases"] = cases
            node["default_case"] = d.get("default_case")
        elif t == "input":
            ins = []
            for i in d.get("inputs", []) or []:
                ins.append(
                    {
                        "name": i.get("name"),
                        "label": T(i.get("label"), i.get("name")),
                        "default": i.get("default", ""),
                        "pipeline_type": i.get("pipeline_type", "string"),
                        "verify": i.get("verify"),
                        "pattern_msg": T(i.get("pattern_msg"), ""),
                    }
                )
            node["inputs"] = ins
        elif t == "hotkey":
            hks = []
            for h in d.get("hotkeys", []) or []:
                hks.append(
                    {
                        "name": h.get("name"),
                        "label": T(h.get("label"), h.get("name")),
                        "default": h.get("default", ""),
                    }
                )
            node["hotkeys"] = hks
        out.append(node)
    return out


# --------------------------------------------------------------------------
# 应用主体
# --------------------------------------------------------------------------
class Panel:
    def __init__(self, maaend_dir: Path, token: str) -> None:
        self.dir = maaend_dir
        self.token = token
        self.bus = LogBus()
        self.op = threading.RLock()  # MaaFw 调用锁：截图/任务不并发
        self.state = "idle"  # idle | ready | busy
        self.runner: R.Runner | None = None
        self.thread: threading.Thread | None = None
        self.current: dict | None = None
        self.last_result: dict | None = None
        self.resolution: dict | None = None
        self.error: str | None = None
        self._log_offset: int | None = None
        self._last_node: str | None = None
        self._iface: dict | None = None
        self._T: Translator | None = None
        # 资源层自报的家门（名字/版本/完整性）。缓存：它随进程不变，换资源层
        # 本来就要重启这个进程。
        self._pack_cache: dict | None = None

    # ---- 初始化 ----

    @property
    def interface(self) -> dict:
        if self._iface is None:
            self._iface = load_interface(str(self.dir))
        return self._iface

    @property
    def T(self) -> Translator:
        if self._T is None:
            self._T = Translator(load_i18n(self.dir))
        return self._T

    def ensure_ready(self) -> None:
        with self.op:
            if self.runner is not None and self.state in ("ready", "busy"):
                return
            self.bus.emit("=== 初始化执行环境 ===")
            self.error = None
            # 先查资源层完整性。缺声明时 MaaFw 会在很深的地方报一个看不出所以然
            # 的错（或者在寻找某个并不存在的窗口时卡住），而"没装资源层""没有
            # Win32 控制器声明"是现在就能说清的事。
            pack = packmeta.read_pack(self.dir)
            self._pack_cache = self._read_pack_info()
            if pack is None or not pack.ok:
                self.error = self._pack_problem_text(pack)
                self.bus.emit(f"[FAIL] {self.error}", "error")
                raise RuntimeError(self.error)
            self.bus.emit(f"资源层：{pack.name} {pack.version}".rstrip())
            r = R.Runner(self.dir, log=True)
            try:
                r.setup_resource()
                r.setup_controller()
                r.setup_tasker()
                r.setup_agents()
                try:
                    self.runner.tasker.set_log_dir(str(R.LOG_DIR / "maa"))
                except Exception:
                    pass
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                self.bus.emit(f"[FAIL] 初始化失败: {self.error}")
                raise
            self.runner = r
            self.state = "ready"
            self.check_resolution()
            self.bus.emit("=== 环境就绪 ===")

    # ---- 前置条件校验 ----

    def check_resolution(self) -> dict:
        hwnd = getattr(self.runner, "_hwnd", None)
        if not hwnd:
            self.resolution = None
            return {}
        u32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rc = RECT()
        u32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
        w = rc.right - rc.left
        h = rc.bottom - rc.top
        ratio = (w / h) if h else 0.0
        ok = abs(ratio - TARGET_RATIO) <= RATIO_TOLERANCE
        self.resolution = {
            "w": w,
            "h": h,
            "ratio": round(ratio, 4),
            "required": "16:9",
            "ok": ok,
            "message": "" if ok else f"游戏窗口 {w}x{h}（比例 {ratio:.4f}）不是 16:9，go-service 会强制停止任务。请把游戏分辨率调成 16:9 后重试。",
        }
        self.bus.emit(
            f"分辨率检查: {w}x{h} ratio={ratio:.4f} -> {'OK' if ok else '不通过'}"
        )
        return self.resolution

    def focus_ok(self) -> bool:
        hwnd = getattr(self.runner, "_hwnd", None)
        if not hwnd:
            return False
        return ctypes.windll.user32.GetForegroundWindow() == hwnd

    def is_minimized(self) -> bool:
        hwnd = getattr(self.runner, "_hwnd", None)
        if not hwnd:
            return False
        return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)))

    def bring_front(self) -> bool:
        hwnd = getattr(self.runner, "_hwnd", None)
        if not hwnd:
            return False
        return R.Runner._bring_to_front(hwnd)

    # ---- 任务 ----

    def start_task(self, task_name: str, option_values: dict, timeout: float) -> dict:
        if self.state == "busy":
            raise RuntimeError("已有任务在运行")
        self.ensure_ready()
        res = self.resolution or {}
        if not res.get("ok"):
            raise RuntimeError(res.get("message") or "分辨率不是 16:9，无法运行任务")
        tasks = {t.get("name"): t for t in self.interface.get("task", [])}
        task_def = tasks.get(task_name)
        if task_def is None:
            raise RuntimeError(f"未知任务 {task_name}")
        entry = task_def.get("entry")

        self.state = "busy"
        self.error = None
        self.last_result = None
        self.current = {
            "task": task_name,
            "label": self.T(task_def.get("label"), task_name),
            "entry": entry,
            "started": time.time(),
            "timeout": timeout,
            "nodes": [],
        }
        self._log_offset = None
        self._last_node = None
        self.bus.emit(f"=== 启动任务 {task_name} (entry={entry}) ===")
        self.thread = threading.Thread(
            target=self._run_task,
            args=(task_name, entry, option_values or {}, timeout),
            daemon=True,
        )
        self.thread.start()
        return self.status()

    def _run_task(self, task_name, entry, values, timeout) -> None:
        result = {"task": task_name, "succeeded": None, "failed": None, "elapsed": None}
        t0 = time.time()
        try:
            override = gen_override_object(task_name, values, assets_dir=str(self.dir))
            self.bus.emit(f"override 编译完成，覆盖 {len(override)} 个节点")

            front = self.bring_front()
            self.bus.emit(f"post_task 前置前 = {front}")

            with self.op:
                tasker = self.runner.tasker
                try:
                    tasker.set_log_dir(str(R.LOG_DIR / "maa"))
                    tasker.set_save_draw(True)
                    tasker.set_debug_mode(True)
                except Exception:
                    pass
                job = tasker.post_task(entry, override)
                self.bus.emit(f"job_id={job.job_id}")

            last_focus_check = 0.0
            while True:
                time.sleep(0.4)
                self._poll_nodes()
                now = time.time()
                if now - last_focus_check >= FOCUS_CHECK_INTERVAL:
                    last_focus_check = now
                    if not self.focus_ok():
                        self.bus.emit("焦点丢失，重新置前游戏窗口", "warn")
                        self.bring_front()
                with self.op:
                    running = self.runner.tasker.running
                if not running:
                    break
                if timeout and (now - t0) > timeout:
                    self.bus.emit(f"超过 {timeout:.0f}s，主动停止任务", "warn")
                    with self.op:
                        self.runner.tasker.post_stop().wait()
                    break

            elapsed = time.time() - t0
            result["elapsed"] = round(elapsed, 1)
            result["succeeded"] = bool(getattr(job, "succeeded", False))
            result["failed"] = bool(getattr(job, "failed", False))
            self.bus.emit(
                f"任务结束：{'成功' if result['succeeded'] else '失败/未完成'}，耗时 {elapsed:.1f}s",
                "ok" if result["succeeded"] else "warn",
            )
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            self.bus.emit(f"[FAIL] {result['error']}", "error")
            self.error = result["error"]
        finally:
            self.last_result = result
            if self.current is not None:
                self.current["ended"] = time.time()
            self.state = "ready"

    def _poll_nodes(self) -> None:
        """从 maafw.log 增量解析节点名，实时显示当前走到哪一步。"""
        p = R.LOG_DIR / "maa" / "maafw.log"
        if not p.exists():
            return
        size = p.stat().st_size
        if self._log_offset is None:
            self._log_offset = size
            return
        if size <= self._log_offset:
            return
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_offset)
                chunk = f.read()
            self._log_offset = size
        except Exception:
            return
        for line in chunk.splitlines():
            if not NODE_RE.search(line):
                continue
            m = NODE_NAME_RE.search(line)
            if not m:
                continue
            name = m.group(1)
            if name == self._last_node:
                continue
            self._last_node = name
            if self.current is not None:
                self.current["nodes"].append(name)
            self.bus.emit(name, "node")

    def stop_task(self) -> dict:
        if self.state != "busy" or self.runner is None:
            return {"ok": False, "message": "当前没有运行中的任务"}
        with self.op:
            self.runner.tasker.post_stop().wait()
        self.bus.emit("已请求停止任务", "warn")
        return {"ok": True}

    # ---- 只读接口 ----

    def screenshot_png(self) -> bytes:
        self.ensure_ready()
        # 窗口最小化时 ScreenDC 抓到的是屏幕上的其他窗口（实测抓到过 CodeBuddy），
        # 必须先还原，否则前端看到的画面是错的。
        if self.is_minimized():
            hwnd = ctypes.c_void_p(self.runner._hwnd)
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.4)
            self.bus.emit("游戏窗口处于最小化，已还原后再截图", "warn")
        with self.op:
            img = self.runner.controller.post_screencap().wait().get()
        import numpy as np
        from PIL import Image

        arr = np.asarray(img)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        return buf.getvalue()

    def tasks_payload(self) -> list[dict]:
        T = self.T
        out = []
        for t in self._task_defs():
            out.append(
                {
                    "name": t.get("name"),
                    "label": T(t.get("label"), t.get("name")),
                    "description": T(t.get("description"), ""),
                    "entry": t.get("entry"),
                    "group": t.get("group") or [],
                    "controller": t.get("controller") or [],
                    "options": t.get("option") or [],
                }
            )
        return out

    def options_payload(self, task_name: str) -> dict:
        defs = self._option_defs()
        task = next((t for t in self._task_defs() if t.get("name") == task_name), None)
        if task is None:
            raise RuntimeError(f"未知任务 {task_name}")
        return {
            "task": task_name,
            "label": self.T(task.get("label"), task_name),
            "tree": build_option_tree(task.get("option"), defs, self.T),
        }

    def _task_defs(self) -> list:
        """interface.json 里声明的任务；读不到就是空列表（原因见 _task_count）。"""
        try:
            tasks = self.interface.get("task", [])
        except Exception:
            return []
        return tasks if isinstance(tasks, list) else []

    def _option_defs(self) -> dict:
        try:
            options = self.interface.get("option", {})
        except Exception:
            return {}
        return options if isinstance(options, dict) else {}

    def _task_count(self) -> int:
        """任务数；没有资源层时是 0，而不是把 /api/status 整个炸掉。

        ``self.interface`` 走 ``load_interface``，它靠 build_interface.py 子进程
        展开 import。没有 interface.json 时那个子进程直接失败 —— 而
        ``/api/status`` 是上游的"你还活着吗"端点，抛出去会让连接被关掉、对方
        只看到一个空响应，于是"没装资源层"这件事在插件侧完全不可见。
        """
        return len(self._task_defs())

    # ---- 资源层自述 ----

    def _read_pack_info(self) -> dict:
        """资源层自报家门。任何情况下都不抛——它存在的意义就是报告异常状态。"""
        try:
            pack = packmeta.read_pack(self.dir)
        except Exception as exc:
            return {
                "present": False, "name": "", "version": "", "license": "", "github": "",
                "groups": [], "problems": [f"{type(exc).__name__}: {exc}"],
            }
        if pack is None:
            return {
                "present": False, "name": "", "version": "", "license": "", "github": "",
                "groups": [],
                "problems": [f"没有安装资源层（找不到 {self.dir / packmeta.INTERFACE_FILENAME}）"],
            }
        return {
            "present": True,
            "name": pack.name,
            "version": pack.version,
            "license": pack.license_ref,
            "github": pack.github,
            "groups": [str(g.get("name") or "") for g in pack.groups],
            "problems": list(pack.problems),
        }

    def pack_info(self, *, refresh: bool = False) -> dict:
        if self._pack_cache is None or refresh:
            self._pack_cache = self._read_pack_info()
        return self._pack_cache

    @staticmethod
    def _pack_problem_text(pack) -> str:
        if pack is None:
            return "没有安装资源层（缺少 interface.json）"
        return "资源层不完整：" + "；".join(pack.problems)

    def status(self) -> dict:
        res = self.resolution
        agents = []
        if self.runner is not None:
            for stem, client, proc in self.runner.agents:
                agents.append({"name": stem, "pid": proc.pid, "alive": bool(client.alive)})
            res = self.resolution or self.check_resolution()
        cur = None
        if self.current:
            cur = dict(self.current)
            cur["elapsed"] = round(time.time() - self.current["started"], 1)
        return {
            "state": self.state,  # idle | ready | busy
            "ready": self.runner is not None,
            "elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
            "hwnd": getattr(self.runner, "_hwnd", None) if self.runner else None,
            "focused": self.focus_ok() if self.runner else False,
            "minimized": self.is_minimized() if self.runner else False,
            "resource_nodes": len(self.runner.resource.node_list) if (self.runner and self.runner.resource) else 0,
            "resource_hash": self.runner.resource.hash if (self.runner and self.runner.resource) else "",
            "agents": agents,
            "resolution": res,
            "current": cur,
            "last_result": self.last_result,
            "error": self.error,
            "task_count": self._task_count(),
            # 哪一份资源层、叫什么、完整不完整。上层用它把"正在跑哪个游戏"讲对，
            # 换掉资源层之后文案不需要跟着改代码。
            "pack": self.pack_info(),
        }

    def dispose(self) -> None:
        if self.runner is not None:
            try:
                self.runner.dispose()
            except Exception:
                pass


# --------------------------------------------------------------------------
# HTTP 层
# --------------------------------------------------------------------------
PANEL: Panel | None = None
PANEL_HTML = HERE / "panel.html"


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "MaaEndPanel/1.0"

        def log_message(self, fmt, *args):  # 静默默认访问日志
            pass

        # ---- 鉴权 / 防护 ----

        def _deny(self, code: int, why: str) -> None:
            body = json.dumps({"error": why}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _guard(self, query: dict) -> bool:
            """Host/Origin 白名单 + token 校验，防 drive-by 请求。"""
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
            if host not in ("127.0.0.1", "localhost"):
                self._deny(403, "Host 不被允许")
                return False
            origin = self.headers.get("Origin")
            if origin:
                host_only = urlparse(origin).hostname or ""
                if host_only not in ("127.0.0.1", "localhost"):
                    self._deny(403, "Origin 不被允许")
                    return False
            token = self.headers.get("X-Auth-Token") or (query.get("token", [""])[0])
            if not token or token != PANEL.token:
                self._deny(401, "缺少或错误的 token")
                return False
            return True

        def _json(self, obj, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _query(self):
            return parse_qs(urlparse(self.path).query)

        # ---- 路由 ----

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            q = self._query()

            if path in ("/", "/index.html"):
                return self._serve_page()

            if path == "/api/ping":
                return self._json({"ok": True, "app": "maaend-panel"})

            if not self._guard(q):
                return None

            if path == "/api/status":
                return self._json(PANEL.status())
            if path == "/api/tasks":
                return self._json({"tasks": PANEL.tasks_payload()})
            if path == "/api/options":
                name = q.get("task", [""])[0]
                try:
                    return self._json(PANEL.options_payload(name))
                except Exception as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/screenshot":
                try:
                    png = PANEL.screenshot_png()
                except Exception as e:
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 503)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
                return None
            if path == "/api/logs":
                return self._stream_logs(q)
            return self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            q = self._query()
            if not self._guard(q):
                return None
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                payload = {}

            if path == "/api/run":
                try:
                    st = PANEL.start_task(
                        payload.get("task", ""),
                        payload.get("options") or {},
                        float(payload.get("timeout") or 300),
                    )
                    return self._json(st, 202)
                except Exception as e:
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            if path == "/api/stop":
                return self._json(PANEL.stop_task())
            if path == "/api/prepare":
                try:
                    PANEL.ensure_ready()
                    return self._json(PANEL.status())
                except Exception as e:
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            if path == "/api/check-resolution":
                return self._json(PANEL.check_resolution())
            return self._json({"error": "not found"}, 404)

        # ---- 页面 ----

        def _serve_page(self) -> None:
            if not PANEL_HTML.exists():
                return self._json(
                    {"error": "panel.html 不存在（前端由工作流提供，见 API_CONTRACT.md）"}, 404
                )
            html = PANEL_HTML.read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", PANEL.token)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---- SSE ----

        def _stream_logs(self, q: dict) -> None:
            since = int((q.get("since", ["0"])[0]) or 0)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            last = since
            deadline = time.time() + 3600
            try:
                while time.time() < deadline:
                    batch = PANEL.bus.wait(last, timeout=1.0)
                    if batch:
                        for ln in batch:
                            last = ln["id"]
                            self.wfile.write(
                                ("data: " + json.dumps(ln, ensure_ascii=False) + "\n\n").encode(
                                    "utf-8"
                                )
                            )
                        self.wfile.flush()
                    else:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return Handler


def main() -> int:
    global PANEL
    ap = argparse.ArgumentParser(description="MaaEnd 控制面板后端")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--maaend-dir", default=str(DEFAULT_MAAEND))
    ap.add_argument("--open", action="store_true", help="启动后用默认浏览器打开")
    ap.add_argument("--no-elevate-check", action="store_true", help="跳过管理员权限提示")
    args = ap.parse_args()

    if not args.no_elevate_check and not ctypes.windll.shell32.IsUserAnAdmin():
        print("警告：当前不是管理员权限。Seize 输入无法送达游戏，任务会失败。")
        print("请用 start_panel.bat 启动（它会请求提权）。\n")

    token = secrets.token_urlsafe(32)
    (HERE / "panel_token.txt").write_text(token, encoding="utf-8")

    PANEL = Panel(Path(args.maaend_dir), token)
    sys.stdout = Tee(PANEL.bus)
    atexit.register(PANEL.dispose)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler())
    url = f"http://127.0.0.1:{args.port}/"
    PANEL.bus.emit("=" * 56)
    PANEL.bus.emit(f"  控制面板后端已启动：{url}")
    PANEL.bus.emit(f"  token: {token}")
    PANEL.bus.emit(f"  token 文件: {HERE / 'panel_token.txt'}")
    PANEL.bus.emit("  仅监听 127.0.0.1；所有 /api/* 需带 X-Auth-Token")
    PANEL.bus.emit("=" * 56)
    print(f"面板地址: {url}", flush=True)
    print(f"token:    {token}", flush=True)

    if args.open:
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url + "?token=" + token)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
