## 实现交接：BACKEND-HANDOFF.md 的四个待办项

已全部实现并通过验收。`182 passed, 9 skipped`（原 161），`ruff check .` 全过。

### 代码在哪

| 文件 | 改动 | 内容 |
|---|---|---|
| `service.py` | **+316 行** | 四个能力的实现：`launch_panel` / `screenshot` / `log_lines` + `_log_pump` / `describe_executor`，以及 `get_status(include_executor=)` |
| `__init__.py` | **+99 行** | 3 个 `@ui.action`（`maaend_launch_panel` / `maaend_screenshot` / `maaend_logs`）+ `set_plugin_dir` 接线 |
| `prompts.py` | **+70 行** | 6 组新文案 × 8 语言（`LAUNCH_*`） |
| `launcher.py` | +16 行 | `manual_launcher_path()` + `describe()` 补 `manual_launcher` 字段 |
| `tests/test_panel_surfaces.py` | 新增 18 用例 | 见下方「测试的三个坑」 |
| `tests/fakes.py` | +16 行 | `FakePanelClient` 补 `screenshot()` / `open_log_stream()` |
| `tests/test_tool_contract.py` | +5 行 | 白名单加 3 个新 entry |

### 契约实现要点

**1. `maaend_launch_panel`** — 三处坑按交接文档处理：
- 先 `probe_status()`，通了直接返回 `launched: false`，不再拉第二个面板
- `resolve_interpreter` 判据是 `import maa`（`launcher.py:107` 已有实现）
- UAC 异步：`launch_elevated` 返回后轮询 `/api/ping` 直到 `_launch_timeout`（默认 90s），超时时返回 `LAUNCH_PENDING` —— 是「若授权窗口还在请点是」这类有用的提示，不是错误
- 失败文案走 `MaaendRejected` + `prompts.t("LAUNCH_*")`，不含路径和异常类名

**2. `maaend_screenshot`** — 缩图在插件侧做：
```
/api/screenshot → 1280x720 PNG (~900 KiB)
  → PIL 缩到 560 宽 + JPEG q72 → ~30 KB
  → "data:image/jpeg;base64,…"
```
PIL 惰性 import 且**错误缓存**（`_PIL_ERROR` / `_PIL_CHECKED`），失败后直接重放，不会每次重试 import；整个缩图走 `asyncio.to_thread`（CPU 活）。

**3. `maaend_logs`** — 游标自己发号：
- `service._log_seq` 是插件进程内单调的 `seq`，**面板的 `id` 只当游标消费，永不回传**
- 面板重启（token 变了）→ 清空缓冲 + `_log_seq` 归零 → 界面手里的 `since` 大于 `next` → 返回 `reset: true`
- 环形缓冲 `deque(maxlen=_log_buffer_lines)`，默认 400
- 日志泵惰性启动（`_ensure_log_pump`），**不在 `startup` 里 create_task**（那个 loop 立刻关闭）；断线指数退避重连，1s→8s 封顶
- 只记 `_log_instance`，**不碰 `_panel_instance`**（那是任务通道的重启信号）

**4. `maaend_status` 的 `executor` 块** — 加了 `include_executor` 开关，默认关：
- `maaend_status` entry 传 `True`（面板只在**未连上**时才读它，正是诊断有用的时候）
- `maaend_ui_context` 不传（它被 5 秒轮询，`launcher.describe` 会为每个候选解释器起 `import maa` 子进程）

额外补了 `service.set_plugin_dir(self.plugin_dir)` 接线 —— 交接文档第 4 节末尾提到的「这一步还没接线」。

### 测试的三个坑（我自己踩的，写在这里以免重犯）

1. **`_service()` 必须显式传 `panel_dir`**。默认 `default_panel_dir()` 指向真实 executor 目录，那里的 `panel_token.txt` 是**活着的面板的** token —— 测试会读到它。
2. **探活必须 fake**。`test_launch_without_an_executor_directory_is_refused` 最初用真 client，如果本机面板在跑，`probe_status()` 返回 True，测试直接走「已经在跑」分支，**永远测不到要测的东西**。
3. **`launcher.describe` 在测试里必须 stub**。它对 PATH 上每个候选解释器跑 `import maa`（`_PROBE_TIMEOUT_S = 25s`），一个测试文件因此跑了 108 秒。现在 `_service()` 里统一 `_no_probe(service)`。

慢的两个测试也修了根因，不是调 timeout：
- `ping_result=False` 让等待跑满 90 秒 → 改成 `client.ping = ping_later` 直接控制返回序列
- `configure({"panel_launch_timeout_seconds": 2.0})` 被 `max(10.0, ...)` 抬成 10 秒 → 直接设 `service._launch_timeout = 1.0`

现在全量 25 秒，最慢的测试 2 秒。

### 未做 / 待确认

- `i18n/` 下只补了 zh-CN / zh-TW / en 之外的 5 个语言文件（`es/ja/ko/pt/ru`），是 `scripts/check_i18n.py` 要求的 8 语齐备；**新 action 的 label 目前是硬编码中文**（`@ui.action(label="启动执行器")`），与现有 7 个 action 一致，没动 i18n 体系
- `tests/test_live_panel.py`（`MAAEND_LIVE=1`）的只读契约测试没跑 —— 需要真机面板在跑，且交接文档明确要求新 action 的真机验证不碰 `/api/run` 与 `/api/prepare`