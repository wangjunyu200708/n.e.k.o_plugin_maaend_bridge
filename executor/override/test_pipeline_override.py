#!/usr/bin/env python3
"""``pipeline_override`` 的单元测试 —— 覆盖黄金测试够不到的部分。

黄金测试跑的是 **MaaEnd 真实 interface** 里的组合，它能证明"移植没有漂移"，
但有两类东西它天然覆盖不到：

* MaaEnd 里**不存在**的选项形态（``pipeline_type: bool``、空字符串回落、
  组合键的修饰键拆分…）；
* TS -> Python 的**语言语义陷阱**（``??`` vs ``||``、``{}`` 的 truthy、
  ``$&`` 不是 Python 语法…）。这些地方出错的症状是"某个选项悄悄不生效"，
  不会报错。

所以这里用**合成 interface** 逐条钉死语义。合成数据手写，刻意短。

运行：python test_pipeline_override.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline_override as P  # noqa: E402
import pretask_builder as B  # noqa: E402

# ---------------------------------------------------------------------------
# 合成 interface：三种 pipeline_type + 嵌套 + hotkey
# ---------------------------------------------------------------------------

SYNTH: dict = {
    "controller": [
        {"name": "Win32-Front", "type": "Win32"},
        {"name": "WlRoots-Front", "type": "WlRoots", "wlroots": {"use_win32_vk_code": True}},
        {"name": "Plain-Linux", "type": "Linux"},
    ],
    "resource": [{"name": "官服"}],
    "global_option": ["GHot"],
    "task": [
        {
            "name": "Demo",
            "entry": "DemoMain",
            "option": [
                "OptBool",
                "OptInt",
                "OptString",
                "OptEmpty",
                "Nest",
                "HotKeyOpt",
                "EmptyTemplate",
            ],
            "pipeline_override": {"TaskOwnNode": {"enabled": True}},
        }
    ],
    "option": {
        "OptBool": {
            "type": "input",
            "label": "$opt.OptBool",
            "inputs": [
                {"name": "OptBool", "default": "true", "pipeline_type": "bool"},
            ],
            "pipeline_override": {"NodeBool": {"flag": "{OptBool}"}},
        },
        "OptInt": {
            "type": "input",
            "label": "$opt.OptInt",
            "inputs": [{"name": "OptInt", "default": "7", "pipeline_type": "int"}],
            "pipeline_override": {"NodeInt": {"count": "{OptInt}"}},
        },
        "OptString": {
            "type": "input",
            "label": "$opt.OptString",
            "inputs": [{"name": "OptString", "default": "d", "pipeline_type": "string"}],
            "pipeline_override": {"NodeStr": {"path": "{OptString}"}},
        },
        "OptEmpty": {
            "type": "input",
            "label": "$opt.OptEmpty",
            "inputs": [{"name": "OptEmpty", "default": "d", "pipeline_type": "string"}],
            "pipeline_override": {"NodeEmpty": {"v": "{OptEmpty}"}},
        },
        "Nest": {
            "type": "switch",
            "label": "$opt.Nest",
            "default_case": "No",
            "cases": [
                {
                    "name": "Yes",
                    "pipeline_override": {"NestYes": {"on": True}},
                    "option": ["NestedOnly"],
                },
                {"name": "No", "pipeline_override": {"NestNo": {"on": False}}},
            ],
        },
        "NestedOnly": {
            "type": "select",
            "label": "$opt.NestedOnly",
            "default_case": "A",
            "cases": [
                {"name": "A", "pipeline_override": {"NestedA": {"x": 1}}},
                {"name": "B", "pipeline_override": {"NestedB": {"x": 2}}},
            ],
        },
        "HotKeyOpt": {
            "type": "hotkey",
            "label": "$opt.HotKeyOpt",
            "hotkeys": [
                {"name": "Key", "default": "R"},
                # 带修饰键：primary=F, modifier1=CTRL
                {"name": "Mod", "default": "Ctrl+F"},
            ],
            "pipeline_override": {
                "KeyDown": {"key": "{Key.primary}"},
                "KeyMod1": {"key": "{Mod.modifier1}"},
                "KeyMod2": {"key": "{Mod.modifier2}"},
                "KeyPlain": {"key": "{Key}"},
            },
        },
        "GHot": {
            "type": "hotkey",
            "label": "$opt.GHot",
            "hotkeys": [{"name": "GKey", "default": "Q"}],
            "pipeline_override": {"GlobalKey": {"key": "{GKey.primary}"}},
        },
        # 空模板：TS 会 push 一个空对象，数组里必须有它
        "EmptyTemplate": {
            "type": "select",
            "label": "$opt.EmptyTemplate",
            "default_case": "A",
            "cases": [{"name": "A", "pipeline_override": {}}],
        },
    },
}

_fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def gen(task="Demo", values=None, ctrl="Win32-Front", res="官服"):
    return P.generate_task_pipeline_override(
        task, values or {}, project_interface=SYNTH, controller_name=ctrl, resource_name=res
    )


def merged(task="Demo", values=None, ctrl="Win32-Front", res="官服"):
    arr = gen(task, values, ctrl, res)
    return P.deep_merge_objects(*arr) if arr else {}


def main() -> int:
    print("=== 1. 任务自带 override 排在最前 ===")
    m = merged()
    check("TaskOwnNode 存在", "TaskOwnNode" in m)
    check(
        "顺序：任务自带是第一个元素",
        list(gen()[0].keys()) == ["TaskOwnNode"],
        f"-> {list(gen()[0].keys())}",
    )

    print("\n=== 2. bool / int / string 三种 pipeline_type ===")
    m = merged(values={"OptBool": {"type": "input", "values": {"OptBool": "yes"}}})
    check("bool 'yes' -> true", m.get("NodeBool", {}).get("flag") is True, f"-> {m.get('NodeBool')}")
    m = merged(values={"OptBool": {"type": "input", "values": {"OptBool": "0"}}})
    check("bool '0' -> false", m.get("NodeBool", {}).get("flag") is False, f"-> {m.get('NodeBool')}")
    m = merged()
    check("bool 默认 'true'", m.get("NodeBool", {}).get("flag") is True)
    m = merged(values={"OptInt": {"type": "input", "values": {"OptInt": "42"}}})
    check("int 42 -> 裸整数", m.get("NodeInt", {}).get("count") == 42, f"-> {m.get('NodeInt')}")
    m = merged(values={"OptString": {"type": "input", "values": {"OptString": "ab"}}})
    check("string ab", m.get("NodeStr", {}).get("path") == "ab")

    print("\n=== 3. `??` 与 `||` 的区别（TS 移植最容易错的一处）===")
    # TS: optionValue.values[name] ?? input.default ?? ''  —— 空串保留
    # TS: inputVal || '0'                             —— 空串变 '0'
    m = merged(values={"OptInt": {"type": "input", "values": {"OptInt": ""}}})
    check("int 空串 -> 0（走了 ||）", m.get("NodeInt", {}).get("count") == 0, f"-> {m.get('NodeInt')}")
    m = merged(values={"OptString": {"type": "input", "values": {"OptString": ""}}})
    check("string 空串 -> ''（不是默认值 d）", m.get("NodeStr", {}).get("path") == "", f"-> {m.get('NodeStr')}")

    print("\n=== 4. 字符串转义（Windows 路径的 \\ 不能破坏 JSON）===")
    m = merged(values={"OptString": {"type": "input", "values": {"OptString": "C:\\Users\\x"}}})
    check("反斜杠路径原样保留", m.get("NodeStr", {}).get("path") == "C:\\Users\\x", f"-> {m.get('NodeStr')}")
    m = merged(values={"OptString": {"type": "input", "values": {"OptString": 'say "hi"'}}})
    check("带引号的字符串", m.get("NodeStr", {}).get("path") == 'say "hi"')
    m = merged(values={"OptString": {"type": "input", "values": {"OptString": "中文路径"}}})
    check("非 ASCII 不转义", m.get("NodeStr", {}).get("path") == "中文路径")

    print("\n=== 5. 嵌套选项只在父项选中对应 case 时生效 ===")
    m = merged()
    check("switch 默认 No -> NestNo", "NestNo" in m and "NestedA" not in m)
    m = merged(values={"Nest": {"type": "switch", "value": True}})
    check("switch Yes -> NestYes", "NestYes" in m and "NestNo" not in m)
    check("Yes 的子选项生效", "NestedA" in m, f"-> NestedA={m.get('NestedA')}")
    m = merged(
        values={
            "Nest": {"type": "switch", "value": True},
            "NestedOnly": {"type": "select", "caseName": "B"},
        }
    )
    check("子选项选 B -> NestedB", "NestedB" in m and "NestedA" not in m)

    print("\n=== 6. 覆盖顺序：后者覆盖前者 ===")
    # task 自带 vs option —— task 自带先入数组，但深合并时后者（option）胜
    m = merged()
    check("深合并取最后一个", isinstance(m.get("TaskOwnNode"), dict))

    print("\n=== 7. hotkey 键码按控制器类型查表 ===")
    m = merged()
    # R = 0x52 = 82, CTRL = 0x11 = 17, Q = 0x51 = 81
    check("主键 R -> 82", m.get("KeyDown", {}).get("key") == 82, f"-> {m.get('KeyDown')}")
    check("modifier1 CTRL -> 17", m.get("KeyMod1", {}).get("key") == 17, f"-> {m.get('KeyMod1')}")
    check("modifier2 缺省 -> 0", m.get("KeyMod2", {}).get("key") == 0, f"-> {m.get('KeyMod2')}")
    check("基础占位符也是 82", m.get("KeyPlain", {}).get("key") == 82)
    check("global_option 的 Q -> 81", m.get("GlobalKey", {}).get("key") == 81, f"-> {m.get('GlobalKey')}")

    m = merged(values={"HotKeyOpt": {"type": "hotkey", "values": {"Key": "Ctrl+Shift+A"}}})
    check("组合键主键 A -> 65", m.get("KeyDown", {}).get("key") == 65, f"-> {m.get('KeyDown')}")
    check("组合键 modifier1 CTRL -> 17", m.get("KeyMod1", {}).get("key") == 17)

    print("\n=== 8. WlRoots 配 use_win32_vk_code 时用 Win32 码 ===")
    m = merged(ctrl="WlRoots-Front")
    check("WlRoots(use_win32_vk_code) -> 82", m.get("KeyDown", {}).get("key") == 82)
    m = merged(ctrl="Plain-Linux")
    check("纯 Linux -> evdev 码(R=19)", m.get("KeyDown", {}).get("key") == 19, f"-> {m.get('KeyDown')}")

    print("\n=== 9. 未知按键 -> 0（不是保留占位符）===")
    m = merged(values={"HotKeyOpt": {"type": "hotkey", "values": {"Key": "NOSUCHKEY"}}})
    check("未知键 -> 0", m.get("KeyDown", {}).get("key") == 0, f"-> {m.get('KeyDown')}")

    print("\n=== 10. JS truthy：空对象是 truthy ===")
    arr = gen()
    # 7 个 option 各贡献 1 个元素（OptBool/OptInt/OptString/OptEmpty/Nest/
    # HotKeyOpt/EmptyTemplate）+ 任务自带 1 个 + global_option 的 GHot 1 个 = 9
    # EmptyTemplate 的 case 模板是 {} —— TS 会 push({})，数组里必须有它
    check("数组长度 == 9", len(arr) == 9, f"-> {len(arr)}")
    check("空元素是 {}", arr[-1] == {}, f"-> {arr[-1]}")
    check("深合并后空对象不贡献字段", "TaskOwnNode" in P.deep_merge_objects(*arr))

    print("\n=== 11. deep_merge_objects ===")
    merged_out = P.deep_merge_objects(
        {"a": {"x": 1, "y": 2}}, {"a": {"y": 3, "z": 4}, "b": 1}
    )
    check("嵌套深合并", merged_out == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1}, f"-> {merged_out}")
    merged_out = P.deep_merge_objects({"a": [1, 2]}, {"a": [3]})
    check("数组后者覆盖（不合并）", merged_out == {"a": [3]}, f"-> {merged_out}")

    print("\n=== 12. 兼容性过滤：controller/resource 白名单 ===")
    add = {
        "controller": [],
        "resource": [],
        "task": [{"name": "Demo", "entry": "e", "option": ["OnlyWin32"], "pipeline_override": {}}],
        "option": {
            **SYNTH["option"],
            "OnlyWin32": {
                "type": "select",
                "default_case": "A",
                "controller": ["Win32-Front"],
                "resource": ["官服"],
                "cases": [{"name": "A", "pipeline_override": {"OnlyWin32Node": {"x": 1}}}],
            },
        },
    }
    arr = P.generate_task_pipeline_override(
        "Demo", {}, project_interface=add, controller_name="Win32-Front", resource_name="官服"
    )
    check("匹配 controller 时保留", any("OnlyWin32Node" in e for e in arr))
    arr = P.generate_task_pipeline_override(
        "Demo", {}, project_interface=add, controller_name="Win32-Background", resource_name="官服"
    )
    check("不匹配 controller 时跳过", not any("OnlyWin32Node" in e for e in arr))
    # 白名单存在但当前没给 resource 名 -> TS 的 isOptionIncompatible 同样判不兼容
    arr = P.generate_task_pipeline_override(
        "Demo", {}, project_interface=add, controller_name="Win32-Front", resource_name=None
    )
    check(
        "有 resource 白名单但未指定资源 -> 跳过",
        not any("OnlyWin32Node" in e for e in arr),
    )
    arr = P.generate_task_pipeline_override(
        "Demo", {}, project_interface=add, controller_name=None, resource_name="官服"
    )
    check("未指定 controller -> 跳过", not any("OnlyWin32Node" in e for e in arr))

    print("\n=== 13. 未知任务返回空数组 ===")
    check("未知任务 -> []", gen(task="NoSuchTask") == [])

    print("\n=== 14. sanitize：类型变了就丢弃 ===")
    v = P.sanitize_option_value("OptInt", {"type": "switch", "value": True}, SYNTH["option"])
    check("类型不匹配 -> None", v is None)
    v = P.sanitize_option_value("NestedOnly", {"type": "select", "caseName": "ZZZ"}, SYNTH["option"])
    check("case 不存在 -> None", v is None)
    v = P.sanitize_option_value("NestedOnly", {"type": "select", "caseName": "A"}, SYNTH["option"])
    check("case 存在 -> 原样返回", v == {"type": "select", "caseName": "A"})

    print("\n=== 15. pretask 参数构造 ===")
    pi = {
        "pretask": {
            "exec": "agent/go-service",
            "args": ["--pretask", "Demo"],
            "name": "DemoPretask",
            "option": ["OptInt", "OptString", "Nest"],
        },
        "option": SYNTH["option"],
    }
    r = B.gen_pretask_args("DemoPretask", {}, pi, "Win32-Front", "官服")
    check("exec", r["exec"] == "agent/go-service")
    check("args 前两段固定", r["args"][:2] == ["--pretask", "Demo"], f"-> {r['args'][:2]}")
    import json as _json

    payload = _json.loads(r["args"][2])
    # input 类型在 pretask 里的形状是 {子项名: 文本}（对齐 TS pretasks.ts:142）
    check("input 取默认值", payload["OptInt"] == {"OptInt": "7"}, f"-> {payload['OptInt']}")
    check("string 取默认值", payload["OptString"] == {"OptString": "d"}, f"-> {payload['OptString']}")
    check("select 归约为字符串", payload["Nest"] == "No", f"-> {payload['Nest']}")
    r2 = B.gen_pretask_args(
        "DemoPretask", {"OptInt": {"type": "input", "values": {"OptInt": "99"}}}, pi
    )
    payload2 = _json.loads(r2["args"][2])
    check("指定值生效", payload2["OptInt"] == {"OptInt": "99"}, f"-> {payload2['OptInt']}")
    # 嵌套：Nest 打开后子选项也应进 payload
    r3 = B.gen_pretask_args("DemoPretask", {"Nest": {"type": "switch", "value": True}}, pi)
    payload3 = _json.loads(r3["args"][2])
    check("递归进嵌套选项", payload3.get("NestedOnly") == "A", f"-> {payload3.get('NestedOnly')}")
    try:
        B.gen_pretask_args("NoSuch", {}, pi)
        check("未知 pretask 报错", False)
    except RuntimeError:
        check("未知 pretask 报错", True)
    pi_no_opt = {"pretask": {"exec": "x", "name": "N", "args": ["-a"]}, "option": {}}
    r4 = B.gen_pretask_args("N", {}, pi_no_opt)
    check("无 option 声明时不追加 JSON 参数", r4["args"] == ["-a"], f"-> {r4['args']}")

    print()
    if _fails:
        print(f"[FAIL] {len(_fails)} 项未通过: {_fails}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
