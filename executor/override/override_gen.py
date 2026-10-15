#!/usr/bin/env python3
"""pipeline_override 生成器 —— 直接复用 MXU 前端的 TypeScript 实现，不做 Python 复刻。

原理
----
MXU 前端的 `utils/pipelineOverride.ts` 除了 logger 之外是纯逻辑（零 DOM / 零 React /
零 Tauri），Node 24 原生支持 TypeScript 类型剥离，可以直接 `node cli.ts` 运行。
本模块只是它的 Python 外壳：展开 interface.json 的 import → 喂给 Node → 拿回 JSON。

好处：MXU 升级时只需重新拷贝那 4 个 .ts 文件，逻辑永远和官方前端一致。

用法
----
    from override_gen import gen_override

    # MaaFw 5.14.2 只接受 object，用 gen_override_object 直接拿合并后的格式
    ov = gen_override_object("AutoStockpile", {
        "AutoStockpileMinBuy": {"type": "switch", "value": True},
        "AutoStockpileServerTime": {"type": "select", "caseName": "CN_UTC_PLUS_8"},
    })
    tasker.post_task("AutoStockpileMain", ov)

    # gen_override 返回数组（与 MXU 前端输出一致），需要时自行 merge_overrides()

命令行
------
    python override_gen.py AutoStockpile
    python override_gen.py AutoStockpile --set AutoStockpileMinBuy=yes
    python override_gen.py --list-tasks
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ASSETS = HERE.parent / "maaend"
DEFAULT_CONTROLLER = "Win32-Front"
DEFAULT_RESOURCE = "官服"

NODE_CANDIDATES = [
    "C:/Program Files/nodejs/node.exe",
    shutil.which("node") or "",
]

_interface_cache: dict | None = None
_interface_cache_path: str | None = None


def _node() -> str:
    for c in NODE_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise RuntimeError("找不到 node.exe（需要 Node 22.6+ / 24 以原生运行 TypeScript）")


def load_interface(assets_dir: Path | str = DEFAULT_ASSETS, force: bool = False) -> dict:
    """展开 interface.json 的 68 条 import，得到 MXU 前端所见的 ProjectInterface。"""
    global _interface_cache, _interface_cache_path
    key = str(assets_dir)
    if _interface_cache is not None and _interface_cache_path == key and not force:
        return _interface_cache

    out = HERE / "interface.merged.json"
    subprocess.run(
        [sys.executable, str(HERE / "build_interface.py"), str(assets_dir), "-o", str(out)],
        check=True,
        capture_output=True,
    )
    _interface_cache = json.loads(out.read_text(encoding="utf-8"))
    _interface_cache_path = key
    return _interface_cache


def gen_override(
    task_name: str,
    option_values: dict | None = None,
    *,
    controller_name: str = DEFAULT_CONTROLLER,
    resource_name: str = DEFAULT_RESOURCE,
    global_option_values: dict | None = None,
    assets_dir: Path | str = DEFAULT_ASSETS,
) -> list[dict]:
    """生成某任务的 pipeline_override（list[dict]，可直接传给 Tasker）。"""
    payload = {
        "taskName": task_name,
        "optionValues": option_values or {},
        "interface": load_interface(assets_dir),
        "controllerName": controller_name,
        "resourceName": resource_name,
        "globalOptionValues": global_option_values or {},
    }
    proc = subprocess.run(
        [_node(), str(HERE / "cli.ts")],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"override 生成失败:\n{proc.stderr}")
    return json.loads(proc.stdout)


def merge_overrides(overrides: list[dict]) -> dict:
    """把数组格式的 override 深合并为单个 object。

    为什么必须合并
    --------------
    MaaFw 5.14.2 的 `Resource.override_pipeline` / `Tasker.post_task` 只接受
    **JSON object**，传数组会报 `json is not object` 并返回 False。
    MXU 前端产出的是数组（多元素），Rust 绑定内部会做合并；Python 绑定没有这层，
    所以对接时必须自己合并。

    为什么是深合并而不是浅覆盖
    --------------------------
    MaaFw 对同一节点的同名字段是完整替换。例如三个数组元素都改
    `AutoStockpileAttach.attach` 的不同键，浅合并只留下最后一个，其余丢失。
    这里采用与 MXU `deepMergeObjects` 完全相同的语义：嵌套 dict 递归合并，
    其余（含数组）后者覆盖前者。

    实测：合并后 `AutoStockpileAttach.attach` =
    `{"server_time": 8, "allow_data_upload": true, "min_buy": true}`，三个键全部保留。
    """
    result: dict = {}
    for src in overrides:
        for k, v in src.items():
            if (
                isinstance(v, dict)
                and not isinstance(v, list)
                and isinstance(result.get(k), dict)
                and not isinstance(result[k], list)
            ):
                result[k] = merge_overrides([result[k], v])
            else:
                result[k] = v
    return result


def gen_override_object(*args, **kwargs) -> dict:
    """同 gen_override，但返回深合并后的单个 dict —— 直接喂给 MaaFw 5.14.2 的格式。"""
    return merge_overrides(gen_override(*args, **kwargs))


def _coerce(value: str, opt_def: dict) -> dict:
    """把命令行的 `key=yes` / `key=Yes` / `key=3` 转成 OptionValue。"""
    t = opt_def.get("type")
    if t == "switch":
        return {"type": "switch", "value": value.lower() in ("1", "true", "yes", "y", "on")}
    if t == "select":
        return {"type": "select", "caseName": value}
    if t == "input":
        return {"type": "input", "values": {"value": value}}
    raise SystemExit(f"暂不支持命令行设置 {t} 类型选项（{opt_def.get('type')}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 MaaEnd 任务的 pipeline_override")
    ap.add_argument("task", nargs="?", help="任务名")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL")
    ap.add_argument("--controller", default=DEFAULT_CONTROLLER)
    ap.add_argument("--resource", default=DEFAULT_RESOURCE)
    ap.add_argument("--list-tasks", action="store_true")
    ap.add_argument(
        "--merged",
        action="store_true",
        help="输出深合并后的单个 object（MaaFw 5.14.2 唯一接受的格式）",
    )
    args = ap.parse_args()

    iface = load_interface()

    if args.list_tasks or not args.task:
        for t in iface.get("task", []):
            print(f"{t['name']:<28} entry={t.get('entry')}")
        return 0

    opts = iface.get("option", {})
    values: dict[str, dict] = {}
    for item in args.set:
        k, _, v = item.partition("=")
        if k not in opts:
            raise SystemExit(f"未知选项: {k}")
        values[k] = _coerce(v, opts[k])

    ov = gen_override(args.task, values, controller_name=args.controller, resource_name=args.resource)
    if args.merged:
        ov = merge_overrides(ov)
    print(json.dumps(ov, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
