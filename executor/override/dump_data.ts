// 一次性数据导出器 —— 把 TS 里的**纯数据表**导成 JSON，供 Python 侧读取。
//
// 为什么不手抄
// ------------
// hotkey 键码表约 280 行、MXU 特殊任务表约 550 行，全是字面量。手抄必然出
// 错而且无法察觉（一个键码错了，表现为某个快捷键失灵）。这里让 Node 把它们
// 原样吐成 JSON，Python 直接读 —— 零转录风险。
//
// 导出后 `data.json` 就是唯一的运行时数据源；本脚本只在 MXU 升级时重跑。
//
// 用法: node dump_data.ts > data.json

import { HOTKEY_KEY_MAP } from './pipelineOverride.ts';
import { MXU_SPECIAL_TASKS } from './specialTasks.ts';

const payload = {
  // 版本标记：Python 侧读到这个字段才认为文件是本导出器生成的
  _generated_by: 'dump_data.ts',
  _note: '数据由 MXU 源码导出，请勿手工编辑；改动后重跑 node dump_data.ts > data.json',
  hotkeyKeyMap: HOTKEY_KEY_MAP,
  mxuSpecialTasks: MXU_SPECIAL_TASKS,
};

process.stdout.write(JSON.stringify(payload, null, 2));
