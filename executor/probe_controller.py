"""探测：枚举窗口 -> 连接 Win32 控制器 -> 截图验证。

不加载资源、不跑任务，只验证「控制器这条线通不通」。
Seize 输入需要管理员权限；非管理员时自动退到 SendMessage 再试一次。
"""

import ctypes
import sys
import time
from pathlib import Path

from maa.controller import (
    Win32Controller,
    MaaWin32ScreencapMethodEnum,
    MaaWin32InputMethodEnum,
)
from maa.toolkit import Toolkit

OUT = Path(__file__).parent / "probe_screenshot.png"


def dump_enum(E, title):
    print(f"  {title}:")
    for n in dir(E):
        if n.startswith("_"):
            continue
        try:
            print(f"    {n} = {int(getattr(E, n))}")
        except Exception:
            pass


def main() -> int:
    print("=== 0. 权限 ===")
    admin = ctypes.windll.shell32.IsUserAnAdmin()
    print(f"  IsUserAnAdmin = {admin}  ({'管理员' if admin else '非管理员 ← Seize 可能失败'})")

    print()
    print("=== 1. 枚举窗口 ===")
    ws = Toolkit.find_desktop_windows()
    hits = [w for w in ws if w.class_name == "UnityWndClass" and w.window_name == "Endfield"]
    if not hits:
        print(f"  [FAIL] 未找到 Endfield 窗口（共 {len(ws)} 个窗口）")
        return 1
    w = hits[0]
    print(f"  hwnd={w.hwnd}  class={w.class_name}  name={w.window_name}")

    print()
    dump_enum(MaaWin32ScreencapMethodEnum, "2. 截图方式")
    dump_enum(MaaWin32InputMethodEnum, "3. 输入方式")

    for label, screencap, mouse, kbd in [
        ("Seize", MaaWin32ScreencapMethodEnum.ScreenDC,
         MaaWin32InputMethodEnum.Seize, MaaWin32InputMethodEnum.Seize),
        ("SendMessage(回退)", MaaWin32ScreencapMethodEnum.ScreenDC,
         MaaWin32InputMethodEnum.SendMessage, MaaWin32InputMethodEnum.SendMessage),
    ]:
        print()
        print(f"=== 4. 连接 [{label}] ===")
        try:
            c = Win32Controller(
                ctypes.c_void_p(w.hwnd),
                screencap_method=screencap,
                mouse_method=mouse,
                keyboard_method=kbd,
            )
        except Exception as e:
            print(f"  构造失败: {type(e).__name__}: {e}")
            continue

        t0 = time.time()
        job = c.post_connection()
        job.wait()
        dt = time.time() - t0
        print(f"  post_connection -> connected={c.connected}  耗时 {dt:.1f}s")

        if not c.connected:
            continue

        print(f"  uuid  = {c.uuid}")
        print(f"  分辨率 = {c.resolution if hasattr(c, 'resolution') else 'N/A'}")

        print("  截图 ...")
        img = c.post_screencap().wait().get()
        if img is None:
            print("  [FAIL] 截图为空")
            continue
        data = img if isinstance(img, (bytes, bytearray)) else bytes(img)
        OUT.write_bytes(data)
        print(f"  截图   -> {len(data):,} 字节，已存 {OUT.name}")
        print(f"\n[OK] 控制器连接 + 截图通过（方式={label}）")
        return 0

    print("\n[FAIL] 所有连接方式均失败（多半是权限不足，需要管理员运行）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
