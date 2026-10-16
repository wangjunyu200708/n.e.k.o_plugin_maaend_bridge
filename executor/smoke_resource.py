"""MaaFw 5.14.2 冒烟测试：加载 MaaEnd 资源包（不连控制器、不跑任务）。

用法:
    python smoke_resource.py [resource_dir]

默认资源目录为 MaaEnd 发行版自带的 resource/。
"""

import sys
import time
from pathlib import Path

from maa.resource import Resource

DEFAULT_RES = str(Path(__file__).resolve().parent / "maaend" / "resource")


def main() -> int:
    res_dir = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RES)
    if not res_dir.is_dir():
        print(f"[FAIL] 资源目录不存在: {res_dir}")
        return 1

    print(f"[1] 资源目录 : {res_dir}")
    sub = sorted(p.name for p in res_dir.iterdir() if p.is_dir())
    print(f"[2] 子目录   : {sub}")

    r = Resource()
    t0 = time.time()
    job = r.post_bundle(str(res_dir))
    print(f"[3] post_bundle 已提交 (job={job})")

    ok = job.wait()
    dt = time.time() - t0
    print(f"[4] wait()   -> {ok}   耗时 {dt:.1f}s")
    print(f"[5] loaded   -> {r.loaded}")

    if not ok or not r.loaded:
        print("[FAIL] 资源加载失败")
        return 1

    nodes = r.node_list
    print(f"[6] 节点数   -> {len(nodes)}")
    if nodes:
        print(f"[7] 前 5 个节点: {nodes[:5]}")

    print(f"[8] hash     -> {r.hash}")
    print("\n[OK] MaaFw 5.14.2 + MaaEnd 资源层 冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
