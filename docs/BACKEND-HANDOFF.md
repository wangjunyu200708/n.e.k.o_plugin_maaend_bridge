# 后端交接：界面已经在调、但还没实现的东西

`surfaces/maaend.tsx` 是照 MXU 布局重写的控制面板，它已经按下面的契约在调
`props.api.call`。这些 `@ui.action` **尚未实现** —— 界面看起来完整，但点下去
会拿到 `entry not found`。

底子已经备好（`launcher.py`、`client.py`、`service.py` 的字段都就位），
下面是每个待办项的精确契约。**不接线也不会坏**：现有 161 个用例与
「状态 / 任务表 / 选项树 / 开始 / 停止 / 初始化 / 重载」七个已有 action 不受影响。

---

## 1. `maaend_launch_panel` — 面板上的「启动执行器」

界面在**未连上执行器**时的告警里调它（`surfaces/maaend.tsx`，`callSimple("maaend_launch_panel")`）。

| | |
|---|---|
| 入参 | `{}` |
| 成功返回 | `Ok({summary: str, launched: bool, diagnostics?: dict})` |
| 失败返回 | `Err(人话)` —— 界面直接展示 `Err` 的文本，所以**不要**把路径/端口/异常类型名塞进去 |

语义与三处坑：

1. **先探一次**。`/api/ping` 通了就别再拉 —— 用户可能只是状态没刷新到。
2. **探测要跑 `import maa`**，不是看解释器存不存在。一个装了 MaaFramework 的
   Python 是唯一靠谱的判据（`launcher.resolve_interpreter` 已实现，逐候选试，
   结果缓存；探到的会写回 `executor/python_path.txt`，让手动 `start_panel.bat`
   用同一个解释器）。
3. **UAC 是异步的**。`ShellExecuteW(..., "runas", ...)` 返回 `>32` 只代表
   *Windows 接受了请求*，不代表面板起来了（用户可能还没点「是」）。
   建议等 `/api/ping` 一小段预算（`service._launch_timeout`，默认 90s），
   超时就返回"已请求提权，若 UAC 还在请点『是』"这类**有用**的话，而不是报错。

底子：

```python
# launcher.py
interpreter, reason = launcher.resolve_interpreter(
    configured=service._executor_python, executor=service.executor_dir()
)
started, message = launcher.launch_elevated(
    interpreter, service.executor_dir(),
    port=<从 base_url 解析>, maaend_dir=service._maaend_dir, logger=...
)
launcher.describe(executor, configured=...)   # 诊断块，见第 4 节
```

`launch_elevated` 里用 `python.exe` 而不是 `pythonw.exe`：面板会 `sys.stdout = Tee(...)`
并往里 print，`pythonw` 下没有 stdout 可包。

---

## 2. `maaend_screenshot` — 右栏「实时画面」

界面按 `state.shotAuto` 每 2.5 秒调一次（`grabShot()`），也支持手动「刷新一张」。

| | |
|---|---|
| 入参 | `{max_width?: int}`（界面**不传**，用 `service._screenshot_max_width`，默认 560） |
| 成功返回 | `Ok({image: "data:image/jpeg;base64,…", width: int, height: int, ts: float})` |
| 失败返回 | `Err(人话)` —— 界面会把它显示在告警区并**停掉连续模式** |

为什么要缩图：面板的 `/api/screenshot` 给的是 1280×720 PNG，约 **900 KiB**；
base64 后 ~1.2 MB，走 `api.call` 的返回通道太重。缩到 560 宽、JPEG q≈72 之后
是 ~30 KB，而面板本来也不需要全分辨率。**缩图在插件侧做**（别改执行器）。

底子：

```python
# client.py —— 已经能用
png: bytes = await client.screenshot(token)     # 取原始 PNG；503 → MaaendNotReady
```

缩图用 PIL（宿主环境自带；`panel_server.py` 也在用）。按铁律：**惰性 import +
错误缓存**（`try: from PIL import Image / except ImportError: 记下错误，后续直接重放`
—— 不要每次调用重试 import）。PIL 是 CPU 活，包 `asyncio.to_thread`。

一个副作用值得知道：执行器那边 `/api/screenshot` 与运行中的任务**共用一把锁**，
所以任务跑着时截图可能排队。`client.screenshot()` 已把读超时放宽到 20s。

---

## 3. `maaend_logs` — 右栏「运行日志」

界面每 2 秒轮询一次（`pollLogs()`）。

| | |
|---|---|
| 入参 | `{since: int, limit?: int}` —— `since` 是**你**的 `seq`，不是执行器的 `id` |
| 成功返回 | `Ok({lines: [{seq:int, t:"HH:MM:SS", src:str, text:str}], next: int, reset: bool})` |
| 失败返回 | `Err`（界面把日志失败**静默吞掉**，因为它是装饰性的；但你还是该返回 Err） |

`seq` / `next` / `reset` 三个字段是这套契约的核心，理由：

- 执行器 `/api/logs` 的 `id` 是**面板进程内**单调的，**面板一重启就从 1 重来**。
  把它当游标回传给插件，重连后拿到的会是一个**空集**（"id 大于旧游标"对新的
  1..N 永远不成立），而且**不报任何错**。
- 所以插件侧自己发号：`service._log_seq`（已就位）。`seq` 对**插件进程**单调，
  面板回传它，语义永远成立。
- 插件重启 → `seq` 归零 → 面板会发现 `since` 大于 `next`，此时回 `reset: true`
  并从头给，界面据此丢掉旧缓冲。
- 环形缓冲用 `service._log_lines`（`deque`，长度 `service._log_buffer_lines`，默认 400）。

底子：

```python
# client.py —— 已经能用。一次连接，泵到结束就返回（重连策略在 service，不在 client）
async for frame in client.open_log_stream(token, since=<执行器游标>):
    # frame 已经是解析过的 dict：{"id", "t", "src", "text"}
    ...
```

后台任务的注意事项：

- **别在 `startup` 里 `asyncio.create_task`** —— 那个事件循环在 `startup` 返回后
  就关了，任务会被静默取消。长驻任务起在 `_on_command_loop_start()`，或在首次
  调用时惰性起（加锁防并发）。
- 断线重连：拿**当前令牌**比对 `service._log_instance`。令牌变了就是面板重启过
  → 执行器游标归零。**注意不要写 `service._panel_instance`** —— 那个是任务通道
  的权威实例 id，日志泵写它会吃掉任务通道的重启检测信号（两条通道各记各的）。
- `/api/logs` 只被用来**显示**。任务状态仍然只从 `/api/status` 读 —— 这个分工
  是有意的：显示层重复无害，任务状态层重复会让人重投一个已经做完的任务。

---

## 4. `maaend_status` 追加执行器诊断块

界面用它决定显示哪条警告（`executor.python_ok === false` → 显示"找不到解释器"
并附 `python_reason`）。现有 `maaend_status` 返回的快照里加一个 `executor` 键即可：

```python
snapshot["executor"] = await asyncio.to_thread(
    launcher.describe, executor_dir, configured=self._executor_python
)
```

`launcher.describe()` 已经产出界面要的全部字段：
`executor_present` / `python_ok` / `python` / `python_reason` / `node` /
`manual_launcher` / `plugin_elevated`。

`describe()` 会跑子进程（`import maa`），所以：**别放进 5 秒一次的状态刷新路径**，
放进 `maaend_status` 这个 entry 就够了（界面只在未连上时才显示诊断）。它内部有缓存。

---

## 已经就位的配置项

`service.configure()` 已经在读这些键，你直接用就行（`plugin.toml` 的 `[maaend]` 节）：

| 字段 | 默认 | 用途 |
|---|---|---|
| `_executor_python` | `""` | 装了 MaaFramework 的解释器；空则探测 |
| `_maaend_dir` | `""` | 资源层目录；**空 = 用自带的 `executor/maaend`**（不传 `--maaend-dir`，让执行器自己解析） |
| `_launch_timeout` | `90.0` | 等面板起来的预算 |
| `_screenshot_max_width` | `560` | 缩图宽度 |
| `_log_buffer_lines` | `400` | 环形缓冲行数（改它会重建 deque） |
| `_log_stream_enabled` | `True` | 关掉后不订阅日志 |
| `_log_lines` / `_log_seq` / `_log_instance` / `_log_cursor` / `_log_task` / `_log_streaming` | — | 日志泵的状态位 |

`service.executor_dir()` 给你 `executor/` 的绝对路径（`_plugin_dir` 由 facade 通过
`service.set_plugin_dir(self.plugin_dir)` 注入；**这一步还没接线**，记得在
`MaaendBridgePlugin.__init__` 里补一行，否则 `executor_dir()` 永远是 `None`）。

---

## 验收

```bash
uv run --project <N.E.K.O> pytest tests -q        # 161 passed 不应变少
uv run --project <N.E.K.O> ruff check .
```

真机路径：起一次 `executor/start_panel.bat`（提权），然后在插件面板里点
「启动执行器」看它是否探测到解释器、是否在 UAC 后自己接上；点「刷新一张」
看右栏出图；跑一个短任务看日志面板有没有流动。

`tests/test_live_panel.py` 里有现成的只读契约测试（`MAAEND_LIVE=1`），
它**不碰** `/api/run` 与 `/api/prepare`（那两条会初始化 MaaFramework 并抢走
游戏窗口焦点）—— 你新加的三个 action 的真机验证也请守住这条。
