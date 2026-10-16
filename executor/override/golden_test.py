#!/usr/bin/env python3
"""override 编译器的黄金测试（golden test）。

它现在守的是一条**移植线**：``pipeline_override.py``（纯 Python）与
``pipelineOverride.ts``（MXU 原版）必须产出相同的 override。

当初选"直接跑 TS"是为了不漂移；分发时 87 MB 的 Node 依赖不划算，于是改成
Python 移植。移植的风险由本测试兜住 —— 快照是 TS 版生成的，一旦两个实现
出现分歧就会在这里报出来，并打印节点级差异。

所以：

* ``--check``   跑 Python，与快照比对（**CI / 提交前跑这个**）
* ``--compare`` 同时跑 Node 与 Python 并互相比对（MXU 升级后跑）
* ``--update``  用 **Node（TS 实现）** 重新生成快照 —— 只有 TS 版有资格写快照

用法
----
    python golden_test.py --check
    python golden_test.py --compare
    python golden_test.py --update
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pipeline_override as P

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE / "golden"

MAAEND_DIR = "E:/MaaEnddm/MaaEnd-win-x86_64-v2.32.0"
CONTROLLER = "Win32-Front"
RESOURCE = "官服"

# (用例名, 任务名, 选项值)
CASES = [
    ("default_daily_rewards", "DailyRewards", {}),
    (
        "default_credit_shopping",
        "CreditShoppingN2",
        {},
    ),
    (
        "stockpile_min_buy",
        "AutoStockpile",
        {"AutoStockpileMinBuy": {"type": "switch", "value": True}},
    ),
    (
        "stockpile_server_time",
        "AutoStockpile",
        {
            "AutoStockpileMinBuy": {"type": "switch", "value": True},
            "AutoStockpileServerTime": {"type": "select", "caseName": "CN_UTC_PLUS_8"},
        },
    ),
    (
        "stockpile_elapsed_valley",
        "AutoStockpile",
        {"AutoStockpileElasticValleyIV": {"type": "switch", "value": True}},
    ),
    # checkbox：多选按 cases 定义顺序合并
    ("default_sell_product", "SellProduct", {}),
    (
        "sell_product_schedule",
        "SellProduct",
        {
            "SellProductSchedule": {
                "type": "checkbox",
                "caseNames": ["SellProductScheduleMonday", "SellProductScheduleFriday"],
            }
        },
    ),
    # input：int 与 string 两种 pipeline_type（MaaEnd 里没有 bool，
    # bool 分支由 test_pipeline_override.py 用合成 interface 覆盖）
    (
        "sell_product_min_price",
        "SellProduct",
        {
            "SellProductStockMinimumUnitPrice": {
                "type": "input",
                "values": {"SellProductStockMinimumUnitPrice": "42"},
            }
        },
    ),
    (
        "seize_delivery_string",
        "SeizeDeliveryJobs",
        {
            "SeizeDeliveryJobsReward": {"type": "input", "values": {"Reward": "30"}},
            "SeizeDeliveryJobsRefreshLimit": {
                "type": "input",
                "values": {"Limit": "250"},
            },
        },
    ),
    # 嵌套：switch 的 case 打开后才出现的子选项
    (
        "stockpile_enable_priority",
        "AutoStockpile",
        {
            "AutoStockpilePriorityRules": {"type": "switch", "value": True},
            "AutoStockpileServerTime": {"type": "select", "caseName": "CN_UTC_PLUS_8"},
        },
    ),
    # 空字符串的 input：验证 TS 的 `??` 语义（空串不回落默认值）
    (
        "seize_delivery_empty_string",
        "SeizeDeliveryJobs",
        {"SeizeDeliveryJobsReward": {"type": "input", "values": {"Reward": ""}}},
    ),
]

_interface_cache: dict | None = None


def interface() -> dict:
    """展开 import 后的 ProjectInterface。"""
    global _interface_cache
    if _interface_cache is None:
        from override_gen import load_interface

        _interface_cache = load_interface(MAAEND_DIR)
    return _interface_cache


def _canon(value) -> str:
    """规范化 JSON 文本，用于比对。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def build_python(task: str, options: dict) -> dict:
    """Python 实现的输出。"""
    arr = P.generate_task_pipeline_override(
        task,
        options,
        project_interface=interface(),
        controller_name=CONTROLLER,
        resource_name=RESOURCE,
    )
    return {"array_form": arr, "merged_form": (P.deep_merge_objects(*arr) if arr else {})}


def build_node(task: str, options: dict) -> dict:
    """MXU 原版 TS 的输出（需要 Node）。"""
    payload = {
        "taskName": task,
        "optionValues": options,
        "interface": interface(),
        "controllerName": CONTROLLER,
        "resourceName": RESOURCE,
        "globalOptionValues": {},
    }
    proc = subprocess.run(
        ["node", str(HERE / "cli.ts")],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"node cli.ts 失败:\n{proc.stderr[-800:]}")
    arr = json.loads(proc.stdout)
    return {"array_form": arr, "merged_form": (P.deep_merge_objects(*arr) if arr else {})}


def _diff_nodes(old: dict, new: dict) -> list:
    out = []
    for node in sorted(set(old) | set(new)):
        if _canon(old.get(node)) != _canon(new.get(node)):
            out.append(
                (
                    node,
                    json.dumps(old.get(node), ensure_ascii=False)[:220],
                    json.dumps(new.get(node), ensure_ascii=False)[:220],
                )
            )
    return out


def _report(tag_a: str, tag_b: str, a: dict, b: dict) -> None:
    for node, sa, sb in _diff_nodes(a, b):
        print(f"        节点 {node}")
        print(f"          {tag_a}: {sa}")
        print(f"          {tag_b}: {sb}")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true", help="用 Node（TS 实现）重生成快照")
    g.add_argument("--check", action="store_true", help="跑 Python，与快照比对")
    g.add_argument("--compare", action="store_true", help="Node 与 Python 互相比对")
    args = ap.parse_args()

    GOLDEN_DIR.mkdir(exist_ok=True)

    if args.update:
        for name, task, options in CASES:
            data = build_node(task, options)
            data.update({"case": name, "task": task, "options": options})
            (GOLDEN_DIR / f"{name}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  [写入] {name}.json  ({len(data['array_form'])} 个元素)")
        print(f"\n已用 Node 更新 {len(CASES)} 份快照 -> {GOLDEN_DIR}")
        return 0

    if args.compare:
        bad = []
        ran = 0
        for name, task, options in CASES:
            try:
                node_out = build_node(task, options)
            except Exception as exc:
                print(f"  [跳过] {name}: {exc}")
                continue
            ran += 1
            py_out = build_python(task, options)
            if _canon(node_out["array_form"]) == _canon(py_out["array_form"]):
                print(f"  [一致] {name}")
            else:
                bad.append(name)
                print(f"  [分歧] {name}")
                _report("TS", "Py", node_out["merged_form"], py_out["merged_form"])
        if bad:
            print(f"\n[FAIL] {len(bad)}/{ran} 个用例分歧: {bad}")
            return 1
        print(f"\n[OK] {ran}/{ran} 个用例 Node 与 Python 完全一致")
        return 0

    # --check
    bad = []
    missing = 0
    for name, task, options in CASES:
        p = GOLDEN_DIR / f"{name}.json"
        if not p.exists():
            print(f"  [缺快照] {name} —— 跑 --update 生成")
            missing += 1
            continue
        old = json.loads(p.read_text(encoding="utf-8"))
        new = build_python(task, options)
        if _canon(old["array_form"]) == _canon(new["array_form"]):
            print(f"  [一致] {name}")
            continue
        bad.append(name)
        print(f"  [漂移] {name}")
        _report(
            "快照(TS)", "本次(Py)", old.get("merged_form", {}), new["merged_form"]
        )

    if bad:
        print(f"\n[FAIL] {len(bad)} 个用例与 TS 快照不一致: {bad}")
        print("\nPython 实现与 MXU 原版产生了分歧。逐个节点确认后二选一：")
        print("  - 有意改动 → python golden_test.py --update（用 Node 重生成快照）")
        print("  - 移植 bug → 修 pipeline_override.py，勿更新快照")
        return 1
    if missing:
        print(f"\n[WARN] {missing} 个用例缺快照，未参与比对")
    print(f"\n[OK] {len(CASES) - missing}/{len(CASES)} 个用例与 TS 快照一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
