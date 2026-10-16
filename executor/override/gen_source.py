#!/usr/bin/env python3
"""生成 override/SOURCE.txt —— 记录 TS 源的版本、哈希与升级流程。

MXU 上游升级后重跑本脚本，能立刻看出哪些文件变了。

注意分工：**这些 .ts 文件不参与运行时**。它们只在两处用到 ——
``dump_data.ts`` 导出数据表（``data.json``），以及 ``golden_test.py
--compare/--update`` 生成/校验对照组。运行时是 ``pipeline_override.py`` /
``pretask_builder.py`` 两个纯 Python 实现。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

SRC = Path(r"E:/MaaEnddm/MXU-2.7.1/MXU-2.7.1")
OUT = Path(__file__).resolve().parent / "SOURCE.txt"

FILES = [
    ("src/utils/pipelineOverride.ts", "升级工具 → dump_data.ts"),
    ("src/utils/optionHelpers.ts", "升级工具"),
    ("src/types/specialTasks.ts", "升级工具 → dump_data.ts"),
    ("src/stores/helpers.ts", "升级工具"),
    ("src/types/pretasks.ts", "升级工具（pretask 参数构造的参考实现）"),
    ("src/utils/logger.ts", "已弃用（Python 版不需要）"),
]

STEPS = """## 运行时依赖什么

**只需要 Python。** `pipeline_override.py` + `pretask_builder.py` + `data.json`
三者即全部运行时依赖，没有任何外部进程。

本目录下的 `.ts` 文件**不参与运行时**，它们只在维护时用到。

## MXU 升级流程

1. 重新拷贝上表文件到本目录
2. 改写 import：`@/` -> 相对路径；`import './x'` -> `import './x.ts'`
3. 给 `pipelineOverride.ts` 的 `HOTKEY_KEY_MAP` 保持 `export`（dump_data.ts 要读）
4. `node dump_data.ts > data.json`          重新导出数据表
5. `python golden_test.py --compare`       Node 与 Python 逐节点比对
6. 逐个核对差异 → 修 `pipeline_override.py`，或接受上游变更
7. `python golden_test.py --update`        用 TS 版重生成快照
8. `python gen_source.py`                  更新本文件的哈希
"""


def main() -> None:
    pkg = json.loads((SRC / "package.json").read_text(encoding="utf-8"))
    # package.json 的 version 常是 0.1.0 占位，真实版本看外层发行目录名（MXU-2.7.1）
    m = [p for p in SRC.parents if p.name.upper().startswith("MXU-")]
    version = m[0].name if m else pkg.get("version", "?")
    lines = [
        "# MXU 源文件清单与运行时依赖",
        "",
        "## 结论：运行时零外部依赖",
        "",
        "override 编译器已从「Node 跑 TS」移植为纯 Python。运行只需要",
        "`pipeline_override.py` + `pretask_builder.py` + `data.json`。",
        "",
        "分发时**不需要**携带 Node（87 MB），终端用户也不用装任何东西。",
        "",
        "下表的 `.ts` 仅在维护 MXU 升级时使用：",
        "`dump_data.ts` 从它们导出 `data.json`；`golden_test.py --compare/--update`",
        "用 TS 实现当对照组。",
        "",
        f"- 来源仓库: `{SRC}`",
        f"- MXU 版本: `{version}`（package.json 为占位值 `{pkg.get('version')}`）",
        f"- 记录时间: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "| 文件 | 用途 | 行数 | sha256(前16位) |",
        "|---|---|---|---|",
    ]
    for rel, note in FILES:
        p = SRC / rel
        if not p.exists():
            lines.append(f"| `{rel}` | {note} | - | **缺失** |")
            continue
        data = p.read_bytes()
        h = hashlib.sha256(data).hexdigest()[:16]
        lines.append(f"| `{rel}` | {note} | {data.count(10)} | `{h}` |")

    lines += ["", STEPS]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {OUT}")


if __name__ == "__main__":
    main()
