#!/usr/bin/env python3
"""override 编译器的黄金测试（golden test）。

为什么需要
----------
option → pipeline_override 的逻辑**直接复用 MXU 前端的 TypeScript**
（`override/pipelineOverride.ts` 等 4 个文件），好处是永远跟得上上游，
坏处是上游一改，这里就可能悄悄漂移 —— 而且漂移不会报错，只会表现为
「任务跑出来的行为不对」，极难排查。

对策：把已知良好的输出**冻结成快照**，任何改动前后跑一遍 `--check`，
一旦 diff 就说明行为变了，必须人工确认是修 bug 还是被上游带偏。

另外在 `override/SOURCE.txt` 里锁定了这 4 个文件来自哪个 MXU 版本，
升级 MXU 时需要重新拷贝 + 重新生成快照。

用法
----
    python golden_test.py --update     # 重新生成快照（改完逻辑后跑）
    python golden_test.py --check      # 对比当前输出与快照（CI / 提交前跑）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE / "golden"

sys.path.insert(0, str(HERE))

from override_gen import gen_override, merge_overrides  # noqa: E402

MAAEND_DIR = str(Path(__file__).resolve().parent.parent / "maaend")

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
]


def build(name: str, task: str, options: dict) -> dict:
    arr = gen_override(task, options, assets_dir=MAAEND_DIR)
    merged = merge_overrides(arr)
    return {
        "case": name,
        "task": task,
        "options": options,
        # 数组形式（MXU 前端原样输出）
        "array_form": arr,
        # 合并后形式（MaaFw 5.14.2 Python 绑定唯一接受的形式）
        "merged_form": merged,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()

    GOLDEN_DIR.mkdir(exist_ok=True)

    if args.update:
        for name, task, options in CASES:
            data = build(name, task, options)
            (GOLDEN_DIR / f"{name}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  [写入] {name}.json  ({len(data['array_form'])} 个元素)")
        print(f"\n已更新 {len(CASES)} 份快照 -> {GOLDEN_DIR}")
        return 0

    # --check
    bad = []
    for name, task, options in CASES:
        p = GOLDEN_DIR / f"{name}.json"
        if not p.exists():
            bad.append((name, "快照缺失，请先跑 --update"))
            continue
        old = json.loads(p.read_text(encoding="utf-8"))
        new = build(name, task, options)
        if json.dumps(old["array_form"], sort_keys=True, ensure_ascii=False) == json.dumps(
            new["array_form"], sort_keys=True, ensure_ascii=False
        ):
            print(f"  [一致] {name}")
            continue
        bad.append((name, "输出与快照不一致"))
        # 打印差异位置
        old_m, new_m = old.get("merged_form", {}), new["merged_form"]
        for node in sorted(set(old_m) | set(new_m)):
            if json.dumps(old_m.get(node), sort_keys=True, ensure_ascii=False) != json.dumps(
                new_m.get(node), sort_keys=True, ensure_ascii=False
            ):
                print(f"        节点 {node}:")
                print(f"          旧: {json.dumps(old_m.get(node), ensure_ascii=False)[:200]}")
                print(f"          新: {json.dumps(new_m.get(node), ensure_ascii=False)[:200]}")

    if bad:
        print(f"\n[FAIL] {len(bad)}/{len(CASES)} 个用例漂移：")
        for n, why in bad:
            print(f"  - {n}: {why}")
        print("\n确认是有意改动后，执行：python golden_test.py --update")
        return 1
    print(f"\n[OK] {len(CASES)}/{len(CASES)} 个用例与快照一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
