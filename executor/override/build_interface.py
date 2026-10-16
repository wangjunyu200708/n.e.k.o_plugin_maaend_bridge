#!/usr/bin/env python3
"""把 MaaEnd-2 的 assets/interface.json 展开成 MXU 前端所见的 ProjectInterface。

- 解析 JSONC（interface.json 带注释，普通 json.load 会失败）
- 展开 68 条 import：list 段追加、dict 段合并
- 输出合并后的 JSON，供 override/cli.ts 消费

用法:
    python build_interface.py [assets_dir] [-o out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_jsonc(path: Path) -> dict:
    """去掉 // 与 /* */ 注释、尾逗号后解析。字符串内的 // 不会被误删。"""
    s = path.read_text(encoding="utf-8")
    out: list[str] = []
    i, n = 0, len(s)
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
    text = "".join(out)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def merge_into(base: dict, sub: dict) -> None:
    for key, val in sub.items():
        cur = base.get(key)
        if isinstance(val, list) and isinstance(cur, list):
            cur.extend(val)
        elif isinstance(val, dict) and isinstance(cur, dict):
            cur.update(val)
        elif key not in base or cur is None:
            base[key] = val
        elif isinstance(val, list):
            base[key] = (cur if isinstance(cur, list) else [cur]) + val
        elif isinstance(val, dict):
            base[key] = {**(cur if isinstance(cur, dict) else {}), **val}


def build(assets_dir: Path) -> dict:
    iface = load_jsonc(assets_dir / "interface.json")
    imported = iface.pop("import", [])
    for rel in imported:
        f = assets_dir / rel
        if not f.exists():
            print(f"[warn] import 缺失: {rel}", file=sys.stderr)
            continue
        merge_into(iface, load_jsonc(f))
    return iface


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("assets", nargs="?", default="E:/MaaEnddm/MaaEnd-2/assets")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    iface = build(Path(args.assets))
    text = json.dumps(iface, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        tasks = iface.get("task", [])
        print(f"已写出 {args.out}  ({len(text)} B)")
        print(f"  task  : {len(tasks)}")
        print(f"  option: {len(iface.get('option', {}))}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
