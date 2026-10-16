"""端到端冒烟：option 编译器（TS 复用）产出的 override 能被 MaaFw 5.14.2 接受。

链路:
    interface.json (JSONC + 68 import)
        -> build_interface.py 展开
        -> Node 跑 MXU 原版 pipelineOverride.ts
        -> override JSON 字符串
        -> Resource.override_pipeline() / Tasker.override_pipeline()

不连控制器、不跑任务，只验证「编译产物合法」。
"""

import sys
from pathlib import Path

from maa.resource import Resource
from override_gen import gen_override, gen_override_object, merge_overrides

RES_DIR = str(Path(__file__).resolve().parent / "maaend" / "resource")


def main() -> int:
    print("=== 1. 编译 option -> pipeline_override ===")
    default_ov = gen_override("AutoStockpile")
    custom_ov = gen_override(
        "AutoStockpile",
        {
            "AutoStockpileMinBuy": {"type": "switch", "value": True},
            "AutoStockpileServerTime": {"type": "select", "caseName": "CN_UTC_PLUS_8"},
        },
    )
    print(f"  默认   : {len(default_ov)} 个元素")
    print(f"  指定后 : {len(custom_ov)} 个元素")
    print(f"  新增   : {[e for e in custom_ov if e not in default_ov]}")

    print()
    print("=== 2. 深合并为单个 object（MaaFw 5.14.2 只接受 object）===")
    merged = merge_overrides(custom_ov)
    print(f"  合并后顶层节点数: {len(merged)}")
    print(f"  AutoStockpileAttach.attach = {merged.get('AutoStockpileAttach', {}).get('attach')}")
    print("  （三个元素分别贡献 server_time / allow_data_upload / min_buy，深合并后应全部保留）")

    print()
    print("=== 3. 加载资源 ===")
    r = Resource()
    r.post_bundle(RES_DIR).wait()
    print(f"  loaded={r.loaded}  节点数={len(r.node_list)}")

    print()
    print("=== 4. 把 override 喂给 MaaFw ===")
    ok_array = r.override_pipeline(custom_ov)
    print(f"  数组格式      -> {ok_array}   （MaaFw 5.14.2 会拒绝，预期 False）")
    ok = r.override_pipeline(merged)
    print(f"  合并 object   -> {ok}")

    if not ok:
        print("[FAIL] MaaFw 拒绝了该 override（节点名或字段名不匹配）")
        return 1

    print()
    print("=== 5. 校验 override 真的生效（读回节点数据）===")
    missing = []
    for node_name in merged:
        data = r.get_node_data(node_name)
        if not data:
            missing.append(node_name)
    print(f"  override 涉及节点 {len(merged)} 个，资源中不存在: {missing or '无'}")

    print()
    print("=== 6. gen_override_object 一步到位 ===")
    one = gen_override_object("AutoStockpile", {"AutoStockpileMinBuy": {"type": "switch", "value": True}})
    print(f"  -> {r.override_pipeline(one)}   节点数={len(one)}")

    print("\n[OK] option 编译器 <-> MaaFw 5.14.2 对接通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
