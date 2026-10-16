// ============================================================================
// 从 MXU `src/types/interface.ts` 抽出的最小类型 + 唯一需要的运行时函数。
//
// 为什么单独抽
// ------------
// `pretasks.ts`（复用的上游文件）有两处 import 指向 `interface.ts`：
//   - `import type {...}`  —— 编译期即消失，运行时不需要
//   - `import { normalizePretaskConfigs }` —— 运行时需要
// 而 MXU 的 `interface.ts` 有上千行、还 import 其它模块，整体搬进来不划算。
// 这里只保留 pretask 相关的那几个类型定义和这一个三行函数，
// 结构与上游保持一致（Node 只做类型剥离，不做类型检查，但字段名必须对得上）。
// ============================================================================

/** v2.7.0 预任务（pretask）配置项 */
export interface PretaskItem {
  exec: string;
  controller?: string[];
  resource?: string[];
  args?: string[];
  name?: string;
  label?: string;
  description?: string;
  icon?: string;
  /** 引用的顶层 option 键名数组，其取值序列化为最后一个参数 */
  option?: string[];
}

export interface OptionCase {
  name?: string;
  label?: string;
  option?: string[];
  pipeline_override?: Record<string, unknown>;
  default_case?: string;
}

export interface OptionInput {
  name: string;
  label?: string;
  default?: string;
  pipeline_type?: string;
  verify?: string;
  pattern_msg?: string;
}

export interface OptionDefinition {
  type: 'select' | 'switch' | 'checkbox' | 'input' | 'hotkey';
  label?: string;
  description?: string;
  cases?: OptionCase[];
  default_case?: string;
  inputs?: OptionInput[];
  hotkeys?: OptionInput[];
  pipeline_override?: Record<string, unknown>;
  controller?: string[];
  resource?: string[];
  pipeline_type?: string;
}

export type OptionValue =
  | { type: 'select'; caseName: string }
  | { type: 'checkbox'; caseNames: string[] }
  | { type: 'switch'; value: boolean }
  | { type: 'input'; values: Record<string, string> }
  | { type: 'hotkey'; values: Record<string, string> };

export interface TaskItem {
  name: string;
  label?: string;
  entry: string;
  description?: string;
  icon?: string;
  option?: string[];
  controller?: string[];
  resource?: string[];
}

export interface ProjectInterface {
  name?: string;
  task?: TaskItem[];
  pretask?: PretaskItem | PretaskItem[];
  option?: Record<string, OptionDefinition>;
  controller?: unknown[];
  resource?: unknown[];
}

/**
 * 将 PI 协议中的 pretask 字段（单对象或数组）标准化为数组。
 * 如果 pretask 未定义则返回 undefined。
 *
 * 逐字复制自 MXU `src/types/interface.ts:94`。
 */
export function normalizePretaskConfigs(
  pretask: PretaskItem | PretaskItem[] | undefined,
): PretaskItem[] | undefined {
  if (!pretask) return undefined;
  return Array.isArray(pretask) ? pretask : [pretask];
}
