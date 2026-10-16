# 已完成：自带 Python 运行时

**状态：已落地（2026-10-16）。** 插件现在**无需任何前置条件**——不用装 Python、
不用 pip 装 MaaFramework、不用装 Node。

重建/校验入口：`scripts/vendor_python_runtime.py`（`--check` / `--report` / 不带参数则构建）。
每样东西的来源、许可与取舍写在 `executor/vendor/README.md`。

---

## 实际结果

| | 计划估的 | 实际 |
|---|---|---|
| 体积 | ~100 MiB | **149.3 MiB / 1914 文件** |
| 组成 | Python + maa + numpy + strenum | 以上 **+ PIL**（漏计了），numpy 2.4.4 也比预估大 |

插件工作区合计 **456 MiB**（资源层 299 + 运行时 154），`.neko-plugin` 同量级。

---

## 与计划的四处偏差（这才是以后有用的部分）

### 1. `import site` 实测**不需要**（计划里说要）

计划照搬了探测阶段的结论。实际把 `_pth` 写成只列路径、不加 `import site`，三条验收
全过——包括真正加载 MaaFramework.dll 与 numpy 数组的那条。

判断依据是可查的，不是碰运气：**本 `site-packages` 下没有任何 `.pth` 文件**，
所以 `site` 的处理对我们这套导入无事可做。（当初得出"必须加"很可能是与 zip 名
那个坑一起改的，而真正起作用的是 zip 名。）

**如果将来 vendor 进来的包带了 `.pth`，就要把它加回去。** 判据：
`find executor/vendor/site-packages -maxdepth 1 -name '*.pth'` 非空。

### 2. PIL 是计划的漏项，而**两条验收都碰不到它**

`panel_server.screenshot_png()` 用 `PIL.Image` 把截图落成 PNG。计划里没列它，是因为
列清单时想的是"驱动 MaaFramework 需要什么"，而截图那条路不在其中。

危险在于它**不会被任何一条验收暴露**：`smoke_resource` 只加载资源层，`Resource()`
实例化也只走 MaaFramework。缺 Pillow 的表现是——所有验收全绿，而第一次有人要截图
时拿到一个 503。

现在由 `tests/test_launcher.py::test_every_third_party_import_the_executor_needs_is_vendored`
兜住：它**从源码 AST 推导**执行器真正 import 的三方包，再逐个核对是否在 vendor 里。
不维护清单，就不会有清单过期。

### 3. `MaaAgentBinary/` 刻意不带（3 MiB）

它是 **Android ADB 的 agent 二进制**（maatouch / minicap）。本桥只走 `Win32-Front`，
永远不构造 ADB 控制器；`maa/controller.py` 里对它的引用在 ADB 控制器类的方法内，
惰性求值。要支持 ADB 时放回 `site-packages/` 即可。

### 4. numpy 砍不掉（否掉了一条体积方案）

"numpy 只用来把截图转 ndarray，砍掉省 53 MiB"这个前提不成立：`maa` 包里有 **7 个
模块在模块级 `import numpy`**（`define.py` / `controller.py` / `resource.py` /
`tasker.py` / `buffer.py` / `context.py` / `custom_recognition.py`），它是
MaaFramework Python 绑定的 import 期硬依赖。砍它要改 wheel。

DirectML（-17.7 MiB）同理不建议：它占整包 4%，换的是识别密集流水线的 GPU 推理。

---

## 验收（必须用**自带**的解释器跑）

```bash
cd executor
vendor/python/python.exe smoke_resource.py maaend/resource   # 应 5809 节点、hash 8a3b256b99ec48e1
vendor/python/python.exe override/golden_test.py --check      # 应 11/11
vendor/python/python.exe override/test_pipeline_override.py   # 应全过
vendor/python/python.exe -c "import io,numpy as np; from PIL import Image; \
b=io.BytesIO(); Image.fromarray(np.zeros((720,1280,4),dtype=np.uint8)).save(b,'PNG'); \
print('ok', len(b.getvalue()))"                                # 截图那条路
```

第一条是最有分量的：5809 节点 + 与系统解释器一致的 resource hash，意味着
MaaFramework.dll、numpy、ONNX/opencv 的 DLL 全部解析成功。

**仍需真机验证的部分**：以上都不碰游戏。提权、窗口置前、Seize 输入这条链路
（也就是 `runner.setup_controller()` 之后的一切）只能进游戏才验得了。

---

## 已查实但未使用的两条备选（留档）

- **不能复用宿主解释器**：`N.E.K.O.exe` 是 Electron；后端 `projectneko_server.exe`
  是打包产物（PyInstaller/Nuitka），Python 被编进那个 exe，`sys.executable` 指向
  bootloader、`sys.path` 冻结，既不能 `pip install --target` 也不能拿它跑脚本。
  执行器又必须是独立提权进程，所以自带解释器是唯一解。
- **资源层可以完全不随包**：`maaend_dir` 配置项优先于自带的 `executor/maaend/`。
  这是"随时撤掉某个游戏的资产"最直接的落地方式——插件其余部分照常工作，只是
  没有资源层时如实说明并不让派活。
