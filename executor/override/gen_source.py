#!/usr/bin/env python3
"""生成 override/SOURCE.txt —— 锁定复用的 MXU 源文件版本与哈希。

MXU 上游升级后重跑本脚本，能立刻看出哪些文件变了。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

SRC = Path(r"E:/MaaEnddm/MXU-2.7.1/MXU-2.7.1")
OUT = Path(__file__).resolve().parent / "SOURCE.txt"

FILES = [
    ("src/utils/pipelineOverride.ts", "原样复用"),
    ("src/utils/optionHelpers.ts", "原样复用"),
    ("src/types/specialTasks.ts", "原样复用（仅 import type）"),
    ("src/stores/helpers.ts", "原样复用（仅 import type）"),
    ("src/utils/logger.ts", "替换为 stub（依赖 loglevel + Tauri paths）"),
]

STEPS = """## 升级 MXU 时的流程

1. 重新拷贝前 4 个文件到本目录（`logger.ts` 继续用 stub）
2. 重跑 sed 改写：`@/` 别名 -> 相对路径；`import './x'` -> `import './x.ts'`（ESM 必须带扩展名）
3. `python golden_test.py --update` 重新生成快照
4. **人工 review 差异** —— 这就是上游带来的行为变化，不是回归
5. 重跑 `python gen_source.py` 更新本文件的哈希
"""


def main() -> None:
    pkg = json.loads((SRC / "package.json").read_text(encoding="utf-8"))
    # package.json 的 version 常是 0.1.0 占位，真实版本看外层发行目录名（MXU-2.7.1）
    m = [p for p in SRC.parents if p.name.upper().startswith("MXU-")]
    version = m[0].name if m else pkg.get("version", "?")
    lines = [
        "# 复用自 MXU 前端的文件清单",
        "",
        "本目录下的 TypeScript 文件**不是本项目写的**，是从 MXU 前端原样复制的。",
        "option -> pipeline_override 的编译逻辑以此保证与官方前端一致。",
        "",
        f"- 来源仓库: `{SRC}`",
        f"- MXU 版本: `{version}`（package.json 为占位值 `{pkg.get('version')}`）",
        f"- 记录时间: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "| 文件 | 处理方式 | 行数 | sha256(前16位) |",
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
