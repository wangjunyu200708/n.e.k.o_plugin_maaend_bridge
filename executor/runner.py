#!/usr/bin/env python3
"""MaaEnd 自建执行器 —— 不依赖 MXU 前端。

Python 3.11 + MaaFw 5.14.2 直连 MaaFramework，资源层用 MaaEnd 发行版自带的
interface.json / resource / agent。

完整链路（对齐 MaaPiCli Runner.cpp 的顺序）:
    1. Resource.post_bundle(resource/)
    2. Toolkit.find_desktop_windows() 找 UnityWndClass/Endfield -> Win32Controller(ScreenDC + Seize)
    3. Tasker.bind(resource, controller)
    4. 每个 agent: AgentClient.bind(resource) -> subprocess(child_exec, identifier) -> connect -> register_sink
    5. gen_override_object(任务, 选项) -> tasker.post_task(entry, override)

用法:
    python runner.py --list-tasks
    python runner.py PullCountCalculator
    python runner.py AutoStockpile --set AutoStockpileMinBuy=yes
    python runner.py DailyRewards --dry-run         # 只准备不执行
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import maa.library as _maalib
import packmeta  # 同目录：资源层自描述（窗口 / 控制器 / 项目身份）
from maa.agent_client import AgentClient
from maa.controller import (
    MaaWin32InputMethodEnum,
    MaaWin32ScreencapMethodEnum,
    Win32Controller,
)
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from override_gen import gen_override_object, load_interface

# 资源层随插件分发（executor/maaend/）。仍可被 --maaend-dir 与插件配置覆盖。
MAAEND_DIR = Path(__file__).resolve().parent / "maaend"
OVERRIDE_DIR = Path(__file__).parent / "override"
LOG_DIR = Path(__file__).parent / "run_logs"

# 窗口身份与控制器方法曾经写死在这里（UnityWndClass / Endfield / ScreenDC / Seize）。
# 它们本来就在资源层的 interface.json 的 controller[].win32 里，写死就意味着
# 「换掉这个游戏的资源层」必须改代码。现在从 packmeta 读，缺声明就明确报错。
#
# 这里原本还有一份 load_jsonc，定义了从未被调用过（真正的解析在
# override/build_interface.py 里，它展开 import 时才用）。已删除；本文件现在
# 需要读 interface.json 时走 packmeta.load_jsonc。


class Runner:
    def __init__(self, maaend_dir: Path, log: bool = True):
        self.dir = maaend_dir
        self.log_enabled = log
        # 用展开 import 后的版本：发行版 interface.json 顶层 task 为空，
        # 46 个任务全在 tasks/*.json 里通过 import 引入
        self.interface = load_interface(str(self.dir))
        # 资源层自己声明要什么窗口、用什么控制器方法（controller[].win32）。
        # 这条路不展开 import，也不碰 Node —— 它是状态路径，不是任务路径。
        self.pack = packmeta.read_pack(Path(self.dir))
        self.resource: Resource | None = None
        self.controller: Win32Controller | None = None
        self.tasker: Tasker | None = None
        self.agents: list[tuple[str, AgentClient, subprocess.Popen]] = []
        self._handles: list[object] = []

    def log(self, msg: str) -> None:
        if self.log_enabled:
            print(msg, flush=True)

    # ---------- 各阶段 ----------

    def _win32_spec(self) -> packmeta.Win32Controller:
        """资源层声明的窗口/控制器信息。

        缺失就明确失败，**不猜**。以前这里是硬编码的 UnityWndClass/Endfield，
        那意味着换一份资源层（另一个游戏）必须改代码；而如果改成"读不到就用
        默认值"，换层之后执行器会去附着一个不存在或错误的窗口，且什么都不报 ——
        正是这次要消掉的那种静默错误。
        """
        pack = self.pack
        if pack is None:
            raise RuntimeError(
                f"资源层目录里没有 {packmeta.INTERFACE_FILENAME}：{self.dir}"
            )
        if pack.win32 is None or not pack.win32.describes_a_window:
            raise RuntimeError(
                f"资源层没有声明可用的 Win32 窗口"
                f"（{packmeta.INTERFACE_FILENAME} 的 controller[].win32 "
                f"缺 class_regex / window_regex）：{self.dir}"
            )
        return pack.win32

    def find_window(self):
        win32 = self._win32_spec()
        hits = [
            w
            for w in Toolkit.find_desktop_windows()
            if w.class_name == win32.class_regex and w.window_name == win32.window_regex
        ]
        return hits[0] if hits else None

    def setup_resource(self) -> None:
        self.log("[1/5] 加载资源 ...")
        r = Resource()
        r.post_bundle(str(self.dir / "resource")).wait()
        self.resource = r
        self.log(f"      loaded={r.loaded}  节点数={len(r.node_list)}  hash={r.hash}")
        if not r.loaded:
            raise RuntimeError("资源加载失败")

    @staticmethod
    def _bring_to_front(hwnd) -> bool:
        """把游戏窗口提到前台。

        Seize 输入走 SendInput，只会投递给**当前前台窗口**。
        如果游戏窗口被遮挡/失焦，点击会全部发给别的窗口 —— 表现为
        「日志里 Click 一大堆，游戏界面毫无反应」。
        """
        u32 = ctypes.windll.user32
        h = ctypes.c_void_p(hwnd)
        VK_MENU = 0x12

        def fg_ok() -> bool:
            return u32.GetForegroundWindow() == hwnd

        try:
            if u32.IsIconic(h):
                u32.ShowWindow(h, 9)  # SW_RESTORE
            if fg_ok():
                return True

            # 招式 1：AttachThreadInput 借用前台线程的输入队列
            tid_fg = u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), None)
            tid_cur = u32.GetCurrentThreadId()
            attached = False
            if tid_fg and tid_fg != tid_cur:
                attached = bool(u32.AttachThreadInput(tid_fg, tid_cur, True))
            u32.SetForegroundWindow(h)
            u32.BringWindowToTop(h)
            if attached:
                u32.AttachThreadInput(tid_fg, tid_cur, False)
            time.sleep(0.3)
            if fg_ok():
                return True

            # 招式 2：模拟按下 Alt —— Windows 的经典前台锁定绕过（Alt 会临时放开 SetForegroundWindow 限制）
            u32.keybd_event(VK_MENU, 0, 0, 0)
            u32.keybd_event(VK_MENU, 0, 2, 0)  # KEYEVENTF_KEYUP
            u32.SetForegroundWindow(h)
            time.sleep(0.3)
            if fg_ok():
                return True

            # 招式 3：SwitchToThisWindow（未文档化，但对顽固窗口常有效）
            try:
                u32.SwitchToThisWindow(h, True)
                time.sleep(0.3)
            except Exception:
                pass
            if fg_ok():
                return True

            # 招式 4：最小化再还原（会触发一次真正的激活）
            u32.ShowWindow(h, 6)  # SW_MINIMIZE
            time.sleep(0.2)
            u32.ShowWindow(h, 9)  # SW_RESTORE
            time.sleep(0.5)
            return fg_ok()
        except Exception:
            return False

    def setup_controller(self) -> None:
        self.log("[2/5] 查找窗口 ...")
        win32 = self._win32_spec()
        w = self.find_window()
        if w is None:
            raise RuntimeError(
                f"未找到 {win32.class_regex}/{win32.window_regex} 窗口，请先启动游戏"
            )
        self.log(f"      hwnd={w.hwnd}")
        self._hwnd = w.hwnd
        front = self._bring_to_front(w.hwnd)
        self.log(f"      窗口已置前={front}  （False 表示点击可能投递给别的窗口）")

        def pick(enum_cls, declared: str, fallback):
            """资源层声明的方法名 → MaaFw 枚举成员。

            不认识的名称用兜底并**记一笔**：静默兜底会让人以为按声明跑上了。
            也不用 ``getattr(...) or fallback`` —— 枚举成员可能是 0，会被误判成缺失。
            """
            member = getattr(enum_cls, declared, None)
            if member is None:
                self.log(f"      [warn] 资源层声明了未知的 {declared}，改用 {fallback.name}")
                return fallback
            return member

        self.log(f"      控制器 {win32.name}: {win32.screencap}/{win32.mouse}+{win32.keyboard}")
        c = Win32Controller(
            ctypes.c_void_p(w.hwnd),
            screencap_method=pick(
                MaaWin32ScreencapMethodEnum, win32.screencap, MaaWin32ScreencapMethodEnum.ScreenDC
            ),
            mouse_method=pick(
                MaaWin32InputMethodEnum, win32.mouse, MaaWin32InputMethodEnum.Seize
            ),
            keyboard_method=pick(
                MaaWin32InputMethodEnum, win32.keyboard, MaaWin32InputMethodEnum.Seize
            ),
        )
        self._bring_to_front(w.hwnd)
        c.post_connection().wait()
        self.controller = c
        self.log(f"      connected={c.connected}")
        if not c.connected:
            raise RuntimeError("控制器连接失败（Seize 输入需要管理员权限）")

    def setup_tasker(self) -> None:
        self.log("[3/5] 绑定 Tasker ...")
        t = Tasker()
        t.bind(self.resource, self.controller)
        self.tasker = t
        self.log(f"      inited={t.inited}")

    def setup_agents(self) -> None:
        self.log("[4/5] 启动 agent ...")
        agent_list = self.interface.get("agent", [])
        if not agent_list:
            self.log("      interface.json 未声明 agent，CustomAction 将不可用")
            return

        maafw_bin = Path(_maalib.__file__).parent / "bin"
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        for i, adef in enumerate(agent_list):
            exe = self.dir / adef.get("child_exec", "")
            if not exe.exists() and os.name == "nt":
                exe = exe.with_suffix(".exe")
            if not exe.exists():
                raise RuntimeError(f"agent[{i}] 不存在: {exe}")

            client = AgentClient()
            ident = client.identifier
            if not client.bind(self.resource):
                raise RuntimeError(f"agent[{i}] bind(resource) 失败")

            env = os.environ.copy()
            env["PATH"] = str(maafw_bin) + os.pathsep + env.get("PATH", "")
            fout = open(LOG_DIR / f"{exe.stem}.stdout.log", "w", encoding="utf-8")
            ferr = open(LOG_DIR / f"{exe.stem}.stderr.log", "w", encoding="utf-8")
            self._handles += [fout, ferr]

            t0 = time.time()
            proc = subprocess.Popen(
                [str(exe), *adef.get("child_args", []), ident],
                cwd=str(self.dir),
                env=env,
                stdout=fout,
                stderr=ferr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(0.8)
            if proc.poll() is not None:
                err = (LOG_DIR / f"{exe.stem}.stderr.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                raise RuntimeError(f"{exe.stem} 立即退出 code={proc.returncode}\n{err[:600]}")

            if not client.connect():
                raise RuntimeError(f"{exe.stem} 未能在超时内连接")
            client.register_sink(self.resource, self.controller, self.tasker)
            self.agents.append((exe.stem, client, proc))
            self.log(
                f"      {exe.stem:<12} PID={proc.pid}  alive={client.alive}  "
                f"耗时 {time.time() - t0:.1f}s"
            )

        self.log(
            f"      自定义识别 {len(self.resource.custom_recognition_list)} 个 / "
            f"自定义动作 {len(self.resource.custom_action_list)} 个"
        )

    # ---------- 任务 ----------

    def run_task(self, task_name: str, option_values: dict, timeout: float | None) -> int:
        task_def = next(
            (t for t in self.interface.get("task", []) if t.get("name") == task_name), None
        )
        if task_def is None:
            names = [t.get("name") for t in self.interface.get("task", [])]
            raise SystemExit(f"未知任务 {task_name}\n可用: {', '.join(names)}")

        entry = task_def.get("entry")
        self.log("[5/5] 编译选项 -> pipeline_override ...")
        override = gen_override_object(
            task_name, option_values, assets_dir=str(self.dir)
        )
        self.log(f"      entry={entry}  override 覆盖 {len(override)} 个节点")

        # 发任务之前再抢一次前台：资源加载 + agent 启动花了好几秒，
        # 期间用户很可能已经切走窗口
        if getattr(self, "_hwnd", None):
            front = self._bring_to_front(self._hwnd)
            self.log(f"      任务前置前={front}")

        self.log("      启动任务 ...")
        try:
            self.tasker.set_log_dir(str(LOG_DIR / "maa"))
            self.tasker.set_stdout_level(LoggingLevelEnum.Debug)
            self.tasker.set_save_draw(True)
            self.tasker.set_debug_mode(True)
        except Exception as e:
            self.log(f"      (日志设置失败: {type(e).__name__}: {e})")

        job = self.tasker.post_task(entry, override)
        t0 = time.time()

        while self.tasker.running:
            time.sleep(1.0)
            el = time.time() - t0
            if timeout and el > timeout:
                self.log(f"      超时 {timeout}s，停止任务")
                self.tasker.post_stop().wait()
                break
            if int(el) % 10 == 0:
                self.log(f"      ... 运行中 {int(el)}s")

        dt = time.time() - t0
        self.log(f"      任务结束，耗时 {dt:.1f}s")
        self.log(f"      job_id={job.job_id}  status={job.status}  succeeded={job.succeeded}  failed={job.failed}")
        try:
            detail = self.tasker.get_task_detail(job.job_id)
            if detail:
                st = getattr(detail, "status", None)
                self.log(f"      task_detail: status={st}")
        except Exception as e:
            self.log(f"      (无法读取 task_detail: {type(e).__name__}: {e})")
        try:
            latest = self.tasker.get_latest_node(entry)
            if latest:
                self.log(f"      latest_node: {latest}")
        except Exception as e:
            self.log(f"      (get_latest_node: {type(e).__name__})")
        return 0

    def dispose(self) -> None:
        if self.tasker is not None:
            try:
                self.tasker.post_stop().wait()
            except Exception:
                pass
        for stem, client, proc in self.agents:
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
            self.log(f"      {stem} 已停止")
        self.agents.clear()
        for h in self._handles:
            try:
                h.close()
            except Exception:
                pass
        self._handles.clear()


def parse_bool(v: str) -> bool:
    return v.lower() in ("1", "true", "yes", "y", "on")


def main() -> int:
    ap = argparse.ArgumentParser(description="MaaEnd 自建执行器")
    ap.add_argument("task", nargs="?", help="任务名")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL")
    ap.add_argument("--list-tasks", action="store_true")
    ap.add_argument("--maaend-dir", default=str(MAAEND_DIR))
    ap.add_argument("--dry-run", action="store_true", help="只准备环境，不跑任务")
    ap.add_argument("--timeout", type=float, default=None, help="任务超时（秒）")
    args = ap.parse_args()

    maaend = Path(args.maaend_dir)
    runner = Runner(maaend)

    if args.list_tasks:
        tasks = runner.interface.get("task", [])
        print(f"=== {maaend.name} 可用任务 ({len(tasks)}) ===")
        for t in tasks:
            print(f"  {t.get('name', ''):<32} entry={t.get('entry', '')}")
        return 0

    if not args.task and not args.dry_run:
        ap.print_help()
        print("\n提示: 用 --list-tasks 查看可用任务")
        return 0

    # 解析 --set（用展开后的 interface 拿选项类型）
    option_defs = load_interface(str(maaend)).get("option", {})
    values: dict[str, dict] = {}
    for item in args.set:
        k, _, v = item.partition("=")
        od = option_defs.get(k)
        if od is None:
            raise SystemExit(f"未知选项: {k}")
        t = od.get("type")
        if t == "switch":
            values[k] = {"type": "switch", "value": parse_bool(v)}
        elif t == "select":
            values[k] = {"type": "select", "caseName": v}
        elif t == "input":
            values[k] = {"type": "input", "values": {"value": v}}
        else:
            raise SystemExit(f"不支持命令行设置 {t} 类型选项: {k}")

    try:
        runner.setup_resource()
        runner.setup_controller()
        runner.setup_tasker()
        runner.setup_agents()
        if args.dry_run:
            print("\n[OK] 环境就绪（--dry-run，未执行任务）")
            return 0
        return runner.run_task(args.task, values, args.timeout)
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        return 1
    finally:
        print("\n=== 清理 ===")
        runner.dispose()


if __name__ == "__main__":
    sys.exit(main())
