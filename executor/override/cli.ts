// 上游逻辑的命令行入口（Node 24 原生跑 TypeScript，无需构建）。
//
// 两种模式，由输入 JSON 的 `mode` 字段决定：
//
// ── mode: "override"（缺省）────────────────────────────────────────────
// 生成某个任务的 pipeline_override。
// 输入:
// {
//   "taskName": "AutoCollect",
//   "optionValues": { "<optionKey>": {...} },   // 可选，缺省走默认值
//   "interface": { ... },                        // 合并 import 后的 ProjectInterface
//   "controllerName": "Win32-Front",             // 可选
//   "resourceName": "官服",                      // 可选
//   "globalOptionValues": { ... }                // 可选
// }
// 输出: pipeline_override 的 JSON 数组字符串
//
// ── mode: "pretask" ────────────────────────────────────────────────────
// 生成某个 pretask 的完整命令行参数数组（上游 `buildPretaskArgs`）。
// pretask 不进 Tasker，而是直接启动外部程序（Ma = go-service），
// 参数 = interface.json 里的固定 args + 序列化后的 option JSON。
// 输入:
// {
//   "pretaskName": "GameSetting",                // 或用 pretaskItem 指名
//   "pretaskItem": { ... },                      // 可选，直接给条目
//   "optionValues": { ... },
//   "interface": { ... },
//   "controllerName": "Win32-Front",             // 可选
//   "resourceName": "官服"                       // 可选
// }
// 输出: { "name": "...", "exec": "...", "args": ["--pretask","GameSetting","{...}"] }
//
// 用法:
//   node cli.ts < input.json

import { generateTaskPipelineOverride } from './pipelineOverride.ts';
import {
  buildPretaskArgs,
  getPretaskItems,
  pretaskItemId,
  pretaskName,
} from './pretasks.ts';
import type { PretaskItem } from './interfaceTypes.ts';

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const c of process.stdin) chunks.push(c as Buffer);
  return Buffer.concat(chunks).toString('utf-8');
}

function runPretaskMode(input: any): void {
  const pi = input.interface ?? null;
  const items: PretaskItem[] = getPretaskItems(pi) ?? [];

  let item: PretaskItem | undefined;
  if (input.pretaskItem) {
    item = input.pretaskItem as PretaskItem;
  } else if (input.pretaskName) {
    // 上游用伪任务名（__MXU_PRETASK__<id>）索引，这里两种写法都接受
    item = items.find(
      (it) => pretaskName(it) === input.pretaskName || pretaskItemId(it) === input.pretaskName,
    );
  }

  if (!item) {
    const known = items.map((it) => pretaskItemId(it));
    process.stderr.write(
      `unknown pretask: ${input.pretaskName ?? '(none)'}; available: ${known.join(', ') || '(none)'}\n`,
    );
    process.exit(1);
  }

  const args = buildPretaskArgs(
    item,
    input.optionValues ?? {},
    pi,
    input.controllerName,
    input.resourceName,
  );

  process.stdout.write(
    JSON.stringify({
      name: pretaskItemId(item),
      exec: item.exec,
      cwd: input.cwd ?? null,
      args,
    }),
  );
}

async function main(): Promise<void> {
  const raw = await readStdin();
  if (!raw.trim()) {
    console.error('usage: node cli.ts < input.json');
    process.exit(1);
  }
  const input = JSON.parse(raw);

  if (input.mode === 'pretask') {
    runPretaskMode(input);
    return;
  }

  const selectedTask = {
    taskName: input.taskName,
    optionValues: input.optionValues ?? {},
  };

  const out = generateTaskPipelineOverride(
    selectedTask,
    input.interface ?? null,
    input.controllerName,
    input.resourceName,
    input.globalOptionValues ?? {},
  );

  process.stdout.write(out);
}

main().catch((e: unknown) => {
  console.error('failed:', e);
  process.exit(1);
});
