# 语言包翻译任务书（交给子 agent 直接执行）

本目录下已有 **zh-CN / zh-TW / en** 三份完整语言包。还需要 **ja / ko / ru / es / pt** 五份。
下面整段可以原样作为 prompt 交给子 agent。

---

## 任务

为 N.E.K.O. 插件 `maaend_bridge` 补齐 5 个语言包，输出到本仓库的 `i18n/` 目录：

```
i18n/ja.json    日本語
i18n/ko.json    한국어
i18n/ru.json    Русский
i18n/es.json    Español
i18n/pt.json    Português
```

这 5 个文件**当前不存在**（旧的占位版已删，避免与新版键集合混淆），从零创建。
`python scripts/check_i18n.py` 现在会报 `[MISSING] no bundle yet for: ja, ko, ru, es, pt`。

## 输入

- **权威源**：`i18n/zh-CN.json`（64 个键，以此为基准）
- **英文参照**：`i18n/en.json`
- **术语参照（重要）**：`prompts.py` —— 里面已经有同一批概念的 8 语言译法，
  新语言包必须与之保持一致，否则界面用词和角色说的话会对不上。

先读这三个文件，再动手。

## 输出格式（硬要求）

- 纯 JSON 对象，**不要** markdown 代码围栏、不要任何解释文字
- UTF-8，**不带 BOM**
- 2 空格缩进，文件末尾一个换行
- 键名**逐个照抄**，一个不多一个不少（64 个）
- 字符串里的双引号按 JSON 规则转义（`\"`）

## 两条会导致线上事故的硬约束

**1. 占位符必须逐字保留，一个都不能少、不能改名、不能翻译。**

界面用 `interpolateI18n` 替占位符：名字对不上时它会把 `{task}` **原样留在屏幕上**，
用户看到的就是一句带花括号的话，而且不报任何错。带占位符的键一共 8 个：

| 键 | 占位符 |
|---|---|
| `ui.badResolution` | `{w}` `{h}` |
| `ui.changed` | `{n}` |
| `ui.manualLaunchHint` | `{path}` |
| `ui.noInterpreter` | `{reason}` |
| `ui.resultOk` | `{task}` `{elapsed}` |
| `ui.resultFailed` | `{task}` `{elapsed}` |
| `ui.resultIncomplete` | `{task}` `{elapsed}` |
| `ui.unsupportedOption` | `{type}` |

占位符周围的语序**可以**按目标语言调整（日语常要把 `{task}` 提到前面），
但花括号里的名字必须原样。

**2. 键名不译。** `plugin.name` 与 `plugin.description` 的**值**要翻译，键名不动。

## 语气与长度

这些文案出现在插件详情页的控制面板上，读者是玩家，不是工程师：

- 按钮/标签要短（目标语言里尽量 1–2 个词），**不要加句末句号**
- 说明性长句（`ui.stopHint`、`ui.notAdmin`、`ui.disconnectedHint`、
  `ui.badResolutionHint`、`ui.manualLaunchHint`、`ui.noShotHint`、
  `ui.prepareHint` 类）保持完整句，语气直接、口语化
- `ui.notAdmin` 是一条**会导致任务必然失败**的警告，要让人一眼看出严重性
- `ui.resultIncomplete`（"没跑完"）**不是失败**——执行器没判定为失败，
  可能是超时或中途停止。不要译成 "失败"/"failed" 的同义词。

## 术语表

| 中文 | 说明 |
|---|---|
| MaaEnd | 项目名，**不译** |
| MaaFramework | 依赖名，**不译** |
| UAC | **不译**（Windows 提权弹窗） |
| `start_panel.bat` / `panel_server.py` | 文件名，**不译**，不要加空格 |
| 终末地 / Endfield | 游戏名，用各语言的官方写法：ja `エンドフィールド`、ko `엔드필드`、ru/es/pt `Endfield` |
| 执行器 / executor | ja `実行器`、ko `실행기`、ru `исполнитель`、es `ejecutor`、pt `executor`（与 `prompts.py` 一致） |
| 控制面板后端 | "control panel backend"：ja `コントロールパネルのバックエンド`、ko `제어 패널 백엔드`、ru `серверная часть панели управления`、es `servidor del panel de control`、pt `backend do painel de controle` |
| 猫娘 | 角色自称：ja `ネコ娘`、ko `네코짱`、ru `неко-тян`、es/pt `neko-chan` |
| 任务 / task | 自动化任务，用游戏/软件语境下的常用词，不要译成 "job" |
| 分辨率 / resolution | 游戏窗口分辨率 |
| 管理员权限 | Windows administrator rights |

## 自验（必须做）

写完跑：

```bash
python scripts/check_i18n.py
```

它会逐个键比对 5 份新文件与 `i18n/zh-CN.json`：键集合、非空、占位符集合。
**必须看到 8 个 locale 全部通过**（`[OK] all 8 locales complete and consistent`）。
有 `[FAIL]` 就按提示改到没有为止；不要用改 `scripts/check_i18n.py` 的方式让检查通过
（那会把一个真问题藏起来，界面上的表现就是用户看到花括号或英文）。

> 顺带一提：`io` 之外不要去改 `scripts/`、`surfaces/` 或任何 `.py` 文件。
> 这个任务只产出 5 个 JSON。
