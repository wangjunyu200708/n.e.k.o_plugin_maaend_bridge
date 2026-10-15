# 待办：把两个前置条件也塞进插件（自带运行时）

**状态：未开工。** 断网前先把这个决定和已经查实的坑记下来。

## 目标

消除装机时的两个外部依赖，做到「装完插件即可用」：

| 前置条件 | 现状 | 目标 |
|---|---|---|
| 装了 MaaFramework 的 Python 3.11 | 要用户自己装 | 自带 |
| Node 22.6+ | 要用户自己装 | 自带（**已定，见下**） |

## 已定的决策

**Node 走「塞进去」**（2026-10-15 决定）。另外两条路评估过、否了：

- 换 QuickJS（pip 包 ~2MB，开发期用 esbuild 把 4 个 `.ts` 转成 `.js`，运行时用 QuickJS 执行）
  —— 查过源码只用 `?.` / `??`（ES2020，QuickJS 支持）、纯 ESM，技术上可行，但多一层
  构建产物 + 要重新对 golden 基线。省 85MB 换一层中间产物，不值。
- 不塞 —— 用户的目标就是消除前置条件，不符。

## 一处硬约束（查实，不是猜）

**宿主没有可复用的 python.exe。**

- `resources/bin/` 里唯一的 exe 是 `projectneko_server.exe`，而它是 **Nuitka 编译**的产物
  （`scripts/check_dist_arch.py:189` 提到 "macOS wraps the Nuitka output in …app"）
  —— Python 被编进那个 exe 里，它跑不了别的脚本
- 顶层只有 `N.E.K.O.exe`（Electron）
- `resources/bin/` 里有 `python311.dll` / `python3.dll`，但**没有 python.exe**

执行器必须是**独立的提权进程**（Seize 的硬约束），所以它必须自带解释器。

## 放哪儿：`executor/vendor/`，不是插件根的 `vendor/`

**这是个已验证的坑。** 宿主 `resources/bin/` 里**已经有 numpy**（还有 PIL、aiohttp、
cryptography 一整套）。而 `plugin/core/host.py:472` 的 `_prepare_child_plugin_vendor_path()`
会把插件根的 `vendor/` 插到**插件进程**的 `sys.path` 前面 —— 两份 numpy 版本不同就会
互相干扰，而插件进程里的宿主 SDK 代码也在用 numpy。

所以放 `executor/vendor/`，由 `launcher.py` 起执行器时**只给执行器进程**。
插件进程照旧走 HTTP 连它，一行 sys.path 都不碰。

（`neko-plugin sync` 默认同步到插件根 `vendor/`，所以这里不走 sync，或同步后移动。）

## 三块内容与来源

| 内容 | 体积 | 从哪来 |
|---|---|---|
| embeddable Python 3.11.9 | ~15 MB | `https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip`（需下载） |
| `site-packages/`：maa + numpy + strenum + maaagentbinary | ~85 MB | 从本机 `C:\Users\25672\AppData\Local\Python\pythoncore-3.11-64\Lib\site-packages\` 复制 |
| `node/`：node.exe | 87 MB | 从 `C:\Program Files\nodejs\node.exe` 复制（单文件自包含，需实测确认） |

maa 是 **纯 Python + DLL**（57 个文件，只有 `.dll` / `.py` / `.pyc`，**没有 `.pyd`**），
所以不吃 Python ABI 补丁版本；但 numpy 有 `.pyd`，是 cp311 绑定 —— 所以 embed 版必须是 3.11。

**许可证要一起带**：Python 的 LICENSE（PSF）、Node 的 LICENSE（MIT）。

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

这两条是现成的端到端证据，跑通即证明「零外部依赖」成立：

```bash
# 资源层加载（应输出 5809 节点、hash 8a3b256b99ec48e1）
executor/vendor/python/python.exe executor/smoke_resource.py

# override 编译器（应 5/5 一致；同时验证 vendor 的 node）
executor/vendor/python/python.exe executor/override/golden_test.py --check
```

`golden_test.py` 里 node 是探测式的（`NODE_CANDIDATES`），落地时要把
`executor/vendor/node/node.exe` 加到候选最前面 —— 否则它仍会去用系统的 Node，
那这条验收就证明不了 Node 也自带了。

## 落地后要改的地方

- `launcher.py`：`candidate_interpreters()` 把 `executor/vendor/python/python.exe`
  排在探测顺序**最前**；`launch_elevated` 传的 `--maaend-dir` 之外不用再传 PYTHONPATH
  （走 `_pth` 已经解决）
- `override/override_gen.py` 与 `override/golden_test.py` 的 `NODE_CANDIDATES`
- README 的「两个前置条件」一节改成「无需前置条件」+ 一节「自带运行时的来源与许可」
- `scripts/sync_maaend_assets.py` 的 `SKIP_REASONS` 里 `maafw` 那条现在是
  「pip 的 maa wheel 自带同一套 DLL」—— 落地后要改成「已由 vendor/site-packages 提供」
- `.gitignore`：不要 ignore `executor/vendor/`（要进仓库）；确认 `.gitattributes`
  对 `executor/vendor/**` 也走 `-text`（里面的二进制不能被行尾规范化碰）

## 体积后果（要提前想）

302 MiB → **~490 MiB**。`.neko-plugin` 包会是这个量级，从插件中心导入会明显变慢
（解压 + 校验 500MB）。如果要做市场分发，值得先评估「首次运行下载资源层」这类方案
——不过那是另一件事，不影响本次落地。
