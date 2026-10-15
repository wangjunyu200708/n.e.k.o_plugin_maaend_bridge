// pipelineOverride 的命令行入口。
// stdin 读 JSON，stdout 输出 pipeline_override 字符串（MaaFramework 直接可用）。
//
// 输入:
// {
//   "taskName": "AutoCollect",
//   "optionValues": { "<optionKey>": {...} },   // 可选，缺省走默认值
//   "interface": { ... },                        // 合并 import 后的 ProjectInterface
//   "controllerName": "Win32-Front",             // 可选
//   "resourceName": "官服",                      // 可选
//   "globalOptionValues": { ... }                // 可选
// }
//
// 用法:
//   node cli.ts < input.json

import { generateTaskPipelineOverride } from './pipelineOverride.ts';

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const c of process.stdin) chunks.push(c as Buffer);
  return Buffer.concat(chunks).toString('utf-8');
}

async function main(): Promise<void> {
  const raw = await readStdin();
  if (!raw.trim()) {
    console.error('usage: node cli.ts < input.json');
    process.exit(1);
  }
  const input = JSON.parse(raw);

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
