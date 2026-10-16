# maaend_bridge — MaaEnd 终末地执行器桥接

> ## ⚠️ 用途声明
>
> **这不是 MaaEnd 的官方项目**，与 MaaEnd 官方无隶属或背书关系。
> 它的用途是**一次技术验证**：把大模型驱动的角色应用（N.E.K.O.）接到一个已有的
> 游戏自动化工具上，**以 MaaEnd 为参考实现**。MaaEnd 的资源层在这里是**原样复用**的
> 输入。请勿把它当作 MaaEnd 的替代品或正式客户端。
> 想用 MaaEnd 请去上游：<https://github.com/MaaEnd/MaaEnd>
>
> 许可是**混合**的（`executor/maaend/` 与 `executor/override/` 是 **AGPL-3.0**，
> 其余为 MIT）。逐子树的划分、AGPL 义务的履行情况，见 **[NOTICE.md](NOTICE.md)**。

把 **MaaEnd 自建执行器**（用 Python + MaaFramework 直接驱动《明日方舟：终末地》，
绕开 MXU 宿主）整个带进 N.E.K.O.：猫娘能指挥它跑日常、刷信用点、收菜，
并在任务进行中实时转播"跑到哪一步了"。

**执行器、资源层与 Python 运行时都随插件分发**（`executor/`，前置条件为零），
装完就能跑，不用再去别处找 MaaEnd、也不用先装 Python。

这是 `mcbot_bridge`（接本地 mcbot，REST + SSE）与 `game_agent_minecraft`（接 mc-agent，裸 WebSocket）
的同形兄弟，接的是第三条线，也是第三种传输。

---

## 无需前置条件

装完插件就能跑。**不用装 Python，不用 pip 装 MaaFramework，不用装 Node。**

插件自带三样东西：

| 自带 | 体积 | 为什么 |
|---|---|---|
| **Python 3.11.9 解释器** | ~10.7 MB | 宿主没有可复用的解释器：`N.E.K.O.exe` 是 Electron，后端是打包过的 exe（Python 编在里面），`sys.executable` 指向 bootloader、`sys.path` 冻结。而执行器必须是独立的**提权**进程（Seize 的硬约束），所以它必须自带解释器 |
| **MaaFramework + numpy + Pillow** | ~139 MB | 见 `executor/vendor/README.md`（含每个包的来源、许可，以及 `_pth` 的两个静默坑） |
| **override 编译器（纯 Python）** | 小 | 原本跑 MXU 的 TypeScript，现在是 Python 移植；等价性由 11 份冻结快照 + 语义边界测试保证 |

顺带一个好处：这三个的版本是**我们的**，不是用户碰巧装的那个。

解释器仍然可覆盖，优先级是 **显式 > 自带 > 记录过 > 环境里的**：

1. `executor_python` 设置项 / `NEKO_MAAEND_PYTHON` 环境变量
2. 自带的 `executor/vendor/python/python.exe`
3. `executor/python_path.txt`（历次成功探测记下来的；**排在自带之后**是有意的——从旧版升上来的机器那个文件指向系统 Python，让它压过自带的就等于自带白带了）
4. 常见安装路径 → `py -3.11` → `PATH` 里的 `python`

探测的判据始终是 `<python> -c "import maa"` 能过——解释器存在与否说明不了它装没装 MaaFramework。

解释器会按 `NEKO_MAAEND_PYTHON` → 插件配置 → `executor/python_path.txt` →
常见安装路径 → `py -3.11` → `python` 的顺序探测，**逐个跑 `import maa` 验证**。
探到的那个会写回 `executor/python_path.txt`，这样插件面板和手动 `start_panel.bat`
用的是同一个解释器。

---

## 权限：为什么必须有一个独立进程

**Seize（`SendInput`）只能从提权进程送到游戏窗口。** 非提权时表现极具迷惑性：
日志里 28 次 Click、21 次按键，游戏毫无反应——连接成功、调用不报错、只是没效果。

而插件跑在 N.E.K.O. 的插件子进程里，N.E.K.O. 本身没有提权，所以**插件永远不可能
是那个驱动游戏的进程**。它能做的只有"请 Windows 起一个"：

```
插件面板 [启动执行器]
   → ShellExecuteW(None, "runas", <python>, "panel_server.py --port 12702")
   → UAC 弹窗（用户点「是」）
   → 提权面板进程起来，写 panel_token.txt，监听 127.0.0.1:12702
   → 插件按 127.0.0.1:12702 用 HTTP 连它
```

`executor/` 因此是**一份独立的脚本目录，不是被 import 的包**——插件只通过命令行和
HTTP 与它交互。这样边界干净，也能整目录替换成更新的执行器。

手动后备路径同样存在：双击 `executor/start_panel.bat`（它会自己请求提权）。

---

## 它连的是什么

执行器暴露一个**只监听 127.0.0.1** 的 HTTP API：

| 面 | 端点 | 说明 |
|---|---|---|
| 探活 | `GET /api/ping` | **唯一免鉴权**端点，返回 `{"ok":true,"app":"maaend-panel"}` |
| 状态 | `GET /api/status` | `state`（`idle`/`ready`/`busy`）、当前任务与**实时节点列表**、`last_result`、分辨率判定、是否提权 |
| 任务 | `GET /api/tasks` | 46 个任务（已本地化，label 带 emoji） |
| | `GET /api/options?task=<name>` | 某任务的递归选项树 |
| | `POST /api/run` | `{task, timeout, options}` → `202` + status；失败 `400` |
| | `POST /api/stop` | → `{"ok":bool}`；**没任务在跑时也是 200** |
| | `POST /api/prepare` | 初始化 Resource/Controller/Agents（3~5 秒） |
| 画面 | `GET /api/screenshot` | 1280×720 PNG |
| 日志 | `GET /api/logs` | SSE 日志总线（`?since=<id>`） |

鉴权是 `X-Auth-Token` 头，令牌由面板**每次启动**重新生成，写到
`executor/panel_token.txt`。面板另外把 `Host`（以及存在时的 `Origin`）钉死在
`127.0.0.1` / `localhost` 上——**浏览器的插件面板不能直连它**（iframe 的 origin 不是
loopback），必须经插件后端代理，这也正好让令牌不落进浏览器。

### 令牌即"执行器实例身份"

这份 API 里**没有会话 id**，但"令牌变了"就是"面板进程重启过"的**证明**——
而一个已经不在的进程没法交代它手上那个任务后来怎么样了。所以：

- 令牌**每次请求现读**，从不缓存，于是"插件装好时面板还没启动过"这个引导问题自然消失。
- 任务进行中令牌变了 → 这次运行的结果标为 **未知**（既不是成功也不是失败），
  未答的问题作废，**不续跑、不重投**。那个任务可能已经在游戏里做完了。

---

## 两条通道的分工

插件对外的能力分给两个调用者，二者**互斥可见**（这是代码决定的，不是约定）：

| 能力 | 通道 | 谁调用 | 为什么 |
|---|---|---|---|
| 派活、停止 | `@llm_tool` `maaend_run` / `maaend_stop` | **对话 LLM** 直接调 | 「帮我把日常跑了」得由正在跟用户对话的那个模型当场拍板，且需要它的类型化参数 |
| 状态、任务表、选项树 | `@plugin_entry` `maaend_status` / `maaend_list_tasks` / `maaend_task_options` | analyzer、插件面板、CLI | 这是事实与设置，不该占对话模型的工具槽位 |
| 面板上的按钮 | `@plugin_entry`（带 `@ui.action`） | 插件面板 | hosted 面板只能 `props.api.call` 一个 entry，够不到 `@llm_tool` |

`maaend_start_task` 与 `maaend_run` 是同一次派活在两个渠道上的入口——因为
`@plugin_entry` 永远进不了 main_server 的工具注册表，它对对话模型**零成本**。

第三条是**反向**通道：执行器的进度被翻译成 `push_message` cue，于是猫娘能主动开口，
不需要任何人轮询。

---

## 界面

控制面板在**插件详情页的「面板」标签**（`[[plugin.ui.panel]]`）。布局照 MXU 2.7.1
的桌面视图：左栏任务列表，右栏实时画面 + 运行日志。

两处**特意没有**照抄 MXU，因为本后端没有对应的概念可以挂：

- **标签栏**：MXU 的 tab 是"保存的配置"（任务选择 + 每任务选项值 + 控制器 + 资源 +
  预动作 + 计划）。这里只有一个执行器、一次一个任务。
- **pretask / pre-action / 任务三段固定分区**：那个划分来自 MXU 的队列式后端（一次提交
  一批）。这里每个任务各自可跑，而目录本身带 `group` 字段，所以按 `group` 分组。

---

## 为什么不用 SSE

执行器确实有 `/api/logs`（SSE），但**任务状态**从它读是不对的：它是**进程内**的原始
日志总线，id 只在本进程内单调，**面板一重启就从 1 重来且没有任何会话标识**。

拿一个陈旧的**高位游标**去"补发 id 大于游标"的帧，面对重启后的新会话是个**空集**：
新会话的 1..N 帧会**整段静默不可见**，而且不报任何错。要正确消费它，就得自己再发明
一套实例身份与游标重置——为的是一个比 `/api/status` **更差**的信号。

所以：**任务状态轮询 `/api/status`**（它不取锁，不像 `/api/screenshot`），
而日志流只用来**显示**（后端镜像进环形缓冲，面板轮询增量）。这个分工是有意的：
显示层的重复无害，任务状态层的重复会让人重投一个已经做完的任务。

---

## 一个任务的三种结局

`last_result` 给的是两个布尔值，而它们张成**三个**结局。只有第三个是"没做成"：

| `succeeded` | `failed` | 插件怎么讲 |
|---|---|---|
| `true` | — | 跑完了，用时 X |
| — | `true` | 失败了 |
| 都不是 | | **没跑完就停了**——可能超时（面板按 `timeout` 主动停），可能被中途停下，可能窗口没了 |

第二种和第三种都**不是成功**，也都不该被说成对方。
另外：**用户主动喊停的那次运行不播报**——那是模型自己发起的事，播报只会诱发对一件
已经放弃的工作的**重新派发**。

---

## 任务名怎么对上号

用户说的是人话，执行器要的是代号。四个 pass，强的证据优先：代号精确 → 显示名精确 →
互相包含 → **模糊重合**（因为真实 label 不是真实句子的子串：「日常奖励领取」vs
用户说的「帮我领取日常奖励」）。打分按查询长度归一化，字符 bigram 为主、去重字符
重合打折 ×0.75 为辅。

两条保守规则，因为**派错任务的代价远大于再问一次**：

- **两个字以下是证据不足**：单个共享的汉字是巧合不是名字（「包」和「存放背包」）。
- **赢家必须明显领先**：次名在首名 1.25 倍以内就算**歧义**，两个一起列出来。
  实测「关掉游戏」会同时命中「关闭游戏（安卓端）」和「关闭游戏（PC）」——这正是对的答案。

---

## 硬性前置条件

| 条件 | 为什么 | 不满足时插件怎么办 |
|---|---|---|
| **管理员权限** | Seize 发键鼠需要提权 | **直接拒绝派活**并说明。面板自己不会拒绝——它会照常"跑完"，游戏里什么都没发生，正是最坏的错法 |
| **16:9 分辨率** | go-service 硬性校验，不达标 17ms 后强制停任务 | 用面板报的 `w`/`h` **自己组织一句话**拒绝，不转发面板的运维散文 |
| **游戏窗口在前台** | `SendInput` 只发前台窗口 | 只**提醒**：面板会在 `post_task` 前抢一次焦点、运行期间每 5 秒复查 |
| **执行器面板在运行** | 它是个独立进程 | 如实说"连不上"，并给出「启动执行器」与 `start_panel.bat` 两条路 |

---

## 自带的东西

```
executor/                     自建执行器（独立进程，不被 import）
├── panel_server.py           HTTP API + SSE + 日志总线
├── runner.py                 资源/控制器/agent/任务
├── packmeta.py               读资源层的自描述（窗口/控制器/项目身份），供 runner 用
├── run_elevated.py           命令行跑单个任务的提权包装
├── start_panel.bat           手动启动器（自动请求提权）
├── vendor/                   ★ 自带的 Python 运行时（149 MiB，见 vendor/README.md）
│   ├── python/               embeddable CPython 3.11.9 + python311._pth
│   ├── site-packages/        maa / numpy(+numpy.libs) / strenum / PIL
│   └── licenses/             上述各包与 CPython 的许可原件
├── override/                 option → pipeline_override 编译器
│   ├── pipeline_override.py    ★ 运行时实现（从 MXU 的 TS 移植）
│   ├── pretask_builder.py      ★ pretask 启动参数（同上）
│   ├── data.json               ★ 从 MXU 导出的数据表（hotkey 映射等）
│   ├── override_gen.py         对外 API 外壳（gen_override_object / merge_overrides）
│   ├── build_interface.py      JSONC 解析 + 展开 68 条 import
│   ├── golden_test.py          黄金测试：--check 跑 Python 对快照，--compare/--update 用 Node 当对照组
│   ├── test_pipeline_override.py  合成 interface 钉死语义边界（golden 够不到的部分）
│   ├── golden/*.json           11 份冻结快照（由 TS 版生成，Python 版必须逐节点吻合）
│   └── *.ts                    MXU 原样复制的 TS，**不参与运行时**：维护期当对照组与数据来源
│                                （来源与 sha256 见 override/SOURCE.txt）
├── maaend/                   资源层（AGPL-3.0，见下）
│   ├── interface.json        任务/选项清单（JSONC，运行时展开）
│   ├── tasks/ (71)           46 个任务定义
│   ├── resource/ (2123)      pipeline（5809 节点）+ 图片 + ONNX 模型
│   ├── agent/ (3)            go-service.exe + cpp-algo.exe（47 自定义识别 / 85 自定义动作）
│   ├── locales/ (46)         界面本地化，面板的 Translator 读它
│   ├── data/ (19)            部分任务要用的地图/表格数据
│   └── SOURCE.txt            来源与逐目录指纹（由脚本生成）
└── 冒烟脚本                  api_smoke / smoke_resource / smoke_override / test_prepare / probe_*
```

**没有带**：`MaaEnd.exe`（MXU 宿主，本执行器存在的意义就是绕开它）、`maafw/`
（pip 的 `maa` wheel 自带同一套 DLL，已验证）、`debug/` `cache/` `config/`
（上游作者的运行残留）、`resource_adb/` 与各平台 `resource_*`（本桥只走 Win32-Front）、
以及 `run_logs/`、截图、`panel_token.txt` 等运行产物。

### 许可

`executor/maaend/` 是 **MaaEnd 的资源层，AGPL-3.0**，**原样分发、未修改**，
许可证随附在 `executor/maaend/LICENSE`。`override/` 下的 4 个 `.ts` 从 **MXU** 复制，
来源与文件 sha256 记在 `executor/override/SOURCE.txt`。分发前请自行确认这两个上游的
许可条款是否满足你的发布方式（AGPL 对网络服务同样有要求）。

`executor/maaend/SOURCE.txt` 记录了这次同步的来源、时间、逐目录文件数/字节数与
`interface.json` 的 sha256。重新校验：

```bash
python scripts/sync_maaend_assets.py <MaaEnd 发行版路径> --check
python scripts/sync_maaend_assets.py --report          # 只看当前带了什么
```

---

## 体积

工作区 **456 MiB**，两块大的都是"自带的第三方东西"：

| | 体积 |
|---|---|
| `executor/maaend/` 游戏资源层（2265 文件，AGPL-3.0） | 299 MiB |
| `executor/vendor/` Python 运行时（1914 文件） | 154 MiB |

最大单文件仍是 93.8 MiB（`resource/model/map/navmesh/base.nav.gz`）——**低于
GitHub 的 100 MiB 硬限制**（但高于 50 MiB 的警告线）。再加更大的模型前先看这条线。

三个后果：

- **首次 `git clone` 与 CI checkout 会明显变慢**
- **`.neko-plugin` 包会是 ~450 MiB**，从插件中心导入要解压 + 校验这个量级
- 如果要走市场分发，值得先评估「首次运行再下载资源层」这类方案；不过那是另一件事

想减体积的话，按"代价从低到高"：

1. **不随包资源层** —— 把 `maaend_dir` 指向用户自己的目录（插件其余部分照常工作）
2. **不随包运行时** —— 删 `executor/vendor/`，退回"用户得自己装 Python + MaaFw"
3. 裁 `maa/bin/DirectML.dll`（-17.7 MiB）—— OCR 退回 CPU 推理，**不建议**
4. 砍 numpy（-53 MiB）—— **做不到**，`maa` 有 7 个模块在模块级 `import numpy`

---

## 配置

`plugin.toml` 的 `[maaend]` 节（打包版请改 `profiles/<name>.toml`，`config/plugin.toml` 改了不生效）：

| 键 | 默认 | 说明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:12702` | 必须是 `127.0.0.1`/`localhost`，否则撞面板的 Host 白名单 |
| `executor_python` | 空 | 装了 MaaFramework 的解释器；留空则探测 |
| `maaend_dir` | 空 | 资源层目录；留空用自带的 `executor/maaend` |
| `panel_dir` / `token_file` | 空 | 令牌位置；留空即 `executor/panel_token.txt` |
| `default_timeout_minutes` | `30` | 派活时发给面板的任务时长上限。**面板自己的默认是 5 分钟**，到点会**主动停任务**——对日常/刷材料偏短 |
| `max_timeout_minutes` | `120` | 模型能要求的单任务时长上限 |
| `task_wait_timeout_seconds` | `3600` | 等它结束的预算；总是**大于**面板自己的截止时间，否则我们会抢在面板报结果之前先放手 |
| `poll_interval_seconds` | `2.0` | 任务进行中的轮询间隔 |
| `progress_min_interval_seconds` | `10` | 进度推进 LLM 上下文的最小间隔 |

---

## 启用

1. 在插件面板里启动本插件（`auto_start = false`）。面板上点**「启动执行器」**，
   在 UAC 弹窗点「是」——或双击 `executor/start_panel.bat`。
2. 进游戏，把分辨率设成 16:9。
3. 如果面板说找不到解释器，把它指到装了 MaaFramework 的那个 Python 3.11。

连通性排查自下而上三层：面板在不在（`curl http://127.0.0.1:12702/api/ping`）→
插件加载了没（`N.E.K.O_Plugin_maaend_bridge_*.log` 里的 `Starting plugin`）→
解释器与令牌读到了没（面板上的诊断行，或同一份日志）。

---

## 已知限制

- 执行器同一时刻只跑一个任务；插件侧另有一把槽位锁，两次并发派活只有一次能成
- 任务选项**只能在插件面板里改**，`maaend_run` 故意不暴露 `options`
  ——46 个任务共 405 个选项，塞进工具参数面只会让模型乱填，而"不提交某个选项"
  本来就是"用它自己的默认值"的写法
- 进度转播用的是 pipeline **节点名**（`SceneEnterMenuShop` 这类英文代号），
  这是执行器唯一暴露的粒度；按 `progress_min_interval_seconds` 分档节流，一次最多报 4 个
- 日志面板是**镜像**，不是直连：执行器重启后缓冲从新开始（面板会收到 `reset`）
- 中文只有 zh-CN / zh-TW / en 三份语言包写完，其余 5 份待补
  （`i18n/TRANSLATION-BRIEF.md` 是可直接交给子 agent 的任务书）

---

## 开发

```bash
cd E:/n.e.k.o_plugin_maaend_bridge
uv run --project E:/NEKOdm/N.E.K.O pytest tests -q     # 203 passed
uv run --project E:/NEKOdm/N.E.K.O ruff check .        # executor/ 与 vendor/ 有意排除
python scripts/check_i18n.py                           # 语言包键集合与占位符一致性
python scripts/sync_maaend_assets.py --report          # 资源层带了什么
python scripts/vendor_python_runtime.py --check        # 自带运行时是否完整
```

后两个脚本同时也是**重建**入口（`sync_maaend_assets.py <发行版路径>` /
`vendor_python_runtime.py --python-zip <zip> --site-packages <路径>`）。改完二者
都要用**自带的解释器**跑一遍 `executor/vendor/README.md` 里那三条验收——
注意 `smoke_resource` 不碰截图那条路，所以它单独通过不代表运行时是完整的
（Pillow 就是这么漏掉过一次；现在 `tests/test_launcher.py` 会从源码推导出
执行器真正 import 的三方包并逐个核对）。

还有一组**对真面板**的契约测试，默认跳过（`MAAEND_LIVE=1` 才跑）。
它只读不写——**绝不**碰 `/api/run` 与 `/api/prepare`，那两条会初始化 MaaFramework
并抢走游戏窗口焦点。它验的是离线假件天生看不见的那一类事：面板**真实的字段名与端点**。
其中一条会把 46 个真实 label 逐个喂回匹配器，要求每个都解析回它自己、且互不撞车。

```bash
MAAEND_LIVE=1 MAAEND_PANEL_DIR=E:/n.e.k.o_plugin_maaend_bridge/executor \
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
| `launcher.py` | 找解释器、提权拉起执行器、诊断 | 不 import 执行器代码 |
| `prompts.py` | 模型可见文本，8 种语言 | 传输细节绝不进这里 |
| `surfaces/maaend.tsx` | hosted UI 面板 | 只能 `props.api.call` entry |
| `i18n/*.json` | 界面文案 | `scripts/check_i18n.py` 把关 |
