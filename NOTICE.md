# 声明与第三方许可 / Notice & Third-Party Licences

## 用途声明（重要）

**本项目不是 MaaEnd 的官方项目**，与 MaaEnd 官方没有任何隶属、赞助或背书关系。

它的用途是**一次技术验证**：把「大模型驱动的角色应用」（N.E.K.O.）接到一个已有的
游戏自动化工具上，看看效果与边界。MaaEnd 在这件事里是**参考实现**——我们参考它的
架构与资源组织方式，并复用了它的资源层作为输入。把它开源出来，是让这个验证过程
**可复核**，而不是提供一套可用的 MaaEnd 客户端。

因此：

- **请勿把本项目当作 MaaEnd 的替代品、官方客户端或正式发行版使用。** 想用 MaaEnd，
  请去上游：<https://github.com/MaaEnd/MaaEnd>
- 资源层的版权属于 MaaEnd 项目，不属于本项目
- 本项目只对 `executor/maaend/` 做了**零改动**的复用（见下"AGPL 义务"）；
  所有游戏相关的逻辑、任务定义、模型与素材都来自上游

---

## 许可构成

本仓库是**混合许可**的。分界如下，请按子树判断：

| 子树 | 许可 | 说明 |
|---|---|---|
| `executor/maaend/` | **AGPL-3.0** | MaaEnd 的游戏资源层，**原样分发、未修改**。许可证原件：`executor/maaend/LICENSE` |
| `executor/maaend/maafw/`（仅此子目录） | **LGPL-3.0** | 例外：这 17 个原生 DLL 是 **MaaFramework** 的构建产物，随 MaaEnd 发行版一起分发，按 MaaFramework 自己的许可（原件：`executor/vendor/licenses/maafw-LICENSE.md`），不是 AGPL |
| `executor/override/` | **AGPL-3.0** | 其中 `.ts` 复制自 MXU（MaaEnd 的桌面宿主）；`pipeline_override.py` / `pretask_builder.py` / `data.json` 是那份 TypeScript 的**移植**——移植属衍生作品，随原许可 |
| `executor/vendor/` | 各自原许可 | 自带的 Python 运行时：CPython(PSF)、MaaFramework、numpy(BSD-3)、Pillow(MIT-CMU)、StrEnum(MIT)。许可原件在 `executor/vendor/licenses/` |
| `executor/panel_server.py`、`runner.py`、`packmeta.py`、`start_panel.bat`、探测脚本 | **MIT** | 本项目自己编写 |
| 插件本体（`__init__.py`、`service.py`、`client.py`、`launcher.py`、`prompts.py`、`surfaces/`、`i18n/`、`scripts/`、`tests/`） | **MIT** | 本项目自己编写 |

- **MIT 的全文**：`LICENSE`（只覆盖上表标为 MIT 的部分）
- **AGPL-3.0 的全文**：`LICENSE-AGPL-3.0.txt`，并在 `executor/override/LICENSE`
  再放一份（这样单独取走 `executor/override/` 的人也带着它）

**注意 AGPL 的传染性**：如果你把 `executor/maaend/` 或 `executor/override/` 与本项目
之外的代码**组合**分发，AGPL 的义务会延伸到那个组合作品。上面把用途限定为技术验证，
一部分原因就是这件事。这不是法律意见——要正式分发请自行确认，或直接联系上游。

---

## AGPL 义务的履行情况

以 AGPL-3.0 分发第三方内容时，需要做到几件事。本仓库的做法：

| 义务 | 本仓库 |
|---|---|
| 附许可证全文 | `executor/maaend/LICENSE`、`executor/override/LICENSE`、根目录 `LICENSE-AGPL-3.0.txt` |
| 保留版权声明 | 随上游原件一并保留（未改动任何上游文件） |
| 标明修改 | **对 `executor/maaend/` 没有任何修改**，逐目录指纹见 `executor/maaend/SOURCE.txt`；对 `executor/override/` 的改动是"从 TS 移植为 Python"，来源与每个 `.ts` 的 sha256 见 `executor/override/SOURCE.txt` |
| 提供对应源代码 | 本仓库即源代码；被复用内容的**上游**在下方链接 |
| 网络交互时向用户提供源代码（§13） | 本项目是本地运行的工具，不对外提供服务；若你要拿它对外提供服务，请自行履行 §13 |

上游：

- MaaEnd：<https://github.com/MaaEnd/MaaEnd>
- MXU（MaaEnd 的桌面宿主，`executor/override/*.ts` 的来源）：MaaEnd 项目下的 MXU
- MaaFramework：<https://github.com/MaaXYZ/MaaFramework>

`executor/maaend/SOURCE.txt` 记录了这份资源层的同步来源、时间与逐目录文件数/字节数；
`executor/override/SOURCE.txt` 记录了每个被复制的 MXU 文件、用途、行数与 sha256。
两份都可以用脚本重新核对（`scripts/sync_maaend_assets.py --check`）。

---

## Notice (English)

**This is NOT an official MaaEnd project.** It is not affiliated with, sponsored by,
or endorsed by the MaaEnd project or its authors.

It exists as a technical experiment: connecting an LLM-driven character application
(N.E.K.O.) to an existing game-automation tool, using **MaaEnd as the reference
implementation**. MaaEnd's asset layer is bundled **verbatim and unmodified** purely
as input to that experiment. It is published so the experiment can be inspected — it
is **not** offered as a usable MaaEnd client.

If you want MaaEnd itself, go upstream: <https://github.com/MaaEnd/MaaEnd>

**Licensing is mixed.** `executor/maaend/` and `executor/override/` are **AGPL-3.0**
(the latter because it contains MXU's TypeScript verbatim plus a Python port of it,
which is a derivative work). `executor/vendor/` carries its components' own licences
(PSF, BSD-3-Clause, MIT-CMU, MIT). Everything else in this repository is MIT.
See the table above; the AGPL text is in `LICENSE-AGPL-3.0.txt`.

AGPL's copyleft reaches combined works. If you distribute either AGPL subtree together
with code from outside this repository, the obligations apply to that combination.
This notice is not legal advice.
