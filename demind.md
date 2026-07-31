可以实现。你需要的并不是单纯“生成一张俯视图”，而是一个具备以下能力的可交互仿真项目：

* 进入多房间住宅，以第一视角用键盘和鼠标行走；
* 从高空相机获得类似论文中的全屋俯视图；
* 自定义家具种类、位置、朝向和物理属性；
* 同时输出 RGB、深度图和实例分割图；
* 后续能够加入机器人、抓取目标、容器和任务点。

这类需求最适合直接使用 **TDW 核心 API**。暂时不要先从旧版 Transport Challenge 的 Docker 环境入手，因为挑战代码额外绑定了旧版依赖、数据集和任务规则；先把 TDW 场景、相机和家具配置跑通会容易很多。

## 一、论文中的画面是如何生成的

论文截图可以拆成三层：

| 画面内容        | TDW 对应组件                   |
| ----------- | -------------------------- |
| 多房间住宅       | `Floorplan`                |
| 机器人在房间内移动   | Magnebot、Replicant 或自定义机器人 |
| 高处俯视整个住宅    | `ThirdPersonCamera`        |
| 隐藏屋顶        | `set_floorplan_roof`       |
| 第一视角进入住宅    | `FirstPersonAvatar`        |
| 家具、床、桌子、容器  | TDW 对象模型库                  |
| 橙色和绿色框、文字标签 | 根据对象元数据后处理绘制               |

TDW 官方提供四种住宅几何，每种有三个外观变体和三个预设家具布局；`Floorplan` 可以直接加载这些多房间场景。官方示例同样使用高处的 `ThirdPersonCamera`，并通过隐藏屋顶生成全屋俯视画面。([GitHub][1])

第一视角可使用 `FirstPersonAvatar`，默认支持 W/S 前后移动、A/D 横向移动以及鼠标控制视角。([GitHub][2])

## 二、建议的项目结构

```text
tdw_custom_house/
├── interactive_house.py       # 第一视角进入住宅
├── capture_top_view.py        # 生成俯视图
├── furniture_config.json      # 家具位置和朝向
├── draw_annotations.py        # 绘制框和名称
└── output/
    ├── ego/
    ├── top/
    ├── depth/
    └── segmentation/
```

第一阶段先完成：

```text
住宅户型
   ├── 第一视角相机：窗口中自由行走
   ├── 顶视相机：保存整屋俯视图
   └── 家具配置：由 JSON 控制
```

第二阶段再加入：

```text
机器人
   ├── Move
   ├── Grasp
   ├── Drop
   └── Transport
```

## 三、最小可运行程序

安装：

```bash
python -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install tdw pillow
```

保存为 `interactive_house.py`：

```python
from pathlib import Path

from tdw.controller import Controller
from tdw.add_ons.floorplan import Floorplan
from tdw.add_ons.first_person_avatar import FirstPersonAvatar
from tdw.add_ons.third_person_camera import ThirdPersonCamera
from tdw.add_ons.image_capture import ImageCapture


OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    controller = Controller()

    # ---------------------------------------------------------
    # 1. 加载多房间住宅和预设家具
    #
    # scene:
    #   1a / 1b / 1c
    #   2a / 2b / 2c
    #   4a / 4b / 4c
    #   5a / 5b / 5c
    #
    # layout:
    #   0 / 1 / 2
    # ---------------------------------------------------------
    floorplan = Floorplan()
    floorplan.init_scene(scene="1a", layout=0)

    controller.add_ons.append(floorplan)

    # 必须先初始化住宅场景。
    controller.communicate([])

    # ---------------------------------------------------------
    # 2. 第一视角
    # 初始位置需要根据具体户型调整，避免出生在家具或墙内。
    # ---------------------------------------------------------
    ego = FirstPersonAvatar(
        avatar_id="ego",
        position={"x": -2.0, "y": 0.0, "z": -2.0},
        rotation=0,
        field_of_view=75,
        move_speed=1.5,
        look_speed=50,
        framerate=60,
    )

    # ---------------------------------------------------------
    # 3. 全屋顶视相机
    # ---------------------------------------------------------
    top_camera = ThirdPersonCamera(
        avatar_id="top",
        position={"x": 0.0, "y": 25.0, "z": 0.0},
        look_at={"x": 0.0, "y": 0.0, "z": 0.0},
    )

    # 保存顶视 RGB、深度和实例分割图。
    top_capture = ImageCapture(
        path=OUTPUT_DIR / "top",
        avatar_ids=["top"],
        pass_masks=["_img", "_depth", "_id"],
        png=True,
    )

    # FirstPersonAvatar 放在 ThirdPersonCamera 后面，
    # 保证应用窗口显示第一视角。
    controller.add_ons.extend(
        [
            top_camera,
            ego,
            top_capture,
        ]
    )

    # ---------------------------------------------------------
    # 4. 隐藏屋顶并设置画面尺寸
    # ---------------------------------------------------------
    controller.communicate(
        [
            {
                "$type": "set_screen_size",
                "width": 1280,
                "height": 720,
            },
            {
                "$type": "set_floorplan_roof",
                "show": False,
            },
        ]
    )

    # 顶视相机只保存第一帧，避免行走时不断写入大量图片。
    top_capture.set(frequency="never")
    controller.communicate([])

    print("控制方式：")
    print("  W/S 或上下方向键：前进、后退")
    print("  A/D 或左右方向键：左右移动")
    print("  鼠标：旋转视角")
    print("  鼠标右键：退出")
    print(f"顶视图输出目录：{OUTPUT_DIR.resolve() / 'top'}")

    try:
        while True:
            controller.communicate([])

            if ego.right_button_pressed:
                break

    finally:
        controller.communicate({"$type": "terminate"})


if __name__ == "__main__":
    main()
```

运行：

```bash
python interactive_house.py
```

启动后会出现 TDW 窗口，你可以直接进入房间，用键盘和鼠标移动。顶视结果会保存到：

```text
output/top/
```

TDW 的 `ImageCapture` 能够按照相机 ID 保存 RGB、实例分割及其他图像通道；图像采集和窗口渲染是两个独立过程。([GitHub][3])

## 四、如何自由设置家具

家具最好写在 JSON 中，而不是全部硬编码到 Python。

`furniture_config.json`：

```json
[
  {
    "name": "custom_table",
    "model_name": "填写TDW模型库中的真实名称",
    "position": {
      "x": 1.5,
      "y": 0.0,
      "z": 2.0
    },
    "rotation": {
      "x": 0.0,
      "y": 90.0,
      "z": 0.0
    }
  },
  {
    "name": "custom_chair",
    "model_name": "填写TDW模型库中的真实名称",
    "position": {
      "x": 2.3,
      "y": 0.0,
      "z": 2.0
    },
    "rotation": {
      "x": 0.0,
      "y": 270.0,
      "z": 0.0
    }
  }
]
```

加载函数：

```python
import json
from pathlib import Path
from typing import Any

from tdw.controller import Controller


def load_furniture_commands(
    controller: Controller,
    config_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    将 JSON 家具配置转换为 TDW 命令。

    返回：
        commands:
            可以传给 controller.communicate() 的命令列表。
        object_ids:
            配置名称到 TDW object ID 的映射。
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"家具配置不存在：{path}")

    with path.open("r", encoding="utf-8") as file:
        furniture = json.load(file)

    if not isinstance(furniture, list):
        raise ValueError("家具配置的根节点必须是列表")

    commands: list[dict[str, Any]] = []
    object_ids: dict[str, int] = {}

    for index, item in enumerate(furniture):
        try:
            item_name = str(item["name"])
            model_name = str(item["model_name"])
            position = item["position"]
            rotation = item.get(
                "rotation",
                {"x": 0, "y": 0, "z": 0},
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"第 {index} 个家具配置格式错误：{item}"
            ) from exc

        object_id = controller.get_unique_id()
        object_ids[item_name] = object_id

        commands.append(
            controller.get_add_object(
                model_name=model_name,
                object_id=object_id,
                position=position,
                rotation=rotation,
                library="models_core.json",
            )
        )

    return commands, object_ids
```

在住宅初始化完成后调用：

```python
commands, furniture_ids = load_furniture_commands(
    controller=controller,
    config_path="furniture_config.json",
)

controller.communicate(commands)
```

TDW 中的对象由 Controller 添加，每个对象具有唯一 ID，并可在创建时指定模型名称、位置和旋转；对象默认同时参与渲染和物理仿真。([GitHub][4])

## 五、家具自定义有三个级别

### 1. 最容易：切换预设布局

```python
floorplan.init_scene(scene="1a", layout=0)
floorplan.init_scene(scene="1a", layout=1)
floorplan.init_scene(scene="1a", layout=2)
```

适合快速得到不同家具摆放。

### 2. 中等：在住宅内自行增删家具

使用：

```python
controller.get_add_object(...)
```

控制：

```text
model_name
position
rotation
scale
mass
friction
```

这种方式最符合你的需求：房屋结构不变，家具位置和类型由配置文件控制。

### 3. 完全自定义：导入自己的模型

可以导入自己的：

```text
FBX
OBJ
Prefab
URDF
ShapeNet 模型
```

TDW 官方文档提供自定义模型、ShapeNet 模型和 URDF 机器人导入流程。([GitHub][5])

## 六、论文截图中的框和文字需要额外绘制

论文中这些内容：

```text
toy
jug
bowl
vase
container
bed
```

以及橙色、蓝色、绿色框，并不是普通顶视相机自动生成的。

实现流程是：

```text
顶视 RGB 图
      +
实例分割 _id
      +
对象 ID、类别和位置
      ↓
计算每个对象的二维区域
      ↓
OpenCV 绘制矩形和文字
```

TDW 原生可以输出：

* `_img`：RGB；
* `_id`：实例分割；
* `_category`：类别分割；
* `_depth`：深度；
* 对象位置和边界信息。

这些通道已在官方视觉感知和图像接口中提供。([GitHub][6])

## 七、关于你当前的 Docker 环境

假如你正在远程服务器或容器内运行，程序可能能够启动，但你看不到交互窗口。此时至少需要一种显示方案：

```text
本地桌面直接运行
X11 forwarding
xpra
VNC
```

TDW 官方也将 Linux 服务器上的 `xpra` 和 X11 转发列为远程渲染方式。([GitHub][5])

为了最快获得“可以进入住宅并看到家具”的效果，建议顺序是：

```text
本地或带桌面的 x86 Ubuntu
        ↓
pip 安装 TDW
        ↓
运行 FirstPersonAvatar
        ↓
跑通 Floorplan 顶视图
        ↓
加入家具 JSON
        ↓
最后再迁移到 Docker/服务器
```

**最终选择：使用 TDW 核心项目作为场景和渲染层，参考 Transport Challenge 的任务设计，但不要直接把旧挑战 Docker 当作第一步。**这样最容易获得论文中的多房间住宅、第一视角、俯视图和可配置家具。

[1]: https://github.com/threedworld-mit/tdw/blob/master/Documentation/lessons/scene_setup_high_level/floorplans.md "tdw/Documentation/lessons/scene_setup_high_level/floorplans.md at master · threedworld-mit/tdw · GitHub"
[2]: https://github.com/threedworld-mit/tdw/blob/master/Documentation/lessons/keyboard_and_mouse/first_person_avatar.md "tdw/Documentation/lessons/keyboard_and_mouse/first_person_avatar.md at master · threedworld-mit/tdw · GitHub"
[3]: https://github.com/threedworld-mit/tdw/blob/master/Documentation/lessons/core_concepts/images.md "tdw/Documentation/lessons/core_concepts/images.md at master · threedworld-mit/tdw · GitHub"
[4]: https://github.com/threedworld-mit/tdw/blob/master/Documentation/lessons/core_concepts/objects.md "tdw/Documentation/lessons/core_concepts/objects.md at master · threedworld-mit/tdw · GitHub"
[5]: https://github.com/threedworld-mit/tdw "GitHub - threedworld-mit/tdw: ThreeDWorld simulation environment · GitHub"
[6]: https://github.com/threedworld-mit/tdw?utm_source=chatgpt.com "GitHub - threedworld-mit/tdw: ThreeDWorld simulation environment · GitHub"
