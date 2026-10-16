# MaaEnd 自建执行器 —— 实现说明

> 用 **Python 3.11 + MaaFramework 5.14.2** 直接驱动 MaaEnd 的资源层，
> **绕开 MXU（Tauri + React 桌面宿主）**。原 MXU GUI 由一个自建的控制面板替代。

---

## 1. 代码在哪

全部在**本插件的 `executor/` 目录**下。执行器、它依赖的 Python 运行时、以及它驱动的
游戏资源层都随插件分发（前置条件为零），下面都是相对 `executor/` 的路径。

```
executor/
├── panel_server.py          ★ 控制面板后端（HTTP API + SSE + 长驻提权进程）
├── runner.py                ★ 执行器核心（资源/控制器/agent/任务）
├── packmeta.py                读资源层的自描述（窗口/控制器/项目身份）
├── run_elevated.py            提权包装（命令行跑单个任务用）
├── start_panel.bat            面板启动器（自动请求管理员权限）
│
├── override/                  option → pipeline_override 编译器
│   ├── pipeline_override.py  ★ 运行时实现（从 MXU 的 TypeScript 移植）
│   ├── pretask_builder.py    ★ pretask 启动参数
│   ├── data.json             ★ 从 MXU 导出的数据表
│   ├── override_gen.py         对外 API 外壳
│   ├── build_interface.py      JSONC 解析 + 展开 68 条 import
│   ├── golden_test.py          黄金测试：--check 跑 Python 对快照
│   ├── test_pipeline_override.py  合成 interface 钉语义边界
│   ├── golden/*.json           11 份冻结快照（由 TS 版生成）
│   └── *.ts / cli.ts           MXU 原样复制的 TS，**不参与运行时**（维护期当对照组）
│
├── vendor/                  ★ 自带的 Python 运行时（约 149 MiB）
│   ├── python/                 embeddable CPython 3.11.9
│   ├── site-packages/          maa / numpy / strenum / PIL
│   └── licenses/
│
├── maaend/                  ★ 游戏资源层（AGPL-3.0，MaaEnd 原样、未修改）
│   └── maafw/                   go-service/cpp-algo 要 dlopen 的原生运行时。**不能省**：
│                                这两个 agent 从资源层根目录找 `maafw/`，和 Python 绑定在
│                                自己旁边找不是一回事（DLL 虽逐字节相同）。
│
├── API_CONTRACT.md          ★ 前端实现契约（端点/字段/选项值格式/配色）
│
│   ── 测试脚本 ──
├── api_smoke.py               API 冒烟（不碰 MaaFw）
├── test_prepare.py            真机验证（初始化/分辨率/截图）
├── smoke_resource.py          资源加载冒烟
├── smoke_override.py          编译器 ↔ MaaFw 端到端
├── probe_controller.py        控制器/截图探测
├── probe_agent.py             agent 链路探测
├── fix_bat.py                 把 .bat 规范成 cmd 能解析的形式（ASCII + CRLF）
│
├── run_logs/                  运行时日志（运行期生成）
└── panel_token.txt            启动时生成的随机 token

★ = 核心实现文件

### 外部依赖

**没有。** 资源层与 Python 运行时就是上面那两个带 ★ 的目录，分发时不需要外部发行版、
也不需要用户装 Python。

历史上它们确实是外部依赖（MaaEnd 发行版 + MXU 源码 + 资源层源仓库），那几处现在只剩
**开发期的维护用途**：`override/*.ts` 是移植的来源与对照基准，MXU 升级时用它重新导出
`data.json`；来源与每个文件的 sha256 记在 `override/SOURCE.txt`。

---

## 2. 数据流

```
浏览器页面  ──HTTP/SSE──>  panel_server.py
                                 │
                                 ├── /api/tasks, /api/options   读 interface.json（已 i18n）
                                 ├── /api/run                   编译 override
                                 └── /api/screenshot            controller 抓帧
                                 │
                          runner.py  ★
                                 │  gen_override_object()
                                 ↓
                     override/cli.ts  →  Node 24 原生跑 TypeScript
                                 │  返回 JSON 数组
                                 ↓  merge_overrides() 深合并成 object
                                 ↓
                          MaaFw 5.14.2  Tasker.post_task(entry, override)
                                 │
                                 ├── Resource  ← resource/（5809 节点）
                                 ├── Controller ← Win32Controller(ScreenDC + Seize)
                                 └── Agents     ← go-service.exe + cpp-algo.exe
                                                    （47 自定义识别 / 85 自定义动作）
```

---

## 3. 快速开始

### 方式 A：控制面板（推荐）

```bat
双击 executor\start_panel.bat     :: 自动请求管理员权限
```

启动后浏览器访问 `http://127.0.0.1:12702`（token 打印在控制台）。
前端页面尚未实现 —— 契约见 `API_CONTRACT.md`，或直接用命令行方式。

### 方式 B：命令行跑任务

```bat
executor\vendor\python\python.exe ^
    executor\run_elevated.py CreditShoppingN2 300
```

```
run_elevated.py <任务名> <超时秒> [--set 选项名=值]
```

`--list-tasks` 可列出全部 46 个任务。

⚠️ **必须用自带解释器跑 executor 里的脚本。** 它们依赖
`vendor/python/python311._pth` 把 `executor/` 与 `executor/override/` 放进
`sys.path`——isolated 模式下 `PYTHONPATH` 会被静默忽略，而**脚本自己所在的目录也
不在 `sys.path` 上**（`sys.path[0]` 是标准库那个 zip）。换成别的 Python，
`runner.py` 会在 `from override_gen import ...` 处直接 ImportError。

---

## 4. 五个关键设计决策

### ① option 编译器：直接复用 MXU 的 TypeScript，不做 Python 移植

`override/pipelineOverride.ts` 是**唯一**知道「勾了某个选项该改 pipeline 哪个字段」的地方。
移植成 Python 会随 MXU 上游更新而漂移。

做法：原样复制 4 个 `.ts`（都是纯逻辑，零 DOM / 零 React / 零 Tauri），
用 **Node 24 原生类型剥离**直接 `node cli.ts` 运行，不需要编译步骤。
只有 `logger.ts` 因为依赖 `loglevel` 和 Tauri `paths` 被替换成 stub。

漂移风险用两样东西兜住：
- `golden_test.py` —— 5 个用例的输出冻结成快照，`--check` 检测变化并打印节点级 diff
- `SOURCE.txt` —— 记录来源 MXU 版本与每个文件的 sha256

### ② MaaFw 5.14.2 只吃 object，前端产出的是数组

MXU 前端产出 `[{...},{...}]`；Rust 绑定内部有合并层，**Python 绑定没有**。
直接传数组会报 `MaaResource.cpp L208: json is not object`。

`merge_overrides()` 做**递归深合并**（语义对齐 MXU 的 `deepMergeObjects`）。
必须是深合并 —— 多个元素常改同一节点的不同键，浅合并会丢字段。

### ③ interface.json 是 JSONC，且任务藏在 68 条 import 后面

顶层 `task` 数组是空的；46 个任务和 405 个选项都在 `tasks/*.json` 里。
`build_interface.py` 负责剥注释、去尾逗号、展开 import。

### ④ 四级置前：Seize 输入只投递给前台窗口

症状极具迷惑性：**日志里 28 次 Click、21 次按键，界面毫无反应**。
因为 `SendInput` 发给了当前前台窗口（当时是别的程序）。

`Runner._bring_to_front()` 依次尝试：
`AttachThreadInput` → **模拟按一下 Alt**（Windows 经典绕过，实测靠它成功）→
`SwitchToThisWindow` → 最小化再还原。

必须在 `post_task` **之前**再抢一次 —— 初始化花了 4 秒，这期间窗口早被切走了。
运行期间每 5 秒复查一次，丢了就抢回。

### ⑤ 前置条件在 API 层前置校验

分辨率不是 16:9 时，`go-service` 会硬停任务，但这个错误**只出现在
`go-service.stdout.log`**，MaaFramework 日志里看不到。
所以 `panel_server.py` 在 `/api/run` 之前就检查客户区比例，
不达标直接返回中文报错，不再让用户去翻日志。

---

## 5. 硬性前置条件

| 条件 | 原因 | 不满足时 |
|---|---|---|
| **管理员权限** | Seize（ViGEm/ SendInput）需要提权 | 连接照样成功、点击照样打日志，但游戏毫无反应 |
| **游戏窗口在前台且未最小化** | SendInput 只发前台；最小化时 ScreenDC 会抓到屏幕上别的窗口 | 同上；`/api/status` 的 `focused` / `minimized` 可检测 |
| **16:9 分辨率** | go-service 硬性校验 | 任务启动 17ms 后被强制停止 |

---

## 6. 测试与验证

```bat
:: 下面全部用自带解释器（原因见 3.B 的注意事项）
set PY=executor\vendor\python\python.exe

:: 编译器漂移检测（不需要游戏）
%PY% executor\override\golden_test.py --check

:: API 冒烟（不需要游戏，16 项断言）
%PY% executor\api_smoke.py --port 12702

:: 真机验证（需要游戏在运行）
%PY% executor\test_prepare.py --port 12702

:: 资源加载冒烟
%PY% executor\smoke_resource.py
```

已实测通过的任务：`DailyRewards`（22+ 节点）、`CreditShoppingN2`（57 节点 override，67 秒）。

---

## 7. 已知限制

- 同时只能跑一个任务（`busy` 状态拒绝并发）
- 前端页面**尚未实现** —— 后端与契约已就绪，等页面
- 截图与任务运行共用一把锁，高频截图会与任务短暂争用
- 后端进程退出不会自动重启
- SSE 断线需前端自行用 `since` 续传
- 未处理 license/CDK 校验（本执行器不加载 MXU 宿主，也就不存在该校验）

---

## 8. 环境

| 项 | 值 |
|---|---|
| Python | **自带**：`vendor/python/python.exe`（embeddable CPython 3.11.9；`python311._pth` 把 `vendor/site-packages` 列进 `sys.path`） |
| MaaFw | **自带两份，都是必需的**：`vendor/site-packages/maa`（Python 绑定，5.14.2，纯 `.py` + 17 个原生 DLL，无 `.pyd`）；`maaend/maafw`（同样那 17 个 DLL，供 `go-service.exe` / `cpp-algo.exe` dlopen——它们按资源层根目录解析，不看 Python 那边） |
| numpy / Pillow | **自带**。`maa` 在模块级 `import numpy`，所以它不是可选项；截图那条路用 PIL |
| Node | **运行时不需要**。override 编译器已移植为纯 Python；Node 只在维护期用到（重新导出 `data.json`、`--compare/--update` 拿 TS 当对照组） |
| 端口 | 12702（只监听 127.0.0.1） |

自带运行时的组成、来源与许可见 `vendor/README.md`。
