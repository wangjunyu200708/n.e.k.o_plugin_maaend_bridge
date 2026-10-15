# MaaEnd 自建执行器 —— 实现说明

> 用 **Python 3.11 + MaaFramework 5.14.2** 直接驱动 MaaEnd 的资源层，
> **绕开 MXU（Tauri + React 桌面宿主）**。原 MXU GUI 由一个自建的控制面板替代。

---

## 1. 代码在哪

**全部实现在 `.workbuddy/` 目录下**（本仓库其余部分是 MaaEnd 官方资源层，未改动）。

```
.workbuddy/
├── panel_server.py          ★ 控制面板后端（HTTP API + SSE + 长驻提权进程）766 行
├── runner.py                ★ 执行器核心（资源/控制器/agent/任务）437 行
├── run_elevated.py          ★ 提权启动包装（UAC 自提升）104 行
├── start_panel.bat            面板启动器（自动请求管理员权限）38 行
│
├── override/                  option → pipeline_override 编译器
│   ├── override_gen.py        Python 外壳（调 Node + 深合并）198 行
│   ├── cli.ts                 Node 入口（stdin JSON → stdout JSON）52 行
│   ├── build_interface.py     JSONC 解析 + 展开 68 条 import 107 行
│   ├── pipelineOverride.ts  ★ 从 MXU 前端原样复制 692 行
│   ├── specialTasks.ts        从 MXU 前端原样复制 705 行
│   ├── storesHelpers.ts       从 MXU 前端原样复制 253 行
│   ├── optionHelpers.ts       从 MXU 前端原样复制 25 行
│   ├── logger.ts              唯一 stub（替换掉依赖 loglevel/Tauri 的原版）
│   ├── golden_test.py         黄金测试（检测上游漂移）136 行
│   ├── golden/*.json          5 份冻结快照
│   ├── gen_source.py          重新生成来源锁定信息
│   └── SOURCE.txt             复用了哪些 MXU 文件、版本与 sha256
│
├── API_CONTRACT.md          ★ 前端实现契约（端点/字段/选项值格式/配色）
├── PROJECT_STATUS_EN.md       英文现状报告（专家评估用）
│
│   ── 测试脚本 ──
├── api_smoke.py               API 冒烟（16 项断言，不碰 MaaFw）
├── test_prepare.py            真机验证（初始化/分辨率/截图）
├── smoke_resource.py          资源加载冒烟
├── smoke_override.py          编译器 ↔ MaaFw 端到端
├── probe_controller.py        控制器/截图探测
├── probe_agent.py             agent 链路探测
├── fix_bat.py                 把 .bat 规范成 cmd 能解析的形式（ASCII + CRLF）
│
├── run_logs/                  运行时日志（maafw.log / go-service.stdout.log / vision/）
└── panel_token.txt            启动时生成的随机 token
```

★ = 核心实现文件

### 外部依赖（不在本仓库）

| 路径 | 内容 |
|---|---|
| `E:/MaaEnddm/MaaEnd-win-x86_64-v2.32.0` | 发行版：`resource/`（5809 节点）、`agent/`（go-service + cpp-algo）、`interface.json`、`locales/` |
| `E:/MaaEnddm/MXU-2.7.1/MXU-2.7.1` | MXU 前端源码，`override/*.ts` 从这里复制 |
| `E:/MaaEnddm/MaaEnd-2/assets` | 资源层源仓库（interface.json / tasks / pipeline / 模型） |
| `F:/插件备份/endfield/endfield/maa_runner.py` | N.E.K.O 的 endfield 插件里另一套 Python 直连实现，可作参考 |

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
双击 .workbuddy\start_panel.bat     :: 自动请求管理员权限
```

启动后浏览器访问 `http://127.0.0.1:12702`（token 打印在控制台）。
前端页面尚未实现 —— 契约见 `API_CONTRACT.md`，或直接用命令行方式。

### 方式 B：命令行跑任务

```bat
C:\Users\25672\AppData\Local\Python\pythoncore-3.11-64\python.exe ^
    .workbuddy\run_elevated.py CreditShoppingN2 300
```

```
run_elevated.py <任务名> <超时秒> [--set 选项名=值]
```

`--list-tasks` 可列出全部 46 个任务。

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
:: 编译器漂移检测（不需要游戏）
python .workbuddy\override\golden_test.py --check

:: API 冒烟（不需要游戏，16 项断言）
python .workbuddy\api_smoke.py --port 12702

:: 真机验证（需要游戏在运行）
python .workbuddy\test_prepare.py --port 12702

:: 资源加载冒烟
python .workbuddy\smoke_resource.py
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
| Python | 3.11.9（`AppData\Local\Python\pythoncore-3.11-64`） |
| MaaFw | 5.14.2（pip wheel，自带全部原生 DLL） |
| Node | 24.x（仅用于跑 override 编译器，需原生支持 TypeScript） |
| 端口 | 12702（仅 127.0.0.1） |
