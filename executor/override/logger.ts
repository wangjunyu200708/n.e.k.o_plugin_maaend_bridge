// MXU 原 utils/logger.ts 的替身。
// 原版依赖 loglevel + ./paths（会碰 Tauri API），在 Node 下无法加载。
// pipelineOverride.ts 只用到了 loggers.task.warn，这里用 Proxy 兜住任意 loggers.xxx.yyy。

type Fn = (...args: unknown[]) => void;

const noop: Fn = () => {};
const warn: Fn = (...args: unknown[]) => console.error('[override.warn]', ...args);

const makeLevel = () => ({
  trace: noop,
  debug: noop,
  info: noop,
  log: noop,
  warn,
  error: warn,
});

export const loggers: Record<string, ReturnType<typeof makeLevel>> = new Proxy(
  {},
  { get: () => makeLevel() },
);

export const log = makeLevel();
export default log;
