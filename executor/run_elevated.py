#!/usr/bin/env python3
"""以管理员权限运行 runner.py，并把输出落到日志文件后回读。

Seize 输入方式需要管理员权限；普通权限下 Win32Controller 能连上（connected=True），
但点击/按键会静默失效 —— 表现为「日志里节点在流转，游戏界面毫无反应」。
本脚本用 ShellExecuteW(runas) 提权重启 runner.py，用户只需在 UAC 弹窗点「是」。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "runner.py"
LOG = HERE / "run_elevated.log"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "DailyRewards"
    timeout = sys.argv[2] if len(sys.argv) > 2 else "180"
    extra = sys.argv[3:]

    args = " ".join(f'"{a}"' for a in extra)

    if is_admin():
        print("[elevated] 已是管理员，直接运行")
        cmd = [
            sys.executable,
            str(RUNNER),
            task,
            "--timeout",
            timeout,
            *extra,
        ]
        return subprocess.run(cmd, cwd=str(HERE)).returncode

    if LOG.exists():
        LOG.unlink()

    cmdline = (
        f'/c ""{sys.executable}" "{RUNNER}" {task} --timeout {timeout} {args} '
        f'> "{LOG}" 2>&1"'
    )
    print(f"[elevated] 请求管理员权限运行: {task}")
    print("[elevated] 请在 UAC 弹窗点「是」...")

    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "cmd.exe", cmdline, str(HERE), 1
    )
    if rc <= 32:
        print(f"[FAIL] ShellExecuteW 失败 rc={rc}（UAC 被取消或拒绝）")
        return 1

    print("[elevated] 已提权启动，等待完成 ...")
    deadline = time.time() + int(timeout) + 90
    last_size, stable = -1, 0

    while time.time() < deadline:
        time.sleep(2)
        if not LOG.exists():
            continue
        size = LOG.stat().st_size
        if size == last_size:
            stable += 1
            if stable >= 3:  # 6 秒无新增 => 认为结束
                break
        else:
            stable = 0
            last_size = size

    if not LOG.exists():
        print("[FAIL] 未产生日志，进程可能未启动")
        return 1

    print()
    print("=" * 60)
    print(f"日志: {LOG}")
    print("=" * 60)
    text = LOG.read_text(encoding="utf-8", errors="replace")
    # 过滤 MaaFw 的 DBG/VRB/TRC 噪音和 agent 清理刷屏
    keep = []
    for line in text.splitlines():
        if "[DBG]" in line or "[VRB]" in line or "[TRC]" in line:
            continue
        if "clear_custom_registration" in line or "remove_sink" in line:
            continue
        keep.append(line[:220])
    print("\n".join(keep[-60:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
