"""完整链路验证：资源 + 控制器 + agent（go-service / cpp-algo）。

到这一步为止所有组件都就位，只差 post_task。
对齐 MaaPiCli Runner.cpp 的启动顺序：
    Resource -> Controller -> Tasker.bind -> 每个 agent: AgentClient.bind -> 子进程 -> connect -> register_sink
"""

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

MAAEND_DIR = Path(__file__).resolve().parent / "maaend"

import packmeta  # noqa: E402  同目录：窗口身份从资源层读，换资源层不必改这里
LOG_DIR = Path(__file__).parent / "agent_logs"

from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402
from maa.controller import (  # noqa: E402
    Win32Controller,
    MaaWin32ScreencapMethodEnum,
    MaaWin32InputMethodEnum,
)
from maa.agent_client import AgentClient  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402
import maa.library as _maalib  # noqa: E402


def load_jsonc(p: Path) -> dict:
    """interface.json 是 JSONC（带注释/尾逗号），不能直接 json.load。"""
    import json
    import re

    s = p.read_text(encoding="utf-8")
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            out.append(c)
            i += 1
            while i < n:
                if s[i] == "\\":
                    out.append(s[i : i + 2])
                    i += 2
                    continue
                if s[i] == '"':
                    break
                out.append(s[i])
                i += 1
            out.append('"')
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    t = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(t)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print("=== 1. 加载资源 ===")
    res = Resource()
    res.post_bundle(str(MAAEND_DIR / "resource")).wait()
    print(f"  loaded={res.loaded}  节点数={len(res.node_list)}")
    if not res.loaded:
        return 1

    print()
    print("=== 2. 连接控制器 ===")
    pack = packmeta.read_pack(MAAEND_DIR)
    if pack is None or pack.win32 is None or not pack.win32.describes_a_window:
        print(f"  [FAIL] 资源层没有声明可用的 Win32 窗口：{MAAEND_DIR}")
        return 1
    spec = pack.win32
    ws = Toolkit.find_desktop_windows()
    hits = [w for w in ws if w.class_name == spec.class_regex and w.window_name == spec.window_regex]
    if not hits:
        print(f"  [FAIL] 未找到 {spec.class_regex}/{spec.window_regex} 窗口")
        return 1
    w = hits[0]
    ctrl = Win32Controller(
        ctypes.c_void_p(w.hwnd),
        screencap_method=MaaWin32ScreencapMethodEnum.ScreenDC,
        mouse_method=MaaWin32InputMethodEnum.Seize,
        keyboard_method=MaaWin32InputMethodEnum.Seize,
    )
    ctrl.post_connection().wait()
    print(f"  hwnd={w.hwnd}  connected={ctrl.connected}")
    if not ctrl.connected:
        return 1

    print()
    print("=== 3. Tasker 绑定 ===")
    tasker = Tasker()
    tasker.bind(res, ctrl)
    print(f"  inited={tasker.inited}")

    print()
    print("=== 4. 启动 agent ===")
    interface = load_jsonc(MAAEND_DIR / "interface.json")
    agent_list = interface.get("agent", [])
    print(f"  interface 声明 {len(agent_list)} 个 agent: {[a.get('child_exec') for a in agent_list]}")

    maafw_bin = Path(_maalib.__file__).parent / "bin"
    clients: list[tuple[str, AgentClient, subprocess.Popen, object]] = []

    try:
        for i, adef in enumerate(agent_list):
            child_exec = adef.get("child_exec", "")
            child_args = list(adef.get("child_args", []))
            exe = MAAEND_DIR / child_exec
            if not exe.exists() and os.name == "nt":
                exe = exe.with_suffix(".exe")
            if not exe.exists():
                print(f"  [FAIL] agent[{i}] 不存在: {exe}")
                return 1
            stem = exe.stem

            client = AgentClient()
            ident = client.identifier
            ok_bind = client.bind(res)
            print(f"  [Agent[{i}]] {stem}  identifier={ident}  bind={ok_bind}")
            if not ok_bind:
                return 1

            cmd = [str(exe), *child_args, ident]
            env = os.environ.copy()
            env["PATH"] = str(maafw_bin) + os.pathsep + env.get("PATH", "")
            fout = open(LOG_DIR / f"{stem}.stdout.log", "w", encoding="utf-8")
            ferr = open(LOG_DIR / f"{stem}.stderr.log", "w", encoding="utf-8")

            t0 = time.time()
            proc = subprocess.Popen(
                cmd,
                cwd=str(MAAEND_DIR),
                env=env,
                stdout=fout,
                stderr=ferr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(0.8)
            if proc.poll() is not None:
                msg = (LOG_DIR / f"{stem}.stderr.log").read_text(encoding="utf-8", errors="replace")
                print(f"  [FAIL] {stem} 立即退出 code={proc.returncode}\n{msg[:800]}")
                return 1
            print(f"  [Agent[{i}]] PID={proc.pid}")

            ok_conn = client.connect()
            dt = time.time() - t0
            print(f"  [Agent[{i}]] connect={ok_conn}  alive={client.alive}  耗时 {dt:.1f}s")
            if not ok_conn:
                return 1

            client.register_sink(res, ctrl, tasker)
            print(f"  [Agent[{i}]] sink 注册完成")

            clients.append((stem, client, proc, (fout, ferr)))

        print(f"\n[OK] agent 全部就绪（{len(clients)} 个）")
        print(f"  自定义识别: {res.custom_recognition_list}")
        print(f"  自定义动作: {res.custom_action_list}")

    finally:
        print()
        print("=== 5. 清理 ===")
        for stem, client, proc, _fh in clients:
            try:
                client.disconnect()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            print(f"  {stem} 已停止")

    return 0


if __name__ == "__main__":
    sys.exit(main())
