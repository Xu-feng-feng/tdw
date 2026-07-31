# TDW 可交互住宅与多通道采集

这个子项目把 TDW 住宅示例扩展成了可直接配置和采集的数据流程：

- 第一视角键鼠漫游；
- 全屋顶视 RGB、深度和实例分割；
- JSON 控制家具模型、位置、朝向、缩放及物理参数；
- 保存颜色到对象 ID 的场景元数据；
- 从实例分割图自动生成对象框、标签和 JSON；
- 可选择 TDW 预设家具布局，或只加载空住宅结构。

## 1. 环境

TDW 1.13.0 仍依赖 Python 3.11 中的旧标准库接口，**不能使用 Python 3.12**；同时需要保留 `pkg_resources`，因此项目固定了 `setuptools==80.9.0`。

使用 uv：

```bash
uv python install 3.11
uv sync
```

或使用 pip：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

TDW 首次启动时会下载与 Python 包版本匹配的 Unity build。交互模式需要带图形桌面的 x86-64 环境；远程服务器应先配置 X11、xpra 或 VNC。只有终端、没有显示服务器时，可以运行配置校验和单元测试，但不能完成真实渲染。

## 2. AssetBundle 缓存与黑屏排查

Unity 2020 自带的旧 TLS 实现可能无法验证 TDW 公共 S3 证书，`Player.log` 中会反复出现：

```text
Curl error 60: Cert verify failed: UNITYTLS_X509VERIFY_FLAG_USER_ERROR1
```

启动时 Python 打印的 `pkg_resources is deprecated` 只是 TDW 1.13 旧依赖的弃用警告，项目已经固定兼容的 setuptools 版本；它不会导致黑屏。

这时场景仍在第一批家具资源处等待，相机尚未创建，因此 Unity 窗口会保持黑色。项目默认使用安全的 `cache` 模式：先由 Python 使用系统证书通过 HTTPS 下载本次场景需要的 AssetBundle，再把命令中的 URL 改为本地 `file:///` 地址，全部准备好之后才启动 Unity。终端会显示 `[当前/总数]` 下载进度；首次运行 `1a/0` 约需下载 370 MiB，后续运行直接复用缓存。

默认缓存目录为：

```text
$XDG_CACHE_HOME/tdw_custom_house/assets
```

未设置 `XDG_CACHE_HOME` 时使用 `~/.cache/tdw_custom_house/assets`。可以覆盖目录：

```bash
uv run tdw-house --asset-cache-dir /data/cache/tdw-assets
```

另外提供两个诊断/兼容模式：

```bash
# 让 Unity 直接使用原始 HTTPS；旧 UnityTLS 环境可能再次长时间重试
uv run tdw-house --asset-mode https

# 仅作为最后的临时手段；明文 HTTP 不提供传输安全
uv run tdw-house --asset-mode http
```

`file:///` 路径由 Unity 进程读取。如果 `--connect-existing` 连接的是另一台主机或未共享文件系统的容器，需要把缓存目录以相同绝对路径挂载给 Unity；否则应改用 `--asset-mode https`（或仅在受控网络临时使用 `http`）。缓存模式只处理 TDW 公共域名，私有库和第三方 URL 保持原样。

如果 TLS 阶段已经结束但窗口仍极慢，检查 Unity 是否落到了 CPU 软件渲染：

```bash
rg 'Renderer:|Curl error 60' ~/.config/unity3d/MIT/TDW/Player.log
nvidia-smi
```

日志中的 `Renderer: llvmpipe` 表示当前 X11/远程桌面没有把 NVIDIA GPU 暴露给 Unity；即使机器安装了 GPU，Unity 仍会在 CPU 上渲染。应改在 GPU-backed X 会话中启动，或正确配置 VirtualGL + VNC、xpra/NoMachine 等远程图形方案。临时排查时可降低分辨率并只采顶视相机，以减少软件渲染负担：

```bash
uv run tdw-house --width 640 --height 360 --no-ego-capture
```

本机 XRDP 日志如果同时出现 `renderD128 open failed` 和 `swrast`，说明登录用户无权打开 GPU render node。当前机器的最小修复是由管理员执行：

```bash
sudo usermod -aG render xwf
```

随后必须**完全注销 xwf 的 XRDP 会话并重新登录**，已有的 Xorg 会话不会自动获得新组权限。重新运行后，`Player.log` 的 `Renderer:` 应显示 `NVIDIA GeForce RTX 3090`，而不是 `llvmpipe`；`nvidia-smi` 中也应出现 `TDW.x86_64`。

修改源码不会改变已经运行的 Python/Unity 进程；应用修复前先在原终端按 `Ctrl+C` 或正常关闭旧窗口，再重新运行。

## 3. 先验证家具配置

这个命令不会导入 TDW，也不会启动 Unity：

```bash
uv run python tdw_custom_house/interactive_house.py --validate-config
```

默认配置中的两件示例家具均为 `"enabled": false`，因此不会意外与预设家具重叠。调整坐标并启用后再运行场景。

## 4. 第一视角漫游

```bash
uv run python tdw_custom_house/interactive_house.py
```

同一台机器上同时运行多个 TDW 控制器时，每个控制器必须使用不同端口。例如默认的
`1071` 已被占用时，可另开一个会话：

```bash
uv run python tdw_custom_house/interactive_house.py --port 1072
```

如果不需要已有会话，也可以先在它的窗口中退出，再重新使用默认端口。不要对一个已由
Python 控制器占用的端口使用 `--connect-existing`；该参数用于等待一个尚未连接控制器的
外部 Unity build，并不会复用另一套 Python 控制器。

控制方式：

- `W/A/S/D`：移动；
- 鼠标：转动视角；
- 窗口右上角：显示最近一次采集的全屋俯视图；
- `C`：同时重新采集顶视图和第一视角，并刷新右上角俯视图；
- `Escape` 或鼠标右键：退出。

默认第一视角物理高度为 `1.9 m`、眼高为 `1.8 m`，出生点使用已实拍验证的客厅开阔区域。可以继续调整：

```bash
uv run tdw-house \
  --ego-position -3.6 0 1.8 --ego-rotation 270 \
  --ego-height 2.0 --ego-camera-height 1.9
```

俯视图默认宽度为 384 像素。可以改变大小或关闭窗口叠加（磁盘中的顶视采集仍会保留）：

```bash
uv run tdw-house --top-view-width 480
uv run tdw-house --no-top-view
```

俯视画中画使用已捕获的静态图，而不是持续渲染第二台相机；住宅不发生变化时效果等同实时显示。程序在采集结束后会关闭顶视传感器，并把第一视角恢复为仅 RGB 渲染；深度和实例分割只在启动及按 `C` 时生成，从而避免无意义的持续多通道开销。

第一视角出生点依赖具体户型和家具布局。默认点是 `1a/0` 的起始参考；如果与墙体或家具相交，请显式调整：

```bash
uv run python tdw_custom_house/interactive_house.py \
  --scene 1a --layout 0 --ego-position -3.6 0 1.8 --ego-rotation 270
```

## 5. 只采集全屋顶视图

```bash
uv run python tdw_custom_house/capture_top_view.py
```

默认顶视相机位于 `(0, 40, 0)`。不同户型可调整相机和注视点：

```bash
uv run python tdw_custom_house/capture_top_view.py \
  --scene 4b --layout 2 \
  --top-position 0 45 0 --top-look-at 0 0 0
```

只保留住宅结构、完全由 JSON 添加家具：

```bash
uv run python tdw_custom_house/capture_top_view.py --layout empty
```

如果 TDW build 已由其他进程或容器启动：

```bash
uv run python tdw_custom_house/capture_top_view.py \
  --connect-existing --port 1071
```

## 6. 家具 JSON

编辑 `tdw_custom_house/furniture_config.json`。模型名必须存在于 TDW 模型库，例如本仓库的 `Python/tdw/metadata_libraries/models_core.json`。

```json
{
  "name": "target_jug",
  "model_name": "jug01",
  "position": {"x": 1.0, "y": 0.8, "z": 2.0},
  "rotation": {"x": 0, "y": 90, "z": 0},
  "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
  "library": "models_core.json",
  "enabled": true,
  "physics": {
    "use_default_values": false,
    "mass": 1.2,
    "dynamic_friction": 0.3,
    "static_friction": 0.4,
    "bounciness": 0.0,
    "kinematic": false,
    "gravity": true,
    "scale_mass": true
  }
}
```

`scale` 可以是单个正数，也可以是三轴对象。不填写 `physics` 时使用 TDW 的模型默认物理值；一旦填写质量或摩擦参数，`use_default_values` 会自动视为 `false`。名称必须唯一，未知字段会直接报错，避免拼写错误静默生效。

## 7. 输出

默认输出位于当前工作目录的 `output/`：

```text
output/
├── scene_objects.json
├── top/
│   ├── img_0000.png
│   ├── depth_0000.png
│   ├── depth_meters_0000.npy
│   ├── id_0000.png
│   ├── annotated_0000.png
│   └── annotations_0000.json
└── ego/
    ├── img_0000.png
    ├── depth_0000.png
    ├── depth_meters_0000.npy
    └── id_0000.png
```

注意：`id_*.png` 的 RGB 像素是 TDW 实例颜色，不是直接写入的整数对象 ID。`scene_objects.json` 保存了颜色、对象 ID、名称、类别、初始位姿、边界和物理信息。`depth_*.png` 是可视化/编码后的深度通道；数值计算应读取单位为米的 `depth_meters_*.npy`。

可以在采集后重新绘制标签：

```bash
uv run python tdw_custom_house/draw_annotations.py --frame 0
```

## 8. 测试

纯逻辑测试不需要 TDW build：

```bash
uv run python -m unittest discover -s tests -v
```

这些测试覆盖家具 JSON 校验、TDW 物理命令拼装、实例颜色匹配、边界框、绘图与 JSON 输出。真实住宅、碰撞、图像和键鼠行为仍需在带显示服务器的 TDW Unity build 中做一次现场冒烟测试。

机器人、抓取、投放和运输属于下一阶段；当前输出的稳定对象 ID、容器模型和场景元数据可直接作为后续 Replicant/Magnebot 任务层的输入。
