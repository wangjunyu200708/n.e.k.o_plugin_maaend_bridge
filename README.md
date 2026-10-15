# maaend_bridge — MaaEnd 终末地执行器桥接

把本地 **MaaEnd 自建执行器**（用 Python + MaaFramework 直接驱动《明日方舟：终末地》，
绕开 MXU 宿主）接进 N.E.K.O.：猫娘可以指挥它跑日常、刷信用点、收菜，
并在任务进行中实时转播"跑到哪一步了"。

这是 `mcbot_bridge`（接本地 mcbot，REST + SSE）与 `game_agent_minecraft`（接 mc-agent，裸 WebSocket）
的**同形兄弟**，接的是第三条线，也是第三种传输。

---

## 它连的是什么

[MaaEnd](https://github.com/MaaEnd/MaaEnd) 的资源层（5809 个 pipeline 节点）+ 一个自建的控制面板后端
（`panel_server.py`，长驻**提权**进程，因为给游戏发键鼠要用 Seize）。

| 面 | 端点 | 说明 |
|---|---|---|
| 探活 | `GET /api/ping` | **唯一免鉴权**端点，返回 `{"ok":true,"app":"maaend-panel"}` |
| 状态 | `GET /api/status` | `state`（`idle`/`ready`/`busy`）、当前任务与**实时节点列表**、`last_result`、分辨率判定、是否提权 |
| 任务 | `GET /api/tasks` | 46 个任务（已由面板本地化，label 带 emoji） |
| | `GET /api/options?task=<name>` | 某任务的递归选项树 |
| | `POST /api/run` | `{task, timeout, options}` → `202` + status；失败 `400` |
| | `POST /api/stop` | → `{"ok":bool}`；**没任务在跑时也是 200** |
| | `POST /api/prepare` | 初始化 Resource/Controller/Agents（3~5 秒） |
| 画面 | `GET /api/screenshot` | 1280×720 PNG |
| 日志 | `GET /api/logs` | SSE 日志总线（`?since=<id>`） |

鉴权是 `X-Auth-Token` 头，令牌由面板**每次启动**用 `secrets.token_urlsafe(32)` 重新生成，
写到 `<面板目录>/.workbuddy/panel_token.txt`。面板另外把 `Host`（以及存在时的 `Origin`）钉死在
`127.0.0.1` / `localhost` 上。

### 令牌即"面板实例身份"

这份 API 里**没有会话 id**，但"令牌变了"就是"面板进程重启过"的**证明**——
而一个已经不在的进程没法交代它手上那个任务后来怎么样了。所以：

- 令牌**每次请求现读**，从不缓存。插件装好时面板可能还没启动过、令牌文件还不存在；
  只有每轮重新判断一次，插件才能自己接上，而不需要用户去重启 N.E.K.O.
- 任务进行中令牌变了 → 这次运行的结果标为 **未知**（既不是成功也不是失败），
  未答的问题作废，**不续跑、不重投**。那个任务可能已经在游戏里做完了。

---

## 两条通道的分工

插件对外的能力分给两个调用者，二者**互斥可见**（这是代码决定的，不是约定）：

| 能力 | 通道 | 谁调用 | 为什么 |
|---|---|---|---|
| 派活、停止 | `@llm_tool` `maaend_run` / `maaend_stop` | **对话 LLM** 直接调 | 「帮我把日常跑了」得由正在跟用户对话的那个模型当场拍板，且需要它的类型化参数 |
| 状态、任务表、选项树 | `@plugin_entry` `maaend_status` / `maaend_list_tasks` / `maaend_task_options` | analyzer、插件面板、CLI | 这是事实与设置，不该占对话模型的工具槽位 |
| 面板上的"开始 / 停止 / 初始化 / 重载" | `@plugin_entry`（带 `@ui.action`） | 插件面板 | hosted 面板只能 `props.api.call` 一个 entry，够不到 `@llm_tool` |

`maaend_start_task` 与 `maaend_run` 是同一次派活在两个渠道上的入口——因为
`@plugin_entry` 永远进不了 main_server 的工具注册表，它对对话模型**零成本**。

第三条是**反向**通道：执行器的进度被翻译成 `push_message` cue，于是猫娘能主动开口，
不需要任何人轮询。

---

## 为什么不用 SSE

`/api/logs` 是一条**进程内**的原始日志总线：

- id 只在本进程内单调递增，**面板一重启就从 1 重新开始**，没有任何会话标识；
- 没有按任务分帧——它把执行器、go-service、MaaFramework 的日志混在一起；
- 单条连接一小时后自行结束，断线要前端自己用 `since` 续传。

拿一个陈旧的**高位游标**去"补发 id 大于游标"的帧，面对重启后的新会话是个**空集**：
新会话的 1..N 帧会**整段静默不可见**，而且不报任何错。要正确消费它，就得自己再
发明一套实例身份与游标重置——为的是一个比 `/api/status` **更差**的信号。

插件需要的一切都在 `/api/status` 里：实时 `current.nodes`、终局的 `last_result`、
面板自己的分辨率判定。`status()` 不取任何锁（不像 `/api/screenshot`），
轮询它足够便宜。

所以：**插件轮询 `/api/status`，从不订阅事件流。**

---

## 一个任务的三种结局

面板的 `last_result` 给的是两个布尔值，而它们张成**三个**结局。只有第三个是"没做成"：

| `succeeded` | `failed` | 插件怎么讲 |
|---|---|---|
| `true` | — | 跑完了，用时 X |
| — | `true` | 失败了 |
| 都不是 | | **没跑完就停了**——可能超时（面板按 `timeout` 主动停），可能被中途停下，可能窗口没了 |

第二种和第三种都**不是成功**，也都不该被说成对方。
另外：**用户主动喊停的那次运行不播报**——那是模型自己发起的事，播报只会诱发对一件已经
放弃的工作的**重新派发**。

---

## 任务名怎么对上号

用户说的是人话，执行器要的是代号。四个 pass，强的证据优先：

1. **代号精确匹配**（大小写无关）——`CreditShoppingN2`
2. **显示名精确匹配**（去掉 emoji、标点、空白）——`信用点购物`
3. **互相包含**——「帮我跑一下自动采集」里含「自动采集」
4. **模糊重合**——因为**真实的 label 不是真实句子的子串**：label 是「日常奖励领取」，
   用户说的是「帮我领取日常奖励」；label 是「信用点购物」，用户说的是「刷一下信用点」。
   只做子串匹配的话，这些全都会变成"没有这个任务，这是 46 个任务"，而这是用户最常见的说话方式。
   所以最后加一轮打分：**字符 bigram 重合率**为主，**去重字符重合率**打折（×0.75）为辅。
   打分按**查询长度**归一化——问的是"这句话在说哪个任务"，长 label 不该因为多出几个用户没说的字被扣分。

两条保守规则，因为**派错任务的代价远大于再问一次**：

- **两个字以下是证据不足**：单个共享的汉字是巧合不是名字（「包」和「存放背包」）。
  此时只有精确匹配算数，其余一律交回目录让模型挑。
- **赢家必须明显领先**：次名与首名在 1.25 倍以内就算**歧义**，两个一起列出来。
  实测「关掉游戏」会同时命中「关闭游戏（安卓端）」和「关闭游戏（PC）」——这正是对的答案。

匹配到的代号永远取自资源层的 `name` 字段，**不用 label 拼**——面板的任务表就是拿这个字符串做键的。

---

## 硬性前置条件

| 条件 | 为什么 | 不满足时插件怎么办 |
|---|---|---|
| **管理员权限** | Seize 发键鼠需要提权 | **直接拒绝派活**并说明。面板自己不会拒绝——它会照常"跑完"，游戏里什么都没发生，正是最坏的错法 |
| **16:9 分辨率** | go-service 硬性校验，不达标 17ms 后强制停任务 | 用面板报的 `w`/`h` **自己组织一句话**拒绝，不转发面板的运维散文 |
| **游戏窗口在前台** | `SendInput` 只发前台窗口 | 只**提醒**：面板会在 `post_task` 前抢一次焦点、运行期间每 5 秒复查一次 |
| **面板后端在运行** | 它是个按需启动的独立进程 | 如实说"连不上"，并指出要双击 `start_panel.bat` |

---

## 配置

`plugin.toml` 的 `[maaend]` 节（打包版请改 `profiles/<name>.toml`，`config/plugin.toml` 改了不生效）：

| 键 | 默认 | 说明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:12702` | 必须是 `127.0.0.1`/`localhost`，否则撞面板的 Host 白名单 |
| `panel_dir` | 空 | 面板仓库的 `.workbuddy` 目录，用来找 `panel_token.txt` |
| `token_file` | 空 | 显式指定令牌文件；留空用 `<panel_dir>/panel_token.txt` |
| `http_timeout_seconds` | `10` | 单个 REST 调用的超时 |
| `prepare_timeout_seconds` | `90` | `/api/prepare` 单独一份预算（拉 5809 节点 + 两个 agent） |
| `poll_interval_seconds` | `2.0` | 任务进行中的轮询间隔 |
| `default_timeout_minutes` | `30` | 派活时发给面板的任务时长上限。面板自己的默认是 **5 分钟**，到点会**主动停任务**——对日常/刷材料偏短 |
| `max_timeout_minutes` | `120` | 模型能要求的单任务时长上限 |
| `task_wait_timeout_seconds` | `3600` | 等它结束的预算；总是**大于**面板自己的截止时间，否则我们会抢在面板报结果之前先放手 |
| `progress_min_interval_seconds` | `10` | 进度推进 LLM 上下文的最小间隔 |

`panel_dir` 留空时会依次试 `MAAEND_PANEL_DIR` 环境变量、`E:/MaaEnddm/MaaEnd-2/.workbuddy`、
`D:/MaaEnddm/MaaEnd-2/.workbuddy`、`~/MaaEnd-2/.workbuddy`。**没有一个猜得中时请显式填。**

---

## 启用

1. 先起执行器：双击面板仓库里的 `.workbuddy\start_panel.bat`（会自动请求提权）。
2. 在插件面板里启动本插件（`auto_start = false`，默认不启动——执行器是按需开的东西，
   不该默认占进每个会话的工具槽位）。
3. 进游戏，把分辨率设成 16:9。
4. 在设置里确认 `panel_dir`（或 `token_file`）指向正确的位置。

连通性排查自下而上三层：面板在不在（`curl http://127.0.0.1:12702/api/ping`）→
插件加载了没（`N.E.K.O_Plugin_maaend_bridge_*.log` 里的 `Starting plugin`）→
令牌读到没（同一份日志里的 `[startup] base_url=... token_file=...`）。

---

## 已知限制

- 执行器同一时刻只跑一个任务；插件侧另有一把槽位锁，两次并发派活只有一次能成
- 任务选项**只能在插件面板里改**，`maaend_run` 故意不暴露 `options`
  ——46 个任务共 405 个选项，塞进工具参数面只会让模型乱填，而"不提交某个选项"
  本来就是"用它自己的默认值"的写法
- 截图端点没有接进插件：1280×720 的 PNG 约 900KB，而且和运行中的任务共用一把互斥锁
- 进度转播用的是 pipeline **节点名**（`SceneEnterMenuShop` 这类英文代号），
  这是执行器唯一暴露的粒度；插件按 `progress_min_interval_seconds` 分档节流，
  一次最多报 4 个新节点

---

## 开发

```bash
cd E:/n.e.k.o_plugin_maaend_bridge
uv run --project E:/NEKOdm/N.E.K.O pytest tests -q      # 152 个用例
uv run --project E:/NEKOdm/N.E.K.O ruff check .
```

还有一组**对真面板**的契约测试，默认跳过（`MAAEND_LIVE=1` 才跑）。
它只读不写——**绝不**碰 `/api/run` 与 `/api/prepare`，那两条会初始化 MaaFramework 并抢走游戏窗口焦点。
它验的是离线假件天生看不见的那一类事：面板**真实的字段名与端点**。
其中一条会把 46 个真实 label 逐个喂回匹配器，要求每个都解析回它自己、且互不撞车。

```bash
MAAEND_LIVE=1 MAAEND_PANEL_DIR=E:/MaaEnddm/MaaEnd-2/.workbuddy \
    uv run --project E:/NEKOdm/N.E.K.O pytest tests/test_live_panel.py -q
```

仓库根有一个 `conftest.py`，让 pytest 能在**独立仓库**里直接跑：注入最小宿主桩件，
把根 `__init__.py` 以合法包名加载，并登记成 `sys.modules["__init__"]`。
在 N.E.K.O. 源码树内它整块跳过，对宿主布局零影响。

分层（照抄 `mcbot_bridge` 的三件套）：

| 文件 | 角色 | 约束 |
|---|---|---|
| `__init__.py` | facade：SDK 胶水、装饰器、生命周期 | 不持有任务状态 |
| `service.py` | 状态与语义：任务槽、令牌轮换、轮询循环、cue 发射 | **不 import 插件 SDK**，可离线单测 |
| `client.py` | 纯传输：HTTP 方法 + 失败分类 | 不持有业务状态 |
| `prompts.py` | 所有模型可见 / 用户可见的文本，8 种语言 | 传输细节绝不进这里 |
| `surfaces/maaend.tsx` | hosted UI 面板（状态 / 任务表 / 选项编辑器） | 只能 `props.api.call` entry |
