# MaaEnd 控制面板 —— 后端 API 契约

前端页面（HTML/JS）照这份文档实现即可，不需要接触 MaaFramework。

- **后端**：`panel_server.py`（长驻提权进程）
- **默认地址**：`http://127.0.0.1:12702`
- **只监听 127.0.0.1**，不对外暴露
- **启动方式**：双击 `start_panel.bat`（自动请求管理员权限），或
  `python panel_server.py --port 12702 --open`

---

## 0. 鉴权（必须）

进程是**提权的、能发送键鼠输入**的，所以三层防护，缺一不可：

| 层 | 规则 | 失败响应 |
|---|---|---|
| Host 头 | 必须是 `127.0.0.1` / `localhost` | `403 {"error":"Host 不被允许"}` |
| Origin 头 | 若存在，必须是 `127.0.0.1` / `localhost` | `403 {"error":"Origin 不被允许"}` |
| Token | `X-Auth-Token` 头，或 URL `?token=` | `401 {"error":"缺少或错误的 token"}` |

**Token 获取方式**：后端启动时随机生成，写入 `.workbuddy/panel_token.txt`，
同时打印在控制台。若后端托管了前端页面（`GET /`），token 会以
`window.__PANEL_TOKEN__` 注入 HTML —— 自己的页面用这个即可。

`/api/ping` 是唯一免鉴权端点，用于探活。

> `EventSource` 不能自定义请求头，所以 SSE 走 URL query 传 token
> （`/api/logs?token=...`）。这没问题：token 只对本机有效，且 Origin 校验仍然生效。

---

## 1. 状态机

```
idle ──POST /api/prepare──> ready ──POST /api/run──> busy ──任务结束──> ready
```

- `idle`：进程刚起，MaaFramework 尚未初始化（首次 `/api/prepare` 或 `/api/run` 会自动初始化）
- `ready`：Resource / Controller / Agent 常驻，可直接跑任务
- `busy`：有任务在跑，此时再 `/api/run` 返回 `409`

初始化约需 3~5 秒（加载 5809 个节点 + 拉起两个 agent）。**建议页面打开后立刻调一次
`/api/prepare`**，把等待放在“准备中”状态里，而不是让用户点开始时才等。

---

## 2. 端点一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/ping` | 探活（免鉴权） |
| GET | `/api/status` | 全局状态 |
| GET | `/api/tasks` | 任务列表（46 个） |
| GET | `/api/options?task=<name>` | 某任务的选项树 |
| GET | `/api/screenshot` | 当前画面 PNG |
| GET | `/api/logs?since=<id>&token=<t>` | **SSE** 日志流 |
| POST | `/api/prepare` | 初始化执行环境 |
| POST | `/api/run` | 启动任务 |
| POST | `/api/stop` | 停止当前任务 |
| POST | `/api/check-resolution` | 重新检测分辨率 |

---

## 3. 各端点详情

### `GET /api/status`

```json
{
  "state": "idle | ready | busy",
  "ready": false,
  "elevated": true,
  "hwnd": 3345004,
  "focused": true,
  "resource_nodes": 5809,
  "resource_hash": "8a3b256b99ec48e1",
  "agents": [{"name": "go-service", "pid": 25768, "alive": true},
             {"name": "cpp-algo",  "pid": 18208, "alive": true}],
  "resolution": {
    "w": 1280, "h": 720, "ratio": 1.7778,
    "required": "16:9", "ok": true, "message": ""
  },
  "current": {
    "task": "CreditShoppingN2",
    "label": "🛍️信用点购物",
    "entry": "CreditShoppingMain",
    "started": 1760000000.0,
    "ended": null,
    "timeout": 300,
    "elapsed": 12.3,
    "nodes": ["CreditShoppingMain", "SceneEnterMenuShop", "..."]
  },
  "last_result": {"task": "...", "succeeded": true, "failed": false, "elapsed": 67.2},
  "error": null,
  "task_count": 46
}
```

**页面必看字段**

| 字段 | 用途 |
|---|---|
| `elevated` | `false` 时整页顶部挂红色警告：Seize 输入无效，任务必失败 |
| `resolution.ok` | `false` 时禁用“开始”按钮，把 `resolution.message` 显示出来 |
| `focused` | `false` 说明游戏窗口被抢焦点 —— 点开始前会自动抢回，但可以让用户先手动切过去 |
| `current.nodes` | 实时节点流转，任务跑到哪一步一目了然 |

`resolution` 在 `ready` 之前为 `null`，前端要容错。

### `GET /api/tasks`

```json
{"tasks": [{
  "name": "CreditShoppingN2",
  "label": "🛍️信用点购物",
  "description": "使用信用点购买商品",
  "entry": "CreditShoppingMain",
  "group": ["other_menu"],
  "controller": ["Win32-Front", "ADB", ...],
  "options": ["CreditShoppingReserve", "CreditShoppingClueSend", ...]
}]}
```

> `label` / `description` 已在后端完成 i18n 替换，**不含 `$`**（原始 `interface.json`
> 里全是 `$task.X.label` 占位符）。带 emoji 是资源本身带的。

`controller` 是该任务支持的控制器列表；本执行器固定用 `Win32-Front`，
前端可忽略或用于提示。

### `GET /api/options?task=CreditShoppingN2`

返回可直接渲染的**递归选项树**（已本地化、已展开嵌套）：

```json
{
  "task": "CreditShoppingN2",
  "label": "🛍️信用点购物",
  "tree": [{
    "name": "CreditShoppingReserve",
    "type": "switch",
    "label": "保留信用点",
    "description": "",
    "depth": 0,
    "default_case": "No",
    "cases": [
      {"name": "Yes", "label": "是", "option": []},
      {"name": "No",  "label": "否", "option": []}
    ]
  }, {
    "name": "SellProductSchedule",
    "type": "checkbox",
    "label": "定时执行",
    "depth": 0,
    "default_case": null,
    "cases": [
      {"name": "…ScheduleMonday", "label": "星期一", "option": []}
    ]
  }, {
    "name": "SomeInput",
    "type": "input",
    "label": "保留数量",
    "inputs": [{
      "name": "SomeInput", "label": "数量", "default": "10",
      "pipeline_type": "int", "verify": "^\\d+$", "pattern_msg": "请输入数字"
    }]
  }, {
    "name": "KeymapGeneral",
    "type": "hotkey",
    "label": "快捷键",
    "hotkeys": [{"name": "KeymapInteract", "label": "交互", "default": "F"}]
  }]
}
```

**关键点：嵌套在 `cases[].option` 里。** 例如某个 `switch` 选 `Yes` 后才会出现
3 个子选项 —— 这些子选项也是同一棵树里的节点（带 `depth`），前端在父项选中对应
case 时才渲染它们，并把它们的值一并提交。

`depth` 字段已给出缩进层级，直接用即可。

### `POST /api/run`

```json
{
  "task": "CreditShoppingN2",
  "timeout": 300,
  "options": {
    "CreditShoppingReserve": {"type": "switch", "value": true},
    "SomeSelect":           {"type": "select", "caseName": "CN"},
    "SomeCheckbox":         {"type": "checkbox", "caseNames": ["A", "B"]},
    "SomeInput":            {"type": "input", "values": {"SomeInput": "20"}}
  }
}
```

响应 `202` + 完整 status。失败 `400`：

| 情况 | message |
|---|---|
| 分辨率不是 16:9 | `游戏窗口 960x720（比例 1.3333）不是 16:9，go-service 会强制停止任务。请把游戏分辨率调成 16:9 后重试。` |
| 已有任务在跑 | `已有任务在运行` |
| 未知任务 | `未知任务 XXX` |
| 环境初始化失败 | `RuntimeError: ...` |

**`options` 的四种取值格式（这是前端最容易写错的地方）**

| 类型 | 格式 | 说明 |
|---|---|---|
| `switch` | `{"type":"switch","value":true}` | `true` → 匹配 `cases` 里 `name="Yes"` 的那个，缺省名即 `Yes`/`No` |
| `select` | `{"type":"select","caseName":"CN"}` | `caseName` 必须是 `cases[].name` 之一 |
| `checkbox` | `{"type":"checkbox","caseNames":["A","B"]}` | **多选**；后端按 `cases` 定义顺序合并所有选中项的 override，不是单选 |
| `input` | `{"type":"input","values":{"<input.name>":"文本"}}` | key 是 `inputs[].name`，**不是** option 的 name |
| `hotkey` | `{"type":"hotkey","values":{"<hotkey.name>":"F"}}` | 同上 |

**默认值**：不提交某个选项 = 用 `interface.json` 里的 `default_case`
（switch 看 `default_case == "Yes"`，select 看 `default_case`）。
最省事的做法是**只提交用户改动过的项**，其余一律不传。

### `POST /api/stop`

`{"ok": true}` 或 `{"ok": false, "message": "当前没有运行中的任务"}`

### `GET /api/screenshot`

返回 `image/png`（1280×720）。前端用 `<img src="/api/screenshot?token=...">`
即可，但**必须加时间戳防缓存**，例如每秒刷一次时用
`?token=T&t=Date.now()`。建议只在需要时取（任务运行时、状态页），别做成常驻轮询。

未初始化时返回 `503 {"error": ...}`。

### `GET /api/logs?since=<id>` —— SSE

```
Content-Type: text/event-stream; charset=utf-8

data: {"id":1,"t":"20:05:01","src":"runner","text":"=== 初始化执行环境 ==="}

: ping
```

每条消息是一个 JSON 对象：

| 字段 | 说明 |
|---|---|
| `id` | 单调递增；断线重连时带上最后收到的 `id` 即可续传 |
| `t` | `HH:MM:SS` 本地时间 |
| `src` | `runner`（执行器） / `node`（节点流转，来自 maafw.log 增量解析） / `warn` / `ok` / `error` |
| `text` | 文本 |

- 空闲时每 ~1 秒发 `: ping` 注释行保活，前端忽略即可
- `src == "node"` 的行就是**当前正在执行的 pipeline 节点名**，
  这是判断“跑到哪一步了”最直接的信号
- 断线自动重连：`new EventSource(url + '&since=' + lastId)`

### `POST /api/prepare` / `POST /api/check-resolution`

都是无 body 的 POST，返回 status / resolution 对象。

---

## 4. 前端渲染建议

**任务列表**按 `group` 分组（`group` 是数组，可能多项）。

**选项编辑器**按 `type` 映射控件：

| type | 控件 | 提交值 |
|---|---|---|
| `switch` | 开关 / 单选按钮组（cases 通常只有 Yes/No） | `{"type":"switch","value":bool}` |
| `select` | 下拉框 | `{"type":"select","caseName":str}` |
| `checkbox` | **多选**复选框组 | `{"type":"checkbox","caseNames":[...]}` |
| `input` | 文本框；用 `inputs[].verify` 做校验，失败提示 `pattern_msg` | `{"type":"input","values":{name:str}}` |
| `hotkey` | 按键录制（监听 keydown，格式如 `Ctrl+Shift+A`） | `{"type":"hotkey","values":{name:str}}` |

**嵌套处理**：`cases[].option` 里的子选项，只在父项当前选中的 case 的 `option`
数组里时才显示。这是复刻 MXU 行为的关键 —— 子选项的 override 只在父项选中时才生效。

---

## 5. 视觉规范（仿 MXU）

深色 zinc 主题，直接抄这些色值：

```css
--bg-primary:   #09090b;   /* 页面底色 */
--bg-secondary: #18181b;   /* 卡片 / 面板 */
--bg-tertiary:  #27272a;   /* 输入框 / 分隔 */
--bg-hover:     #3f3f46;
--bg-active:    #52525b;
--text-primary:   #fafafa;
--text-secondary: #a1a1aa;
--text-tertiary:  #71717a;
--text-muted:     #52525b;
--border:         #27272a;
--border-strong:  #3f3f46;
--accent:         #3B4754;  /* 寒武岩灰 */
--accent-hover:   #2D3640;
--radius-sm/md/lg/xl: 6px / 8px / 12px / 16px;
```

字体 `'Segoe UI', -apple-system, 'Roboto', sans-serif`；滚动条 6px 细条。

MXU 的布局顺序：**标题栏 → 标签栏 → 左任务列表 / 中选项编辑器 / 右截图+日志 → 底部工具栏**。

---

## 6. 必须遵守的三条硬约束

1. **后端进程必须以管理员身份运行。** `elevated: false` 时 Seize 输入发不出去，
   日志会显示点击成功但游戏毫无反应。页面应显著提示。
2. **分辨率必须 16:9。** 后端已在 `/api/run` 前置校验，但前端也要在
   `resolution.ok === false` 时禁用开始按钮 —— 否则用户会以为是 bug。
3. **游戏窗口需要在前台。** 后端在 `post_task` 前会自动抢焦点，且运行期间每 5 秒
   检查一次；`focused: false` 时可以让用户知道焦点在别处。

---

## 7. 已知限制

- 同一时刻只能跑一个任务（`busy` 状态拒绝并发）
- 后端进程退出不会自动重启；异常需手动重跑 `start_panel.bat`
- SSE 断线需前端自行重连（`since` 续传）
- 截图与任务运行共用一把锁，极高并发下截图可能短暂排队
