#!/usr/bin/env python3
"""把 start_panel.bat 规范成 cmd 能正确解析的形式：纯 ASCII + CRLF。

cmd.exe 按 OEM 代码页（简体中文 = 936/GBK）解析 batch，
UTF-8 中文注释会被当成命令执行，报「'xxx' 不是内部或外部命令」。
`chcp 65001` 救不了 —— 解析器在执行到它之前已按原代码页读完整个文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

p = Path(__file__).resolve().parent / "start_panel.bat"
data = p.read_bytes()

bad = [i for i, b in enumerate(data) if b > 127]
if bad:
    print(f"发现 {len(bad)} 个非 ASCII 字节，请把注释改成英文")
    sys.exit(1)

data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
p.write_bytes(data)

check = p.read_bytes()
crlf = check.count(b"\r\n")
bare_lf = check.count(b"\n") - crlf
non_ascii = sum(1 for b in check if b > 127)
print(f"OK  {p.name}  {len(check)} B")
print(f"    CRLF 行数: {crlf}")
print(f"    裸 LF   : {bare_lf}")
print(f"    非 ASCII: {non_ascii}")
