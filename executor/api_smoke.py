#!/usr/bin/env python3
"""panel_server.py 的 API 冒烟测试（不触发 MaaFw 环境初始化）。

覆盖：鉴权、Host/Origin 防护、任务列表、选项树、SSE 日志流。

用法:
    python panel_server.py --port 12702     # 另开一个终端
    python api_smoke.py --port 12702
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def req(url: str, token: str | None = None, headers: dict | None = None, timeout: float = 10.0):
    h = dict(headers or {})
    if token:
        h["X-Auth-Token"] = token
    r = urllib.request.Request(url, headers=h)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return -1, str(e).encode(), {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=12702)
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    token_file = HERE / "panel_token.txt"
    if not token_file.exists():
        print("[FAIL] panel_token.txt 不存在，后端没启动？")
        return 1
    token = token_file.read_text(encoding="utf-8").strip()

    fails = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")
        if not cond:
            fails.append(name)

    print("=== 1. 基础连通 ===")
    st, body, _ = req(f"{base}/api/ping")
    check("ping 无需 token", st == 200, f"-> {st}")

    print("\n=== 2. 鉴权 ===")
    st, body, _ = req(f"{base}/api/status")
    check("无 token -> 401", st == 401, f"-> {st}")
    st, body, _ = req(f"{base}/api/status", token="wrong-token")
    check("错误 token -> 401", st == 401, f"-> {st}")
    st, body, _ = req(f"{base}/api/status", token=token)
    check("正确 token -> 200", st == 200, f"-> {st}")

    print("\n=== 3. Origin 防护（防 drive-by 请求）===")
    st, _, _ = req(
        f"{base}/api/status", token=token, headers={"Origin": "https://evil.example"}
    )
    check("外部 Origin -> 403", st == 403, f"-> {st}")
    st, _, _ = req(
        f"{base}/api/status", token=token, headers={"Origin": f"http://127.0.0.1:{args.port}"}
    )
    check("本机 Origin -> 200", st == 200, f"-> {st}")

    print("\n=== 4. 状态 ===")
    status = json.loads(req(f"{base}/api/status", token=token)[1])
    print(
        f"      state={status['state']}  elevated={status['elevated']}  "
        f"tasks={status['task_count']}  resolution={status['resolution']}"
    )
    check("返回 task_count", status["task_count"] > 0)
    check("字段完整", all(k in status for k in ("state", "agents", "resolution", "current")))

    print("\n=== 5. 任务列表 ===")
    st, body, _ = req(f"{base}/api/tasks", token=token)
    tasks = json.loads(body)["tasks"]
    check("任务数 > 40", len(tasks) > 40, f"-> {len(tasks)}")
    sample = next((t for t in tasks if t["name"] == "CreditShoppingN2"), None)
    check("含 CreditShoppingN2", sample is not None)
    if sample:
        print(f"      label={sample['label']}  entry={sample['entry']}  options={len(sample['options'])}")
        check("label 已本地化（非 $ 开头）", not sample["label"].startswith("$"))

    print("\n=== 6. 选项树 ===")
    st, body, _ = req(f"{base}/api/options?task=CreditShoppingN2", token=token)
    opt = json.loads(body)
    tree = opt["tree"]
    check("选项树非空", len(tree) > 0, f"-> {len(tree)} 项")
    types = {n["type"] for n in tree}
    print(f"      类型: {sorted(types)}")
    check("类型合法", types <= {"switch", "select", "input", "checkbox", "hotkey"})
    nested = sum(1 for n in tree for c in n.get("cases", []) if c.get("option"))
    print(f"      含嵌套子选项的 case: {nested}")
    if tree:
        check("label 已本地化", not tree[0]["label"].startswith("$"), f"-> {tree[0]['label']}")

    print("\n=== 7. SSE 日志流 ===")
    try:
        r = urllib.request.Request(
            f"{base}/api/logs?since=0", headers={"X-Auth-Token": token}
        )
        resp = urllib.request.urlopen(r, timeout=8)
        got = 0
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data: "):
                got += 1
                if got == 1:
                    obj = json.loads(line[6:])
                    print(f"      首条: id={obj['id']} src={obj['src']} {obj['text'][:50]}")
            if got >= 3:
                break
        resp.close()
        check("SSE 收到日志", got >= 1, f"-> {got} 条")
    except Exception as e:
        check("SSE 收到日志", False, f"-> {type(e).__name__}: {e}")

    print("\n=== 8. 404 ===")
    st, _, _ = req(f"{base}/api/nope", token=token)
    check("未知路径 -> 404", st == 404, f"-> {st}")

    print()
    if fails:
        print(f"[FAIL] {len(fails)} 项未通过: {fails}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
