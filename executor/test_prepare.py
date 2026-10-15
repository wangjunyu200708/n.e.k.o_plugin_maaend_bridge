#!/usr/bin/env python3
"""真机验证：初始化 MaaFw 环境 + 分辨率校验 + 截图。

必须先启动 panel_server.py。此脚本会真正加载资源、拉起 agent、连游戏窗口。

注意：非管理员权限下连接能成功，但 Seize 输入无效 —— 这里只验证环境，
不发送任何键鼠输入。
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def call(base: str, token: str, path: str, method: str = "GET", body: dict | None = None,
         timeout: float = 120.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"X-Auth-Token": token, "Content-Type": "application/json"},
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=12702)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    token = (HERE / "panel_token.txt").read_text(encoding="utf-8").strip()

    print("=== 1. POST /api/prepare（真实初始化，约 3~5 秒）===")
    t0 = time.time()
    st, raw = call(base, token, "/api/prepare", "POST", {})
    print(f"  HTTP {st}  耗时 {time.time() - t0:.1f}s")
    s = json.loads(raw)
    if st != 200:
        print(f"  [FAIL] {s.get('error')}")
        return 1
    print(f"  state      = {s['state']}")
    print(f"  elevated   = {s['elevated']}")
    print(f"  hwnd       = {s['hwnd']}")
    print(f"  nodes      = {s['resource_nodes']}")
    print(f"  hash       = {s['resource_hash']}")
    for a in s["agents"]:
        print(f"  agent      = {a['name']:<12} pid={a['pid']} alive={a['alive']}")
    res = s["resolution"] or {}
    print(f"  resolution = {res.get('w')}x{res.get('h')} ratio={res.get('ratio')} ok={res.get('ok')}")

    if not s["elevated"]:
        print("\n  [注意] 当前非管理员：Seize 输入无效，真跑任务会失败。请用 start_panel.bat 启动。")

    print("\n=== 2. POST /api/check-resolution（重新检测）===")
    st, raw = call(base, token, "/api/check-resolution", "POST", {})
    res = json.loads(raw)
    print(f"  {res.get('w')}x{res.get('h')} ratio={res.get('ratio')} ok={res.get('ok')}")
    if not res.get("ok"):
        print(f"  message: {res.get('message')}")

    print("\n=== 3. 分辨率拦截验证 ===")
    st, raw = call(base, token, "/api/run", "POST",
                   {"task": "DailyRewards", "timeout": 60, "options": {}})
    r = json.loads(raw)
    if res.get("ok"):
        print(f"  分辨率通过，任务已受理 (HTTP {st}) -> 记得去停止它：POST /api/stop")
        call(base, token, "/api/stop", "POST", {})
        time.sleep(2)
    else:
        print(f"  HTTP {st} -> {r.get('error')}")
        print("  [OK] 非 16:9 时在 API 层就拒绝了，不会静默失败")

    print("\n=== 4. GET /api/screenshot ===")
    st, raw = call(base, token, "/api/screenshot")
    if st == 200 and raw[:4] == b"\x89PNG":
        p = HERE / "panel_shot.png"
        p.write_bytes(raw)
        w = int.from_bytes(raw[16:20], "big")
        h = int.from_bytes(raw[20:24], "big")
        print(f"  [OK] {len(raw)} 字节  {w}x{h} -> {p}")
    else:
        print(f"  [FAIL] HTTP {st}: {raw[:200]}")

    print("\n=== 5. 未知任务应被拒绝 ===")
    st, raw = call(base, token, "/api/run", "POST",
                   {"task": "NoSuchTask", "timeout": 10, "options": {}})
    print(f"  HTTP {st} -> {json.loads(raw).get('error')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
