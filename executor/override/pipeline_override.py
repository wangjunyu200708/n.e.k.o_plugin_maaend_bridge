"""option -> pipeline_override 编译器（纯 Python 移植）。

移植自 MXU 前端的 TypeScript 实现，逐函数对齐，行为以
``golden_test.py --check`` 为准（输出与 TS 版冻结快照逐字节对比）。

为什么现在没有 Node 了
----------------------
早期版本直接 ``node cli.ts`` 跑原版 TS。分发时这要求终端用户的机器装有
Node 22.6+，且装对了版本 —— 一个 87 MB 的运行时依赖换取一个可以移植的编译
器，不划算。所以改成 Python 实现，Node 只在**升级 MXU 时**用来导出数据表
（``dump_data.ts``）和跑黄金测试的对照组。

数据来源
--------
``data.json`` 由 ``dump_data.ts`` 从 ``pipelineOverride.ts`` /
``specialTasks.ts`` 原样导出（hotkey 键码表约 280 行、MXU 特殊任务表约
550 行，全是字面量 —— 手抄必然出错且无法察觉）。

TS -> Python 的三处语义陷阱（移植时踩过，勿简化）
--------------------------------------------------
1. ``??`` 与 ``||`` 不同。``optionValue.values[name] ?? inputDef.default ?? ''``
   里的 ``??`` 只在 null/undefined 时兜底，**空字符串会原样保留**；而后面
   ``inputVal || '0'`` 是 falsy 检查，空字符串会被替换成 ``'0'``。Python 的
   ``or`` 一律是 falsy 检查，用在这里会让用户清空输入框后拿到默认值而不是空串。
   所以下面一律显式写 ``is None`` 判断。

2. **必须保留"先序列化、再替换、再解析"的字符串往返**。TS 把选项值填进
   模板时是先 ``JSON.stringify`` 整个 pipeline_override，再做字符串替换，
   最后 ``JSON.parse`` 回来；解析失败就**丢弃这一条 override**。如果改成
   直接操作 dict，一个形如 ``"path/{X}/img.png"`` 的占位符在 TS 里会产出
   非法 JSON 转义（``\\U`` 不是合法转义）而被丢弃，在 Python 里却会被正确
   转义 —— 两者行为不同。TS 的行为是「用户填了非法路径就这条不生效」，
   保留它。

3. **JS 的 JSON 转义规则与 Python 不同**。``JSON.stringify("中文")`` 输出原
   字符，``json.dumps`` 默认 ``ensure_ascii=True`` 会变成 ``\\u4e2d\\u6587``。
   凡是复刻 TS 的字符串转义，都必须显式 ``ensure_ascii=False``。

替换一律用**函数式** ``re.sub(lambda m: value)``：JS 的替换串里 ``$&`` 有特殊
含义、Python 的 ``\\g<1>`` 也有，而用户的输入可能同时含有这两种符号。函数式
替换两边都绕开了解析。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).resolve().parent
_DATA_PATH = _HERE / "data.json"

_data_cache: Optional[dict] = None


def _data() -> dict:
    """导出的数据表（hotkey 键码表 + MXU 特殊任务表）。"""
    global _data_cache
    if _data_cache is None:
        if not _DATA_PATH.is_file():
            raise RuntimeError(
                f"缺少 {_DATA_PATH.name} —— 它由 Node 从 MXU 源码导出："
                f" node dump_data.ts > data.json"
            )
        _data_cache = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return _data_cache


def hotkey_key_map() -> dict:
    return _data()["hotkeyKeyMap"]


def mxu_special_tasks() -> dict:
    return _data()["mxuSpecialTasks"]


# 与 TS 的 YES_CASE_NAMES / NO_CASE_NAMES 逐项一致
_YES_CASE_NAMES = ("Yes", "yes", "Y", "y")
_NO_CASE_NAMES = ("No", "no", "N", "n")

# TS: escapedPlaceholder 的正则字符集（用于转义后再进正则）
_RE_META = re.compile(r"[.*+?^${}()|\[\]\\]")


def _escape_placeholder(name: str) -> str:
    """把占位符里的正则元字符转义。

    注意替换串用 ``\\g<0>`` 而不是 TS 源码里的 ``\\\\$&`` —— ``$&`` 是
    **JavaScript** 的"整个匹配"语法；Python 的 ``re.sub`` 不认它，会把
    ``\\$&`` 整段当字面量吐出来，于是所有占位符都匹配不上（表现为 override
    里留下一堆没替换的 ``{Xxx}``）。移植时踩过一次，留此注释。
    """
    return _RE_META.sub(r"\\\g<0>", name)


# ---------------------------------------------------------------------------
# optionHelpers.ts
# ---------------------------------------------------------------------------


def find_switch_case(cases: Optional[list], is_checked: bool) -> Optional[dict]:
    """switch 的选中值 → 对应 case。找不到返回 ``None``。

    对应 ``findSwitchCase``：先按 Yes/No 的四种写法（大小写各一）找，找不到
    就没有 —— 找不到的兜底在调用方（回退到字面量 ``Yes`` / ``No``）。
    """
    if not cases:
        return None
    targets = _YES_CASE_NAMES if is_checked else _NO_CASE_NAMES
    for case in cases:
        if case.get("name") in targets:
            return case
    return None


# ---------------------------------------------------------------------------
# storesHelpers.ts（只取编译器用到的部分）
# ---------------------------------------------------------------------------


def create_default_option_value(option_def: dict) -> dict:
    """按定义生成默认值。对应 ``createDefaultOptionValue``。"""
    t = option_def.get("type")

    if t == "input":
        values: dict[str, str] = {}
        for item in option_def.get("inputs", []) or []:
            # TS: input.default || ''  —— falsy 兜底
            values[item["name"]] = item.get("default") or ""
        return {"type": "input", "values": values}

    if t == "hotkey":
        values = {}
        for item in option_def.get("hotkeys", []) or []:
            values[item["name"]] = item.get("default") or ""
        return {"type": "hotkey", "values": values}

    if t == "switch":
        cases = option_def.get("cases") or []
        default_case = option_def.get("default_case") or (
            cases[0].get("name") if cases else None
        ) or "Yes"
        return {"type": "switch", "value": default_case in _YES_CASE_NAMES}

    if t == "checkbox":
        return {"type": "checkbox", "caseNames": list(option_def.get("default_case") or [])}

    # select（缺省分支）
    cases = option_def.get("cases") or []
    default_case = option_def.get("default_case") or (
        cases[0].get("name") if cases else None
    ) or ""
    return {"type": "select", "caseName": default_case}


def sanitize_option_value(
    option_key: str,
    value: dict,
    all_options: dict,
    warn: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """校验保存值是否仍符合当前定义；不符合返回 ``None``（调用方回退默认值）。

    对应 ``sanitizeOptionValue``。
    """

    def say(msg: str) -> None:
        if warn:
            warn(msg)

    option_def = all_options.get(option_key)
    if not option_def:
        say(f'选项 "{option_key}" 已不存在，已丢弃保存值')
        return None

    expected = option_def.get("type") or "select"
    if value.get("type") != expected:
        say(
            f'选项 "{option_key}" 的类型已从 "{value.get("type")}" '
            f'变更为 "{expected}"，已重置为默认值'
        )
        return None

    cases = option_def.get("cases") or []

    if (not option_def.get("type") or option_def["type"] == "select") and value.get(
        "type"
    ) == "select":
        if not any(c.get("name") == value.get("caseName") for c in cases):
            say(f'选项 "{option_key}" 的 case "{value.get("caseName")}" 已不存在，已重置为默认值')
            return None
        return value

    if option_def["type"] == "checkbox" and value.get("type") == "checkbox":
        valid = {c.get("name") for c in cases}
        given = list(value.get("caseNames") or [])
        kept = [n for n in given if n in valid]
        if len(kept) != len(given):
            removed = [n for n in given if n not in valid]
            say(f'选项 "{option_key}" 包含已不存在的 case "{", ".join(removed)}"，已过滤')
        if given and not kept:
            say(f'选项 "{option_key}" 保存的 case 已全部失效，已重置为默认值')
            return None
        return {"type": "checkbox", "caseNames": kept}

    if option_def["type"] == "input" and value.get("type") == "input":
        valid = {i["name"] for i in option_def.get("inputs", []) or []}
        values = {
            k: v for k, v in (value.get("values") or {}).items() if k in valid
        }
        out: dict[str, Any] = {"type": "input", "values": values}
        encrypted = {
            k: v
            for k, v in (value.get("encryptedValues") or {}).items()
            if k in valid
        }
        if encrypted:
            out["encryptedValues"] = encrypted
        return out

    return value


# ---------------------------------------------------------------------------
# hotkey.ts（pipelineOverride.ts 内的两段）
# ---------------------------------------------------------------------------


def convert_hotkey_key_name(key_name: str, controller_type: Optional[str] = None) -> Optional[int]:
    """按键名 → 虚拟键码。未知返回 ``None``。

    对应 ``convertHotkeyKeyName``。两个要点：
    - 按控制器**类型**查表，不是 name；
    - MacOS 没有回退 —— 映射表缺失时必须返回 None，绝不能退回 Win32 VK。
    """
    tables = hotkey_key_map()
    if controller_type and controller_type in tables:
        table = tables[controller_type]
    elif controller_type == "MacOS":
        table = None
    else:
        table = tables.get("Win32")

    if table is None:
        return None
    return table.get(key_name.strip().upper())


def split_hotkey_combo(value: str) -> tuple[str, list[str]]:
    """``"Ctrl+Shift+A"`` -> ``("A", ["Ctrl", "Shift"])``。末位是主键。

    对应 ``splitHotkeyCombo``。
    """
    parts = [p.strip() for p in value.split("+")]
    parts = [p for p in parts if p]
    if not parts:
        return "", []
    return parts[-1], parts[:-1]


# ---------------------------------------------------------------------------
# pipelineOverride.ts 主体
# ---------------------------------------------------------------------------


def is_option_incompatible(
    option_def: dict, controller_name: Optional[str], resource_name: Optional[str]
) -> bool:
    """选项是否与当前控制器/资源不兼容（应跳过）。"""
    controllers = option_def.get("controller") or []
    if controllers:
        if not controller_name or controller_name not in controllers:
            return True
    resources = option_def.get("resource") or []
    if resources:
        if not resource_name or resource_name not in resources:
            return True
    return False


def _json_dumps(value: Any) -> str:
    """与 JS ``JSON.stringify`` 等价：紧凑、无空格、非 ASCII 不转义。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _js_truthy(value: Any) -> bool:
    """JS 的真值判断，不是 Python 的。

    TS 里所有 ``if (x.pipeline_override)`` 都要用这个，不能用 Python 的
    ``if x.get(...)``：**JS 的 ``{}`` 和 ``[]`` 是 truthy，Python 里是 falsy。**

    踩过的具体后果：``CreditShoppingPriority1`` 的选项模板是空对象 ``{}``，
    TS 会 ``push({})`` 往 override 数组里塞一个空元素，Python 直接跳过 ——
    数组少一个元素。虽然深合并后两者结果相同（空对象不贡献任何字段），但
    数组形态不一致，黄金测试会报分歧，而 ``MaaFramework`` 那边数组元素顺序
    也是有意义的，不能当成"无所谓"。
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and value == value  # 排除 NaN
    return True  # dict / list / True 都是 JS 里的 truthy


def _json_string_body(value: str) -> str:
    """``JSON.stringify(s).slice(1,-1)`` —— 转义后的字符串内容（不含引号）。

    JS 会把 ``\\`` ``"`` 换控制字符转义，但**不转义** ``/`` 与非 ASCII 字符。
    Python 的 ``ensure_ascii=False`` 与之一致；``json.dumps`` 另外会转义
    ``\\u2028`` / ``\\u2029`` 吗？不会 —— 这一点上两边一致，无需处理。
    """
    return _json_dumps(value)[1:-1]


def _replace_all(text: str, pattern: re.Pattern, value: str) -> str:
    """函数式替换：绕开 JS ``$&`` / Python ``\\g<1>`` 的替换串语义。"""
    return pattern.sub(lambda _m: value, text)


def collect_option_overrides(
    option_key: str,
    option_values: dict,
    overrides: list,
    all_options: dict,
    controller_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    controller_type: Optional[str] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> None:
    """递归处理选项的 pipeline_override，收集进 ``overrides``。

    对应 ``collectOptionOverrides`` —— 编译器里唯一有状态的部分。
    """

    def say(msg: str) -> None:
        if warn:
            warn(msg)

    option_def = all_options.get(option_key)
    if not option_def:
        return
    if is_option_incompatible(option_def, controller_name, resource_name):
        return

    saved = option_values.get(option_key)
    sanitized = (
        sanitize_option_value(option_key, saved, all_options, say) if saved else None
    )
    option_value = sanitized or create_default_option_value(option_def)
    value_type = option_value.get("type")

    if value_type == "checkbox" and option_def.get("type") == "checkbox":
        # 多选：按 cases 的**定义顺序**合并所有选中项的 override，不是单选
        selected = set(option_value.get("caseNames") or [])
        for case_def in option_def.get("cases") or []:
            if case_def.get("name") in selected and _js_truthy(case_def.get("pipeline_override")):
                overrides.append(case_def["pipeline_override"])

    elif value_type in ("select", "switch") and "cases" in option_def:
        if value_type == "switch":
            is_checked = bool(option_value.get("value"))
            case = find_switch_case(option_def.get("cases"), is_checked)
            case_name = (case or {}).get("name") or ("Yes" if is_checked else "No")
        else:
            case_name = option_value.get("caseName")

        case_def = next(
            (c for c in (option_def.get("cases") or []) if c.get("name") == case_name),
            None,
        )
        if case_def and _js_truthy(case_def.get("pipeline_override")):
            overrides.append(case_def["pipeline_override"])
        if case_def and case_def.get("option"):
            for nested in case_def["option"]:
                collect_option_overrides(
                    nested,
                    option_values,
                    overrides,
                    all_options,
                    controller_name,
                    resource_name,
                    controller_type,
                    warn,
                )

    elif value_type in ("input", "hotkey") and _js_truthy(option_def.get("pipeline_override")):
        input_defs = (
            (option_def.get("hotkeys") or [])
            if option_def.get("type") == "hotkey"
            else (option_def.get("inputs") or [])
        )
        override_str = _json_dumps(option_def["pipeline_override"])

        for input_def in input_defs:
            input_name = input_def["name"]
            # TS 用 ?? —— 只有 null/undefined 兜底，空字符串原样保留
            input_val = (option_value.get("values") or {}).get(input_name)
            if input_val is None:
                input_val = input_def.get("default")
            if input_val is None:
                input_val = ""
            if not isinstance(input_val, str):
                input_val = str(input_val)

            quoted_ph = "{" + input_name + "}"
            quoted = re.compile(f'"{_escape_placeholder(quoted_ph)}"')
            bare = re.compile(_escape_placeholder(quoted_ph))

            if option_def.get("type") == "hotkey":
                primary, modifiers = split_hotkey_combo(input_val)

                def resolve(name: str) -> str:
                    code = convert_hotkey_key_name(name, controller_type) if name else None
                    return str(code) if code is not None else ""

                # 占位符：{name} / {name}.primary / {name}.modifier1 / .modifier2
                for suffix, key_code in (
                    ("", resolve(primary)),
                    (".primary", resolve(primary)),
                    (".modifier1", resolve(modifiers[0] if len(modifiers) > 0 else "")),
                    (".modifier2", resolve(modifiers[1] if len(modifiers) > 1 else "")),
                ):
                    ph = "{" + input_name + suffix + "}"
                    ph_q = re.compile(f'"{_escape_placeholder(ph)}"')
                    ph_b = re.compile(_escape_placeholder(ph))
                    int_val = key_code or "0"
                    override_str = _replace_all(override_str, ph_q, int_val)
                    override_str = _replace_all(override_str, ph_b, int_val)
                continue

            pipeline_type = input_def.get("pipeline_type") or "string"

            if pipeline_type == "int":
                int_val = input_val or "0"  # TS 用 || —— falsy 兜底
                override_str = _replace_all(override_str, quoted, int_val)
                override_str = _replace_all(override_str, bare, int_val)
            elif pipeline_type == "bool":
                bool_val = "true" if (input_val or "").lower() in ("true", "1", "yes", "y") else "false"
                override_str = _replace_all(override_str, quoted, bool_val)
                override_str = _replace_all(override_str, bare, bool_val)
            else:
                # 字符串：带引号的完整占位符先替换（保持 JSON 结构），未加引号的兜底
                override_str = _replace_all(override_str, quoted, _json_dumps(input_val or ""))
                override_str = _replace_all(
                    override_str, bare, _json_string_body(input_val or "")
                )

        try:
            overrides.append(json.loads(override_str))
        except Exception as exc:
            # TS 同样吞掉解析失败并继续 —— 见模块 docstring 第 2 条
            say(f"解析选项覆盖失败: {type(exc).__name__}")


def deep_merge_objects(*sources: dict) -> dict:
    """递归深合并（非 dict 的值后者覆盖前者）。

    对应 ``deepMergeObjects``。MaaFramework 对同名字段是**完整替换**而非深
    合并，所以多个选项共同贡献 ``custom_action_param`` 时必须先在这里合成
    一个对象，否则后面的会把前面的整块冲掉。
    """
    result: dict = {}
    for source in sources:
        for key, value in source.items():
            if (
                isinstance(value, dict)
                and isinstance(result.get(key), dict)
            ):
                result[key] = deep_merge_objects(result[key], value)
            else:
                result[key] = value
    return result


def _controller_type(project_interface: dict, controller_name: Optional[str]) -> Optional[str]:
    """由控制器 name 解析出**类型**（热键查表用）。

    WlRoots / Linux 若配置了 ``use_win32_vk_code``，热键必须用 Win32 VK 码 ——
    表不一致会让快捷键静默失效。
    """
    if not controller_name:
        return None
    ctrl = next(
        (c for c in project_interface.get("controller", []) or [] if c.get("name") == controller_name),
        None,
    )
    if not ctrl:
        return None
    t = ctrl.get("type")
    if t == "WlRoots" and (ctrl.get("wlroots") or {}).get("use_win32_vk_code"):
        return "Win32"
    if t == "Linux" and (ctrl.get("linux") or {}).get("use_win32_vk_code"):
        return "Win32"
    return t


def generate_task_pipeline_override(
    task_name: str,
    option_values: Optional[dict] = None,
    project_interface: Optional[dict] = None,
    controller_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    global_option_values: Optional[dict] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> list:
    """生成某任务的 pipeline_override（**数组**格式，与 MXU 前端一致）。

    覆盖顺序（后者优先）：
        任务自带 → global_option → resource.option → controller.option → task.option
    """
    option_values = option_values or {}

    if is_mxu_special_task(task_name):
        return generate_mxu_special_task_override(task_name, option_values, warn)

    if not project_interface:
        return []

    ctrl_type = _controller_type(project_interface, controller_name)
    overrides: list = []
    task_def = next(
        (t for t in project_interface.get("task", []) or [] if t.get("name") == task_name), None
    )
    if not task_def:
        return []

    if _js_truthy(task_def.get("pipeline_override")):
        overrides.append(task_def["pipeline_override"])

    all_options = project_interface.get("option")
    if all_options:
        # 1. 全局选项（最低优先级）
        for key in project_interface.get("global_option") or []:
            collect_option_overrides(
                key, global_option_values or {}, overrides, all_options,
                controller_name, resource_name, ctrl_type, warn,
            )
        # 2. 资源包级
        if resource_name:
            res = next(
                (r for r in project_interface.get("resource", []) or [] if r.get("name") == resource_name),
                None,
            )
            for key in (res or {}).get("option") or []:
                collect_option_overrides(
                    key, option_values, overrides, all_options,
                    controller_name, resource_name, ctrl_type, warn,
                )
        # 3. 控制器级
        if controller_name:
            ctrl = next(
                (c for c in project_interface.get("controller", []) or [] if c.get("name") == controller_name),
                None,
            )
            for key in (ctrl or {}).get("option") or []:
                collect_option_overrides(
                    key, option_values, overrides, all_options,
                    controller_name, resource_name, ctrl_type, warn,
                )
        # 4. 任务级（最高）
        for key in task_def.get("option") or []:
            collect_option_overrides(
                key, option_values, overrides, all_options,
                controller_name, resource_name, ctrl_type, warn,
            )

    return overrides


# ---------------------------------------------------------------------------
# specialTasks.ts（MXU 内置特殊任务）
# ---------------------------------------------------------------------------


def is_mxu_special_task(task_name: str) -> bool:
    return task_name in mxu_special_tasks()


def get_mxu_special_task(task_name: str) -> Optional[dict]:
    return mxu_special_tasks().get(task_name)


def generate_mxu_special_task_override(
    task_name: str, option_values: dict, warn: Optional[Callable[[str], None]] = None
) -> list:
    """MXU 内置任务的 override —— 深合并成**单元素**数组。

    MaaFramework 对同名字段完整替换，所以这里必须先合并成一个对象。
    对应 ``generateMxuSpecialTaskOverride``。
    """
    special = get_mxu_special_task(task_name)
    if not special:
        if warn:
            warn(f"未找到特殊任务定义: {task_name}")
        return []

    overrides: list = []
    task_def = special.get("taskDef") or {}
    option_defs = special.get("optionDefs") or {}

    if _js_truthy(task_def.get("pipeline_override")):
        overrides.append(task_def["pipeline_override"])

    for key in task_def.get("option") or []:
        collect_option_overrides(key, option_values, overrides, option_defs, warn=warn)

    if not overrides:
        return []
    return [deep_merge_objects(*overrides)]


__all__ = [
    "collect_option_overrides",
    "convert_hotkey_key_name",
    "create_default_option_value",
    "deep_merge_objects",
    "find_switch_case",
    "generate_mxu_special_task_override",
    "generate_task_pipeline_override",
    "hotkey_key_map",
    "is_mxu_special_task",
    "is_option_incompatible",
    "mxu_special_tasks",
    "sanitize_option_value",
    "split_hotkey_combo",
]
