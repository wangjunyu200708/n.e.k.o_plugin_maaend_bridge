#!/usr/bin/env python3
"""pipeline_override / pretask 参数生成器（对外 API 稳定，实现已换）。

历史
----
1. 最初**直接跑 MXU 前端的 TypeScript**（``node cli.ts``），逻辑永远和官方一致，
   但分发时要连带 87 MB 的 Node 运行时 —— 终端用户还得装对版本
   （Node 22.6+ 才支持原生类型剥离）。
2. 现在改为**纯 Python 移植**（``pipeline_override.py`` / ``pretask_builder.py``），
   运行时零外部依赖。
3. 等价性由两层网兜住：
   - ``golden_test.py --check`` —— 与 TS 版生成的冻结快照逐节点比对
   - ``test_pipeline_override.py`` —— 合成 interface 钉死语义边界

Node 现在只在两处用到，都不是运行时路径：``dump_data.ts``（从 MXU 源码导出
数据表）和 ``golden_test.py --compare/--update``（生成/校验对照组）。

用法
----
    from override_gen import gen_override_object

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
import subprocess
import sys
from pathlib import Path

import pipeline_override as _po
import pretask_builder as _pb

HERE = Path(__file__).resolve().parent
DEFAULT_ASSETS = Path("E:/MaaEnddm/MaaEnd-2/assets")
DEFAULT_CONTROLLER = "Win32-Front"
DEFAULT_RESOURCE = "官服"

_interface_cache: dict | None = None
_interface_cache_path: str | None = None


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
    """生成某任务的 pipeline_override（list[dict]，与 MXU 前端输出同形）。"""
    return _po.generate_task_pipeline_override(
        task_name,
        option_values or {},
        project_interface=load_interface(assets_dir),
        controller_name=controller_name,
        resource_name=resource_name,
        global_option_values=global_option_values or {},
    )


def gen_pretask_args(
    pretask_name: str,
    option_values: dict | None = None,
    *,
    controller_name: str = DEFAULT_CONTROLLER,
    resource_name: str = DEFAULT_RESOURCE,
    assets_dir: Path | str = DEFAULT_ASSETS,
) -> dict:
    """生成某个 pretask 的完整启动参数。

    pretask 与普通任务的本质区别
    ----------------------------
    - **不进 Tasker 队列**，没有 entry，也没有 pipeline
    - 是**直接启动外部程序**（MaaEnd 的 pretask = ``agent/go-service --pretask GameSetting``）
    - 参数 = interface.json 里的固定 ``args`` + **序列化后的 option JSON**
      （单行紧凑 JSON，作为最后一个参数追加）

    返回 ``{"name": ..., "exec": ..., "args": [...]}``；exec 可能是相对路径
    （如 ``agent/go-service``），调用方需按 MaaEnd 根目录解析成绝对路径。
    """
    return _pb.gen_pretask_args(
        pretask_name,
        option_values or {},
        load_interface(assets_dir),
        controller_name,
        resource_name,
    )


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
    return _po.deep_merge_objects(*overrides) if overrides else {}


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
