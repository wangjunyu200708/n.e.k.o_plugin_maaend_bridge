# 待办：把最后一个前置条件也塞进插件（自带 Python 运行时）

**状态：未开工。** 只剩一件事了——Node 那条已经由移植解决（见下）。

## 目标

消除装机时的外部依赖，做到「装完插件即可用」。原本有两个前置条件：

| 前置条件 | 现状 |
|---|---|
| Node 22.6+ | ✅ **已消除**：override 编译器移植成纯 Python，运行时零外部进程 |
| 装了 MaaFramework 的 Python 3.11 | ⬜ 待做：自带一个解释器 + 三方包 |

## Node 那条怎么没的

`override/` 原本直接跑 MXU 前端的 TypeScript（`node cli.ts`）。2026-10-16 移植为
`pipeline_override.py` + `pretask_builder.py` + `data.json`，等价性由
`golden_test.py --check`（11 份 TS 生成的冻结快照逐节点比对）与
`test_pipeline_override.py`（合成 interface 钉死 `??` vs `||`、`{}` truthy、
`$&` 这类语言语义陷阱）两层兜住。Node 只剩维护期用途，不进运行时。

> 原本这里记的是「Node 走塞进去（+87 MB）」。**那条决定已作废**——直接从
> 依赖里消失，而不是打包进去。少 87 MB，也少一层"用户得装对版本"的坑。

## 剩下的硬约束（已查实，不是猜）

**宿主没有可复用的 python.exe。**

- `resources/bin/` 里唯一的 exe 是 `projectneko_server.exe`，而它是 **Nuitka 编译**
  的产物（`scripts/check_dist_arch.py:189` 提到 "macOS wraps the Nuitka output in
  …app"）——Python 被编进那个 exe 里了，它跑不了别的脚本
- 顶层只有 `N.E.K.O.exe`（Electron）
- `resources/bin/` 里有 `python311.dll` / `python3.dll`，但**没有 python.exe**

执行器必须是**独立的提权进程**（Seize 的硬约束），所以它必须自带解释器。

## 放哪儿：`executor/vendor/`，不是插件根的 `vendor/`

**这是已验证的坑。** 宿主 `resources/bin/` 里**已经有 numpy**（还有 PIL、aiohttp、
cryptography 一整套）。而 `plugin/core/host.py:472` 的
`_prepare_child_plugin_vendor_path()` 会把插件根的 `vendor/` 插到**插件进程**的
`sys.path` 前面——两份 numpy 版本不同就会互相干扰，而插件进程里的宿主 SDK 代码
也在用 numpy。

所以放 `executor/vendor/`，由 `launcher.py` 起执行器时**只给执行器进程**。
插件进程照旧走 HTTP 连它，一行 sys.path 都不碰。

（`neko-plugin sync` 默认同步到插件根 `vendor/`，所以这里不走 sync，或同步后移动。）

## 两块内容与来源

| 内容 | 体积 | 从哪来 |
|---|---|---|
| embeddable Python 3.11.9 | ~15 MB | `https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip`（需下载） |
| `site-packages/`：maa + numpy + strenum + maaagentbinary | ~85 MB | 从本机 `C:\Users\25672\AppData\Local\Python\pythoncore-3.11-64\Lib\site-packages\` 复制 |

maa 是 **纯 Python + DLL**（57 个文件，只有 `.dll` / `.py` / `.pyc`，**没有 `.pyd`**），
所以不吃 Python ABI 补丁版本；但 numpy 有 `.pyd`，是 cp311 绑定 —— 所以 embed 版
必须是 3.11。

**许可证要一起带**：Python 的 LICENSE（PSF）。

## 关键坑：embeddable Python 默认是 isolated 的

embeddable 发行版带一个 `python311._pth`，它的存在会让 Python 进入 isolated 模式
——**`PYTHONPATH` 不生效**，`site` 也不自动 import。直接把 `site-packages` 指过去会
静默不生效（表现为 `import maa` 失败，但解释器本身跑得好好的）。

正解是改 `_pth`：

```
python311.zip
.
..\site-packages
```

这样 `site-packages` 就稳定在 `sys.path` 上，且不依赖环境变量（确定性更好）。

## 验收（必须用 vendor 环境跑，不是本机解释器）

两条现成的端到端证据，跑通即证明「零外部依赖」成立：

```bash
# 资源层加载（应 5809 节点、hash 8a3b256b99ec48e1）
executor/vendor/python/python.exe executor/smoke_resource.py

# override 编译器等价性（应 11/11；纯 Python，不需要 Node）
executor/vendor/python/python.exe executor/override/golden_test.py --check

# 语义边界
executor/vendor/python/python.exe executor/override/test_pipeline_override.py
```

## 落地后要改的地方

- `launcher.py`：`candidate_interpreters()` 把 `executor/vendor/python/python.exe`
  排在探测顺序**最前**
- README 的「一个前置条件」一节改成「无需前置条件」+ 一节「自带运行时的来源与许可」
- `scripts/sync_maaend_assets.py` 的 `SKIP_REASONS` 里 `maafw` 那条现在是
  「pip 的 maa wheel 自带同一套 DLL」——落地后要改成「已由 vendor/site-packages 提供」
- `.gitignore`：不要 ignore `executor/vendor/`（要进仓库）；确认 `.gitattributes`
  对 `executor/vendor/**` 也走 `-text`（里面的二进制不能被行尾规范化碰）

## 体积后果（要提前想）

302 MiB → **~402 MiB**（Node 那 87 MB 不再需要）。`.neko-plugin` 包会是这个量级，
从插件中心导入会明显变慢（解压 + 校验 400MB）。如果要做市场分发，值得先评估
「首次运行下载资源层」这类方案——不过那是另一件事，不影响本次落地。
