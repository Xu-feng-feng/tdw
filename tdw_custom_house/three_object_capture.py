"""Capture a cluttered multi-room home for robot-cleaning observations."""

from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from base64 import b64encode
import shutil
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tdw_custom_house.asset_cache import (
    cache_tdw_asset_urls,
    default_asset_cache_dir,
    rewrite_tdw_asset_urls_to_http,
)
from tdw_custom_house.config import Vector3
from tdw_custom_house.runtime import RuntimeDependencyError, _load_tdw_api


RGB_PASS = ["_img"]
AVATAR_IDS = ["robot", "top"]
SETTLE_FRAMES = 4
HOUSE_SCENE = "1a"
HOUSE_LAYOUT = 0


@dataclass(frozen=True, slots=True)
class ThreeObjectCaptureOptions:
    output: Path
    width: int
    height: int
    port: int
    connect_existing: bool
    asset_mode: str
    asset_cache_dir: Path
    view_pairs: int


@dataclass(frozen=True, slots=True)
class GroundObject:
    name: str
    model_name: str
    position: Vector3
    rotation: Vector3
    scale: Vector3
    color: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ViewPair:
    """One intentionally irregular robot/top camera pair."""

    robot_position: Vector3
    robot_look_at: Vector3
    top_position: Vector3
    top_look_at: Vector3


# These are deliberately spread across the living areas, kitchen, bedroom, and
# hallway. They are kinematic so each item remains exactly where it was dropped
# on the floor while the robot plans a cleanup route.
GROUND_OBJECTS = (
    GroundObject(
        name="living_apple",
        model_name="apple",
        position=Vector3(1.15, 0.0, -4.55),
        rotation=Vector3(0.0, 31.0, 0.0),
        scale=Vector3(3.2, 3.2, 3.2),
        color=(0.82, 0.08, 0.06),
    ),
    GroundObject(
        name="living_orange",
        model_name="orange",
        position=Vector3(0.45, 0.0, -3.35),
        rotation=Vector3(0.0, 77.0, 0.0),
        scale=Vector3(3.4, 3.4, 3.4),
        color=(0.95, 0.42, 0.03),
    ),
    GroundObject(
        name="living_banana",
        model_name="banana_fix2",
        position=Vector3(-1.65, 0.0, -4.25),
        rotation=Vector3(12.0, 125.0, 18.0),
        scale=Vector3(3.0, 3.0, 3.0),
        color=(0.93, 0.8, 0.05),
    ),
    GroundObject(
        name="fallen_mug",
        model_name="mug",
        position=Vector3(2.15, 0.13, -3.95),
        rotation=Vector3(88.0, 27.0, 0.0),
        scale=Vector3(4.0, 4.0, 4.0),
        color=(0.16, 0.3, 0.78),
    ),
    GroundObject(
        name="fallen_coffee_cup",
        model_name="coffee_cup",
        position=Vector3(-0.95, 0.29, -3.05),
        rotation=Vector3(92.0, 48.0, 0.0),
        scale=Vector3(3.0, 3.0, 3.0),
        color=(0.91, 0.9, 0.82),
    ),
    GroundObject(
        name="living_plate",
        model_name="plate05",
        position=Vector3(1.9, 0.02, -5.05),
        rotation=Vector3(3.0, 18.0, 0.0),
        scale=Vector3(3.2, 3.2, 3.2),
        color=(0.93, 0.89, 0.72),
    ),
    GroundObject(
        name="living_spoon",
        model_name="spoon1",
        position=Vector3(-2.25, 0.03, -3.55),
        rotation=Vector3(0.0, 118.0, 4.0),
        scale=Vector3(4.0, 4.0, 4.0),
        color=(0.72, 0.72, 0.76),
    ),
    GroundObject(
        name="living_fork",
        model_name="fork1",
        position=Vector3(0.05, 0.03, -4.75),
        rotation=Vector3(1.0, 37.0, 0.0),
        scale=Vector3(4.2, 4.2, 4.2),
        color=(0.72, 0.72, 0.76),
    ),
    GroundObject(
        name="living_knife",
        model_name="knife1",
        position=Vector3(2.7, 0.02, -4.55),
        rotation=Vector3(0.0, 142.0, 0.0),
        scale=Vector3(4.2, 4.2, 4.2),
        color=(0.63, 0.66, 0.7),
    ),
    # A deliberately uneven pile across the open living-room floor. These
    # small household objects are kept separate rather than arranged in a
    # grid, so the robot view has many individual cleanup targets.
    GroundObject(
        name="living_fallen_pan",
        model_name="skillet_closed",
        position=Vector3(-0.3, 0.04, -4.05),
        rotation=Vector3(7.0, 203.0, 13.0),
        scale=Vector3(1.45, 1.45, 1.45),
        color=(0.16, 0.18, 0.22),
    ),
    GroundObject(
        name="living_spatula",
        model_name="spatula2",
        position=Vector3(-1.05, 0.02, -4.72),
        rotation=Vector3(2.0, 16.0, 0.0),
        scale=Vector3(3.3, 3.3, 3.3),
        color=(0.1, 0.53, 0.79),
    ),
    GroundObject(
        name="living_baking_tray",
        model_name="baking_sheet10",
        position=Vector3(2.55, 0.02, -3.35),
        rotation=Vector3(0.0, 109.0, 3.0),
        scale=Vector3(1.8, 1.8, 1.8),
        color=(0.27, 0.3, 0.34),
    ),
    GroundObject(
        name="living_jug",
        model_name="jug01",
        position=Vector3(-1.8, 0.12, -3.1),
        rotation=Vector3(84.0, 40.0, 0.0),
        scale=Vector3(2.1, 2.1, 2.1),
        color=(0.13, 0.63, 0.72),
    ),
    GroundObject(
        name="living_pepper_shaker",
        model_name="pepper",
        position=Vector3(0.8, 0.02, -5.25),
        rotation=Vector3(74.0, 214.0, 8.0),
        scale=Vector3(2.2, 2.2, 2.2),
        color=(0.12, 0.12, 0.12),
    ),
    GroundObject(
        name="living_tumbler",
        model_name="glass2",
        position=Vector3(-2.6, 0.08, -4.55),
        rotation=Vector3(87.0, 64.0, 0.0),
        scale=Vector3(2.6, 2.6, 2.6),
        color=(0.26, 0.7, 0.84),
    ),
    GroundObject(
        name="living_pencils",
        model_name="pencil_all",
        position=Vector3(1.35, 0.03, -3.1),
        rotation=Vector3(0.0, 157.0, 0.0),
        scale=Vector3(2.2, 2.2, 2.2),
        color=(0.95, 0.31, 0.07),
    ),
    # A closer, untidy pile: objects overlap, point in different directions,
    # and sit near furniture rather than following a regular spacing pattern.
    GroundObject(
        name="pile_spilled_mug",
        model_name="mug",
        position=Vector3(-0.62, 0.14, -4.42),
        rotation=Vector3(91.0, 232.0, 16.0),
        scale=Vector3(3.5, 3.5, 3.5),
        color=(0.78, 0.11, 0.19),
    ),
    GroundObject(
        name="pile_orange",
        model_name="orange",
        position=Vector3(-0.78, 0.0, -4.1),
        rotation=Vector3(0.0, 267.0, 0.0),
        scale=Vector3(2.6, 2.6, 2.6),
        color=(0.97, 0.32, 0.01),
    ),
    GroundObject(
        name="pile_apple",
        model_name="apple",
        position=Vector3(0.72, 0.0, -4.75),
        rotation=Vector3(0.0, 11.0, 0.0),
        scale=Vector3(2.65, 2.65, 2.65),
        color=(0.69, 0.05, 0.04),
    ),
    GroundObject(
        name="pile_plate",
        model_name="plate05",
        position=Vector3(1.25, 0.03, -3.65),
        rotation=Vector3(9.0, 197.0, 5.0),
        scale=Vector3(2.75, 2.75, 2.75),
        color=(0.78, 0.69, 0.31),
    ),
    GroundObject(
        name="pile_fork",
        model_name="fork1",
        position=Vector3(-1.55, 0.03, -4.8),
        rotation=Vector3(4.0, 283.0, 6.0),
        scale=Vector3(3.85, 3.85, 3.85),
        color=(0.6, 0.62, 0.65),
    ),
    GroundObject(
        name="pile_spoon",
        model_name="spoon1",
        position=Vector3(0.12, 0.03, -4.88),
        rotation=Vector3(2.0, 133.0, 0.0),
        scale=Vector3(3.65, 3.65, 3.65),
        color=(0.62, 0.65, 0.7),
    ),
    GroundObject(
        name="pile_toaster",
        model_name="toaster_002",
        position=Vector3(-1.75, 0.16, -3.7),
        rotation=Vector3(87.0, 18.0, 7.0),
        scale=Vector3(1.35, 1.35, 1.35),
        color=(0.24, 0.28, 0.35),
    ),
    GroundObject(
        name="pile_jug",
        model_name="jug01",
        position=Vector3(2.05, 0.13, -4.25),
        rotation=Vector3(89.0, 128.0, 9.0),
        scale=Vector3(1.85, 1.85, 1.85),
        color=(0.13, 0.42, 0.88),
    ),
    GroundObject(
        name="pile_baking_tray",
        model_name="baking_sheet10",
        position=Vector3(2.35, 0.03, -3.75),
        rotation=Vector3(4.0, 321.0, 5.0),
        scale=Vector3(1.45, 1.45, 1.45),
        color=(0.21, 0.24, 0.28),
    ),
    GroundObject(
        name="right_room_fruit_basket",
        model_name="fruit_basket",
        position=Vector3(6.25, 0.0, -4.15),
        rotation=Vector3(0.0, 51.0, 0.0),
        scale=Vector3(1.15, 1.15, 1.15),
        color=(0.86, 0.38, 0.07),
    ),
    GroundObject(
        name="right_room_toy",
        model_name="toy_monkey_medium",
        position=Vector3(7.65, 0.0, -3.05),
        rotation=Vector3(0.0, 211.0, 0.0),
        scale=Vector3(2.4, 2.4, 2.4),
        color=(0.46, 0.16, 0.62),
    ),
    GroundObject(
        name="right_room_drawer_cabinet",
        model_name="cabinet_24_two_drawer_white_wood",
        position=Vector3(4.95, 0.0, -4.55),
        rotation=Vector3(0.0, 68.0, 0.0),
        scale=Vector3(1.0, 1.0, 1.0),
        color=(0.72, 0.57, 0.33),
    ),
    GroundObject(
        name="right_room_orange",
        model_name="orange",
        position=Vector3(6.45, 0.0, -2.85),
        rotation=Vector3(0.0, 143.0, 0.0),
        scale=Vector3(2.9, 2.9, 2.9),
        color=(0.96, 0.34, 0.02),
    ),
    GroundObject(
        name="right_room_coffee_cup",
        model_name="coffee_cup",
        position=Vector3(7.0, 0.18, -3.7),
        rotation=Vector3(88.0, 178.0, 4.0),
        scale=Vector3(2.7, 2.7, 2.7),
        color=(0.98, 0.77, 0.22),
    ),
    GroundObject(
        name="right_room_spoon",
        model_name="spoon1",
        position=Vector3(7.95, 0.03, -3.9),
        rotation=Vector3(0.0, 72.0, 2.0),
        scale=Vector3(3.8, 3.8, 3.8),
        color=(0.75, 0.75, 0.79),
    ),
    GroundObject(
        name="lounge_satchal",
        model_name="blue_satchal",
        position=Vector3(-5.25, 0.0, 1.35),
        rotation=Vector3(0.0, 127.0, 0.0),
        scale=Vector3(2.0, 2.0, 2.0),
        color=(0.12, 0.24, 0.84),
    ),
    GroundObject(
        name="lounge_briefcase",
        model_name="metal_briefcase",
        position=Vector3(-6.25, 0.0, 2.45),
        rotation=Vector3(0.0, 19.0, 0.0),
        scale=Vector3(1.35, 1.35, 1.35),
        color=(0.2, 0.23, 0.28),
    ),
    GroundObject(
        name="lounge_banana",
        model_name="banana_fix2",
        position=Vector3(-4.7, 0.0, 2.55),
        rotation=Vector3(6.0, 42.0, 12.0),
        scale=Vector3(2.8, 2.8, 2.8),
        color=(0.9, 0.74, 0.04),
    ),
    GroundObject(
        name="lounge_plate",
        model_name="plate05",
        position=Vector3(-5.85, 0.02, 0.85),
        rotation=Vector3(0.0, 33.0, 0.0),
        scale=Vector3(2.9, 2.9, 2.9),
        color=(0.94, 0.7, 0.18),
    ),
    GroundObject(
        name="lounge_spatula",
        model_name="spatula2",
        position=Vector3(-6.3, 0.02, 1.55),
        rotation=Vector3(0.0, 141.0, 3.0),
        scale=Vector3(2.8, 2.8, 2.8),
        color=(0.82, 0.15, 0.15),
    ),
    GroundObject(
        name="kitchen_mug",
        model_name="mug",
        position=Vector3(0.85, 0.13, 2.35),
        rotation=Vector3(86.0, 113.0, 0.0),
        scale=Vector3(4.0, 4.0, 4.0),
        color=(0.17, 0.58, 0.64),
    ),
    GroundObject(
        name="kitchen_plate",
        model_name="plate05",
        position=Vector3(-0.55, 0.02, 3.1),
        rotation=Vector3(0.0, 71.0, 0.0),
        scale=Vector3(3.2, 3.2, 3.2),
        color=(0.92, 0.85, 0.56),
    ),
    GroundObject(
        name="kitchen_fallen_pan",
        model_name="skillet_closed",
        position=Vector3(1.8, 0.05, 2.85),
        rotation=Vector3(4.0, 272.0, 10.0),
        scale=Vector3(1.4, 1.4, 1.4),
        color=(0.18, 0.18, 0.21),
    ),
    GroundObject(
        name="kitchen_spatula",
        model_name="spatula2",
        position=Vector3(1.95, 0.02, 2.05),
        rotation=Vector3(0.0, 53.0, 1.0),
        scale=Vector3(3.0, 3.0, 3.0),
        color=(0.7, 0.12, 0.16),
    ),
    GroundObject(
        name="kitchen_orange",
        model_name="orange",
        position=Vector3(-1.15, 0.0, 2.25),
        rotation=Vector3(0.0, 17.0, 0.0),
        scale=Vector3(3.0, 3.0, 3.0),
        color=(0.98, 0.39, 0.02),
    ),
    GroundObject(
        name="kitchen_baking_tray",
        model_name="baking_sheet10",
        position=Vector3(-1.7, 0.02, 3.5),
        rotation=Vector3(1.0, 126.0, 0.0),
        scale=Vector3(1.55, 1.55, 1.55),
        color=(0.3, 0.31, 0.36),
    ),
    GroundObject(
        name="kitchen_pepper_shaker",
        model_name="pepper",
        position=Vector3(0.15, 0.02, 1.95),
        rotation=Vector3(88.0, 24.0, 0.0),
        scale=Vector3(2.0, 2.0, 2.0),
        color=(0.13, 0.13, 0.13),
    ),
    GroundObject(
        name="bedroom_briefcase",
        model_name="metal_briefcase",
        position=Vector3(-10.65, 0.0, 2.15),
        rotation=Vector3(0.0, 132.0, 0.0),
        scale=Vector3(1.3, 1.3, 1.3),
        color=(0.32, 0.12, 0.08),
    ),
)


# Four room-level robot observations and four whole-home maps:
# 4 pairs × 2 viewpoints = 8 screenshots.
VIEW_PAIRS = (
    ViewPair(
        robot_position=Vector3(1.5, 1.25, -5.45),
        robot_look_at=Vector3(0.35, 0.35, -3.75),
        top_position=Vector3(0.0, 42.0, -7.0),
        top_look_at=Vector3(0.0, 0.0, 0.0),
    ),
    ViewPair(
        # A second diagonal pass over the dense living-room cleanup area.
        robot_position=Vector3(-2.8, 1.3, -5.3),
        robot_look_at=Vector3(-0.25, 0.25, -3.85),
        top_position=Vector3(-4.0, 40.0, -5.0),
        top_look_at=Vector3(-0.5, 0.0, 0.0),
    ),
    ViewPair(
        # This is deliberately close to the floor pile, with a different
        # approach angle so the pan, utensils, fruit, and cup do not overlap.
        robot_position=Vector3(3.15, 1.3, -5.2),
        robot_look_at=Vector3(0.65, 0.25, -3.95),
        top_position=Vector3(4.0, 41.0, 5.0),
        top_look_at=Vector3(0.0, 0.0, 0.0),
    ),
    ViewPair(
        robot_position=Vector3(-0.4, 1.3, -5.7),
        robot_look_at=Vector3(0.5, 0.24, -3.75),
        top_position=Vector3(0.0, 44.0, 7.0),
        top_look_at=Vector3(0.0, 0.0, 0.0),
    ),
)


def run_three_object_capture(options: ThreeObjectCaptureOptions) -> None:
    """Write cluttered-home robot and whole-map captures as PNG and SVG files."""

    if not 1 <= options.view_pairs <= len(VIEW_PAIRS):
        raise ValueError(f"--view-pairs must be between 1 and {len(VIEW_PAIRS)}")
    api = _load_tdw_api()
    controller: Any | None = None
    try:
        _ensure_port_available(options.port)
        commands = _build_scene_commands(api)
        commands = _prepare_assets(commands, options)
        controller = api.Controller(
            port=options.port,
            check_version=not options.connect_existing,
            launch_build=not options.connect_existing,
        )

        views = VIEW_PAIRS[: options.view_pairs]
        first_view = views[0]
        # This low, forward-looking camera represents the robot's perception
        # sensor. It is deliberately distinct from the elevated top camera.
        robot_camera = api.ThirdPersonCamera(
            avatar_id="robot",
            position=first_view.robot_position.as_dict(),
            look_at=first_view.robot_look_at.as_dict(),
            field_of_view=60,
        )
        # A horizontal offset from the vertical axis makes this a genuinely
        # tilted top view while keeping all three objects in frame.
        top_camera = api.ThirdPersonCamera(
            avatar_id="top",
            position=first_view.top_position.as_dict(),
            look_at=first_view.top_look_at.as_dict(),
            field_of_view=45,
        )
        controller.add_ons.extend([robot_camera, top_camera])
        controller.communicate(
            commands
            + [
                {"$type": "set_screen_size", "width": options.width, "height": options.height},
                {"$type": "set_floorplan_roof", "show": False},
                # A single global depth-of-field focus plane can't keep both
                # the room-level robot camera and whole-home map sharp.
                {"$type": "set_post_process", "value": False},
            ]
        )
        # Let Unity finish loading the camera sensors and apply temporal
        # rendering state before the single lossless capture.
        for _ in range(SETTLE_FRAMES):
            controller.communicate([])

        options.output.mkdir(parents=True, exist_ok=True)
        capture = api.ImageCapture(
            path=options.output,
            avatar_ids=AVATAR_IDS,
            pass_masks=RGB_PASS,
            png=True,
        )
        _prime_rgb_capture(capture)
        controller.add_ons.append(capture)
        for index, view in enumerate(views, start=1):
            if index == 1:
                controller.communicate([])
            else:
                # Use the camera add-on APIs so their persistent look-at state
                # stays in sync with each deliberately uneven observation.
                robot_camera.teleport(view.robot_position.as_dict())
                robot_camera.look_at(view.robot_look_at.as_dict())
                top_camera.teleport(view.top_position.as_dict())
                top_camera.look_at(view.top_look_at.as_dict())
                controller.communicate([])
                capture.set(frequency="once", avatar_ids=AVATAR_IDS, pass_masks=RGB_PASS)
                controller.communicate([])

            _validate_rgb_capture(capture)
            _copy_named_images(
                options.output,
                frame=capture.frame - 1,
                capture_index=index,
                width=options.width,
                height=options.height,
            )
            print(f"Saved irregular robot/top pair {index}/{len(views)}", flush=True)
    finally:
        if controller is not None:
            with suppress(Exception):
                controller.communicate({"$type": "terminate"})


def _build_scene_commands(api: Any) -> list[dict[str, Any]]:
    floorplan = api.Floorplan()
    floorplan.init_scene(scene=HOUSE_SCENE, layout=HOUSE_LAYOUT)
    commands = list(floorplan.commands)
    for item in GROUND_OBJECTS:
        object_id = int(api.Controller.get_unique_id())
        commands.extend(
            api.Controller.get_add_physics_object(
                model_name=item.model_name,
                object_id=object_id,
                position=item.position.as_dict(),
                rotation=item.rotation.as_dict(),
                library="models_core.json",
                scale_factor=item.scale.as_dict(),
                kinematic=True,
                gravity=False,
                default_physics_values=False,
                mass=1.0,
                dynamic_friction=0.8,
                static_friction=0.8,
                bounciness=0.0,
            )
        )
        commands.append(
            {
                "$type": "set_color",
                "color": {"r": item.color[0], "g": item.color[1], "b": item.color[2], "a": 1.0},
                "id": object_id,
            }
        )
    return commands


def _prepare_assets(
    commands: list[dict[str, Any]], options: ThreeObjectCaptureOptions
) -> list[dict[str, Any]]:
    if options.asset_mode == "https":
        return commands
    if options.asset_mode == "http":
        print("Warning: --asset-mode http disables transport encryption for TDW assets.", file=sys.stderr)
        return rewrite_tdw_asset_urls_to_http(commands)
    if options.asset_mode != "cache":
        raise ValueError(f"Unsupported asset mode: {options.asset_mode!r}")

    print(f"Preparing TDW assets in {options.asset_cache_dir.expanduser()} ...", flush=True)
    return cache_tdw_asset_urls(
        commands,
        options.asset_cache_dir,
        progress=lambda message: print(message, flush=True),
    )


def _prime_rgb_capture(capture: Any) -> None:
    """Configure ImageCapture for exactly one pair of lossless RGB frames."""

    capture.set(frequency="once", avatar_ids=AVATAR_IDS, pass_masks=RGB_PASS)
    # Initialization emits the same commands. Remove only the duplicates queued
    # by set() before the add-on was initialized.
    capture.commands.clear()


def _validate_rgb_capture(capture: Any) -> None:
    for avatar_id in AVATAR_IDS:
        images = capture.images.get(avatar_id)
        if images is None:
            raise RuntimeError(f"TDW returned no RGB image for {avatar_id!r}")
        masks = {images.get_pass_mask(index) for index in range(images.get_num_passes())}
        if "_img" not in masks:
            raise RuntimeError(f"TDW RGB image is missing for {avatar_id!r}")


def _copy_named_images(
    output: Path, *, frame: int, capture_index: int, width: int, height: int
) -> None:
    filename = f"img_{frame:04d}.png"
    for avatar_id, basename in (
        ("robot", f"robot_view_{capture_index:02d}"),
        ("top", f"top_view_tilted_{capture_index:02d}"),
    ):
        source = output / avatar_id / filename
        if not source.is_file():
            raise RuntimeError(f"Expected PNG capture was not written: {source}")
        destination = output / f"{basename}.png"
        shutil.copy2(source, destination)
        _write_svg_wrapper(destination, output / f"{basename}.svg", width=width, height=height)


def _write_svg_wrapper(png_path: Path, svg_path: Path, *, width: int, height: int) -> None:
    """Store the rendered PNG in a self-contained SVG document."""

    encoded_png = b64encode(png_path.read_bytes()).decode("ascii")
    svg_path.write_text(
        "\n".join(
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                f'  <image width="{width}" height="{height}" href="data:image/png;base64,{encoded_png}" />',
                "</svg>",
            )
        ),
        encoding="utf-8",
    )


def _ensure_port_available(port: int) -> None:
    import errno
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise RuntimeError(f"TDW port {port} is already in use; choose another --port.") from exc
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a cluttered home from robot and whole-home tilted map cameras."
    )
    parser.add_argument("--output", type=Path, default=Path.cwd() / "output" / "house_clutter")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--port", type=int, default=1073)
    parser.add_argument("--connect-existing", action="store_true")
    parser.add_argument(
        "--view-pairs",
        type=int,
        default=4,
        help="Robot/top pairs to save; default 4 produces 8 screenshots.",
    )
    parser.add_argument("--asset-mode", choices=("cache", "https", "http"), default="cache")
    parser.add_argument("--asset-cache-dir", type=Path, default=default_asset_cache_dir())
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.width < 1 or args.height < 1:
        print("Error: --width and --height must be positive.", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65535:
        print("Error: --port must be between 1 and 65535.", file=sys.stderr)
        return 2
    if not 1 <= args.view_pairs <= len(VIEW_PAIRS):
        print(f"Error: --view-pairs must be between 1 and {len(VIEW_PAIRS)}.", file=sys.stderr)
        return 2
    options = ThreeObjectCaptureOptions(
        output=args.output,
        width=args.width,
        height=args.height,
        port=args.port,
        connect_existing=args.connect_existing,
        asset_mode=args.asset_mode,
        asset_cache_dir=args.asset_cache_dir,
        view_pairs=args.view_pairs,
    )
    try:
        run_three_object_capture(options)
        return 0
    except (RuntimeDependencyError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
