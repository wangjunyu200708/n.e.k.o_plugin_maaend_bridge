# 自带的 Python 运行时

**这份目录让插件不再需要任何机器级安装。** 装完插件就能跑任务，不用先装 Python、
不用 `pip install`。约 **149 MiB / 1913 个文件**，是插件体积里第二大的东西
（最大的是 `../maaend/` 的资源层，293 MiB）。

重建/刷新用 `scripts/vendor_python_runtime.py`（本文件记录的是它的产物）。

---

## 为什么要自带

宿主没有可复用的解释器：`N.E.K.O.exe` 是 Electron，后端 `projectneko_server.exe`
是 PyInstaller/Nuitka 打包的产物——Python 被编进那个 exe 里，`sys.executable` 指向
bootloader，`sys.path` 是冻结的，既不能 `pip install --target` 也不能拿它跑脚本。

而执行器必须是**独立的提权进程**（Seize 的硬约束），所以它必须自带解释器。

---

## 组成

| 项 | 体积 | 来源 |
|---|---|---|
| `python/` | ~10.7 MiB | `python-3.11.9-embed-amd64.zip`（python.org）。含 `python.exe`、`python311.dll`、`python311.zip`（标准库）、`vcruntime140*.dll` ——**自带 MSVC 运行库**，所以不要求用户装 VC++ Redistributable |
| `site-packages/maa` | 59.6 MiB | MaaFramework 5.14.2 的 Python 绑定。**纯 `.py` + 17 个原生 DLL，没有 `.pyd`** → 不吃 Python ABI 补丁版本。大头是 `bin/onnxruntime_maa.dll`(18.1) + `bin/DirectML.dll`(17.7) + `bin/opencv_world4_maa.dll`(13.0) |
| `site-packages/numpy` + `numpy.libs` | 53.0 MiB | 2.4.4。**`numpy.libs` 是包的同级目录**，见下面的坑 |
| `site-packages/PIL` | 15.2 MiB | Pillow 12.1.1。`panel_server.screenshot_png()` 用它把截图落成 PNG |
| `site-packages/strenum` | ~0 | `maa` 的依赖 |
| `*.dist-info` | 小 | 留着让 `importlib.metadata` 能答话 |
| `licenses/` | 小 | 见下 |

### numpy 不是可选项

有人看到 `numpy` 只被用来把 `post_screencap()` 的结果转成 ndarray，就以为能砍掉省
53 MiB。**不能**：`maa` 包里有 7 个模块在**模块级** `import numpy`
（`define.py` / `controller.py` / `resource.py` / `tasker.py` / `buffer.py` /
`context.py` / `custom_recognition.py`），它是 MaaFramework Python 绑定的 import
期硬依赖。砍掉它就得改 wheel。

### DirectML 也不建议砍

`bin/DirectML.dll` 17.7 MiB 提供 GPU 推理，去掉后 ONNX 退回 CPU。这条流水线是
识别密集的（OCR、模板匹配、检测模型），而 17.7 MiB 只占整包 ~430 MiB 的 4%。
用 4% 换核心循环变慢不值。

---

## `python311._pth` —— 三个静默坑

embeddable 发行版带一个 `_pth`，它的存在会让 Python 进入 **isolated 模式**。
本副本的内容是：

```
python311.zip
.
..\site-packages
..\..
..\..\override
```

**坑一：zip 名不能带点号版本号。** 写成 `python3.11.9.zip` 会让标准库整个找不到，
`python.exe` 直接以 `<no Python frame>` 崩掉，连 `sys.version` 都打不出来 —— 没有
任何提示指向"文件名写错了"。

**坑二：`..\site-packages` 必须显式列出。** isolated 模式下 **`PYTHONPATH` 会被静默
忽略**，靠环境变量指路是无效的。（把路径写进 `_pth` 也比环境变量更好：确定性、不受
调用方环境影响。）

**坑三：脚本自己所在的目录也不在 `sys.path` 上。** 这一条最容易踩：isolated 模式下
`sys.path[0]` 是上面那个 zip，而列表里的 `.` 指的是**解释器所在目录**（`vendor/python`），
不是调用方的当前目录。所以最后两行专门把 `executor/` 与 `executor/override/` 放进去——
executor 的每个脚本都要 import 同目录或 `override/` 的兄弟模块。

在把这两行挪进来之前，脚本各自在文件中间调 `sys.path.insert` 解决这件事。那不是个好
写法：`sys.path.insert` 是语句，插在它后面的每个 import 都是 E402（"module level import
not at top of file"），而插件市场的 verify 工作流用 `--ignore-noqa` 跑 ruff，`# noqa`
在那里**不生效**，于是 41 个 E402 一个都压不掉。路径本来就属于运行时的配置，放在
`._pth` 里既解决了 lint，也让脚本从任意 cwd 都能跑。

**关于 `import site`：本副本特意没有加，因为实测不需要。** 判断依据是可查的：本
`site-packages` 下**没有任何 `.pth` 文件**（`find site-packages -maxdepth 1 -name
'*.pth'` 为空），所以 `site` 的处理对我们这套导入没有任何作用。验证方式是三条都过
（见下）——尤其第一条会真正加载 MaaFramework.dll、numpy 数组与 ONNX/opencv 的 DLL。
如果将来 vendor 进来的包**带了 `.pth`**，那才需要把它加回去。

---

## 刻意没有带的东西

| 项 | 为什么 |
|---|---|
| `MaaAgentBinary/`（86 文件 / 3 MiB） | 那是 **Android ADB 的 agent 二进制**（maatouch / minicap）。本桥只走 `Win32-Front`，永远不构造 ADB 控制器。`maa/controller.py` 里对它的引用（`"../MaaAgentBinary"`）在**缩进 8 的位置**，即 ADB 控制器类的方法内，惰性求值。**如果将来要支持 ADB，把 `MaaAgentBinary/` 放回 `site-packages/` 即可。** |
| `pytest` / `ruff` 等开发工具 | 运行时不需要；`executor/override/test_pipeline_override.py` 是纯 stdlib 的自研测试脚本，不依赖 pytest |

---

## 许可

`licenses/` 下随包附带，都是各自上游的原件：

| 包 | 许可 |
|---|---|
| CPython 3.11.9 | PSF License（`python/LICENSE.txt`） |
| MaaFramework（maafw） | 见 `licenses/maafw-*` |
| numpy | BSD-3-Clause（`licenses/numpy-*`） |
| Pillow | MIT-CMU（`licenses/pillow-*`） |
| StrEnum | MIT（`licenses/strenum-LICENSE`） |

---

## 验收（必须用**自带**的解释器跑，不是机器上那个）

三条一起过才算这份运行时是好的。第二条尤其重要——它是资源层真的加载起来了的证据
（MaaFramework.dll、numpy、ONNX/opencv 的 DLL 全部解析成功），而第三条覆盖截图那条
`smoke_resource` 碰不到的路径。

```bash
cd executor
vendor/python/python.exe smoke_resource.py maaend/resource   # 应 5809 节点、hash 8a3b256b99ec48e1
vendor/python/python.exe override/golden_test.py --check      # 应 11/11
vendor/python/python.exe override/test_pipeline_override.py   # 应全过

# 截图那条路（panel_server.screenshot_png 做的事：numpy -> PIL -> PNG）
vendor/python/python.exe -c "import io,numpy as np; from PIL import Image; \
b=io.BytesIO(); Image.fromarray(np.zeros((720,1280,4),dtype=np.uint8)).save(b,'PNG'); \
print('ok', len(b.getvalue()))"
```
