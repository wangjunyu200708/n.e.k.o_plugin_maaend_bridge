"""pretask 参数构造（纯 Python 移植）。

移植自 MXU 的 ``src/types/pretasks.ts``。

pretask 与普通任务的本质区别
----------------------------
- **不进 Tasker 队列**，没有 entry、没有 pipeline
- 是**直接启动外部程序**（MaaEnd 的 pretask = ``agent/go-service --pretask GameSetting``）
- 参数 = interface.json 里的固定 ``args`` + **序列化后的 option JSON**
  （单行紧凑 JSON，作为最后一个参数追加）

与 ``pipeline_override.py`` 同源，两者的选项清洗/默认值都复用那里的实现，
避免同一套语义写两遍。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from pipeline_override import (  # noqa: F401  (类型别名)
    create_default_option_value,
    find_switch_case,
    is_option_incompatible,
    sanitize_option_value,
)

PRETASK_NAME_PREFIX = "__MXU_PRETASK__"


def normalize_pretask_configs(pretask: Any) -> Optional[list]:
    """``pretask`` 字段（单对象或数组）→ 数组；未定义返回 ``None``。

    逐字对应 MXU ``src/types/interface.ts:94``。
    """
    if not pretask:
        return None
    return pretask if isinstance(pretask, list) else [pretask]


def pretask_item_id(item: dict) -> str:
    """稳定标识：缺省 name 时回退到 exec。"""
    return item.get("name") or item.get("exec") or ""


def pretask_name(item: dict) -> str:
    return PRETASK_NAME_PREFIX + pretask_item_id(item)


def is_pretask_name(task_name: str) -> bool:
    return isinstance(task_name, str) and task_name.startswith(PRETASK_NAME_PREFIX)


def get_pretask_items(project_interface: Optional[dict]) -> list:
    if not project_interface:
        return []
    return normalize_pretask_configs(project_interface.get("pretask")) or []


def get_pretask_item(project_interface: Optional[dict], task_name: str) -> Optional[dict]:
    if not is_pretask_name(task_name):
        return None
    target = task_name
    return next(
        (item for item in get_pretask_items(project_interface) if pretask_name(item) == target),
        None,
    )


def _collect_pretask_option_values(
    option_key: str,
    option_values: dict,
    all_options: dict,
    result: dict,
    controller_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> None:
    """把一个选项的取值收进 ``result``，并递归激活 case 的嵌套选项。

    对应 ``collectPretaskOptionValues``。注意它**不做 pipeline_override 模板
    替换** —— pretask 传给外部程序的是选项值本身，形状按类型：
    ``switch``/``select`` → 字符串，``checkbox`` → 数组，
    ``input``/``hotkey`` → ``{子项名: 文本}`` 字典。
    """
    option_def = all_options.get(option_key)
    if not option_def:
        if warn:
            warn(
                f'pretask 引用了未定义的 option "{option_key}"，序列化时将跳过；'
                f"请确认 pretask.option 与 pi.option 中的键名一致"
            )
        return

    if is_option_incompatible(option_def, controller_name, resource_name):
        return

    if option_key in result:
        return

    saved = option_values.get(option_key)
    sanitized = (
        sanitize_option_value(option_key, saved, all_options, warn) if saved else None
    )
    option_value = sanitized or create_default_option_value(option_def)
    value_type = option_value.get("type")
    cases = option_def.get("cases")

    if value_type == "checkbox":
        result[option_key] = list(option_value.get("caseNames") or [])
        return

    if value_type == "input":
        values: dict[str, str] = {}
        if option_def.get("type") == "input":
            for item in option_def.get("inputs") or []:
                values[item["name"]] = (option_value.get("values") or {}).get(
                    item["name"]
                ) or item.get("default") or ""
        result[option_key] = values
        return

    if value_type == "hotkey":
        values = {}
        if option_def.get("type") == "hotkey":
            for item in option_def.get("hotkeys") or []:
                values[item["name"]] = (option_value.get("values") or {}).get(
                    item["name"]
                ) or item.get("default") or ""
        result[option_key] = values
        return

    # select / switch
    if value_type == "switch":
        case = find_switch_case(cases, bool(option_value.get("value"))) if cases else None
        case_name = (case or {}).get("name") or (
            "Yes" if option_value.get("value") else "No"
        )
    else:
        case_name = option_value.get("caseName")
    result[option_key] = case_name

    if cases:
        case_def = next((c for c in cases if c.get("name") == case_name), None)
        for nested in (case_def or {}).get("option") or []:
            _collect_pretask_option_values(
                nested, option_values, all_options, result,
                controller_name, resource_name, warn,
            )


def serialize_pretask_options(
    item: dict,
    option_values: dict,
    project_interface: Optional[dict],
    controller_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """选项取值 → 单行紧凑 JSON 字符串。

    ``item.option`` 为空时返回 ``None``（不追加这个参数）。
    对应 ``serializePretaskOptions``。
    """
    keys = item.get("option") or []
    if not keys:
        return None
    all_options = (project_interface or {}).get("option") or {}
    result: dict = {}
    for key in keys:
        _collect_pretask_option_values(
            key, option_values, all_options, result,
            controller_name, resource_name, warn,
        )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def build_pretask_args(
    item: dict,
    option_values: Optional[dict],
    project_interface: Optional[dict],
    controller_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> list:
    """完整参数数组 = 固定 ``args`` + 序列化的 option JSON。

    对应 ``buildPretaskArgs``。
    """
    args = list(item.get("args") or [])
    payload = serialize_pretask_options(
        item, option_values or {}, project_interface, controller_name, resource_name, warn
    )
    if payload is not None:
        args.append(payload)
    return args


def gen_pretask_args(
    pretask_name_or_id: str,
    option_values: Optional[dict] = None,
    project_interface: Optional[dict] = None,
    controller_name: Optional[str] = None,
    resource_name: Optional[str] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> dict:
    """按名字取 pretask 条目并生成启动参数。

    ``pretask_name_or_id`` 接受伪任务名（``__MXU_PRETASK__GameSetting``）或
    条目 id（``GameSetting``），与 TS 版一致。

    返回 ``{"name", "exec", "args"}``；``exec`` 可能是相对路径
    （如 ``agent/go-service``），调用方需按 MaaEnd 根目录解析成绝对路径。
    """
    items = get_pretask_items(project_interface)
    item = next(
        (
            it
            for it in items
            if pretask_name(it) == pretask_name_or_id
            or pretask_item_id(it) == pretask_name_or_id
        ),
        None,
    )
    if not item:
        known = ", ".join(pretask_item_id(i) for i in items) or "(none)"
        raise RuntimeError(f"未知预任务 {pretask_name_or_id}；可用: {known}")
    return {
        "name": pretask_item_id(item),
        "exec": item.get("exec"),
        "args": build_pretask_args(
            item, option_values, project_interface, controller_name, resource_name, warn
        ),
    }


__all__ = [
    "build_pretask_args",
    "gen_pretask_args",
    "get_pretask_item",
    "get_pretask_items",
    "is_pretask_name",
    "normalize_pretask_configs",
    "pretask_item_id",
    "pretask_name",
    "serialize_pretask_options",
]
