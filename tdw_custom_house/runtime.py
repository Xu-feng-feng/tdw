"""TDW scene orchestration for capture and first-person interaction."""

from __future__ import annotations

import errno
import math
import socket
import sys
from base64 import b64encode
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from .asset_cache import (
    cache_tdw_asset_urls,
    default_asset_cache_dir,
    rewrite_tdw_asset_urls_to_http,
)
from .config import FurnitureItem, Vector3, build_furniture_commands
from .instance_annotations import annotate_image, save_annotations_json


PASS_MASKS = ["_img", "_depth", "_id"]
CAPTURE_TIMEOUT_FRAMES = 120


class RuntimeDependencyError(RuntimeError):
    """Raised when the compatible TDW runtime isn't installed."""


@dataclass(frozen=True, slots=True)
class RuntimeOptions:
    scene: str
    layout: int | None
    output: Path
    width: int
    height: int
    top_position: Vector3
    top_look_at: Vector3
    top_field_of_view: int
    port: int
    connect_existing: bool
    show_roof: bool
    settle_frames: int
    annotations: bool
    min_annotation_pixels: int
    asset_mode: str = field(default="cache", kw_only=True)
    asset_cache_dir: Path = field(default_factory=default_asset_cache_dir, kw_only=True)


@dataclass(frozen=True, slots=True)
class InteractiveOptions(RuntimeOptions):
    ego_position: Vector3
    ego_rotation: float
    ego_field_of_view: int
    ego_height: float
    ego_camera_height: float
    move_speed: float
    look_speed: float
    framerate: int
    ego_radius: float
    capture_ego: bool
    show_top_view: bool
    top_view_width: int


@dataclass(frozen=True, slots=True)
class CapturePose:
    """Deterministic ego and optional top-camera poses for batch capture."""

    position: Vector3
    rotation: float
    top_position: Vector3 | None = field(default=None, kw_only=True)
    top_look_at: Vector3 | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class _TdwApi:
    Controller: Any
    Floorplan: Any
    FirstPersonAvatar: Any
    ThirdPersonCamera: Any
    ImageCapture: Any
    Keyboard: Any
    TDWUtils: Any
    SceneMetadata: Any


def run_top_capture(options: RuntimeOptions, furniture: Sequence[FurnitureItem]) -> None:
    """Load a floorplan, capture one top frame, write metadata, and exit."""

    api = _load_tdw_api()
    controller: Any | None = None
    try:
        _ensure_port_available(options.port)
        scene_commands, custom_ids = _prepare_scene_and_furniture_commands(
            api, options, furniture
        )
        scene_commands = _prepare_asset_commands(scene_commands, options)
        controller = _create_controller(api, options)
        _load_prepared_scene(controller, scene_commands, options.settle_frames)
        top_camera = api.ThirdPersonCamera(
            avatar_id="top",
            position=options.top_position.as_dict(),
            look_at=options.top_look_at.as_dict(),
            field_of_view=options.top_field_of_view,
        )
        capture = api.ImageCapture(
            path=options.output,
            avatar_ids=["top"],
            pass_masks=PASS_MASKS,
            png=True,
        )
        _prime_one_shot_capture(capture, ["top"])
        metadata = api.SceneMetadata()
        controller.add_ons.extend([top_camera, capture, metadata])
        controller.communicate(_display_commands(options))

        _validate_capture(capture, ["top"])
        frame = max(0, capture.frame - 1)
        custom_names = {object_id: name for name, object_id in custom_ids.items()}
        _write_capture_artifacts(
            options=options,
            capture=capture,
            metadata=metadata,
            custom_names=custom_names,
            frame=frame,
            api=api,
        )
        capture.set(
            frequency="never",
            avatar_ids=["top"],
            pass_masks=PASS_MASKS,
        )
        controller.communicate([])
        print(f"Capture complete: {options.output.resolve()}")
    finally:
        _terminate(controller)


def run_interactive(options: InteractiveOptions, furniture: Sequence[FurnitureItem]) -> None:
    """Run the first-person floorplan and allow on-demand C-key captures."""

    api = _load_tdw_api()
    controller: Any | None = None
    try:
        _ensure_port_available(options.port)
        scene_commands, custom_ids = _prepare_scene_and_furniture_commands(
            api, options, furniture
        )
        scene_commands = _prepare_asset_commands(scene_commands, options)
        controller = _create_controller(api, options)
        _load_prepared_scene(controller, scene_commands, options.settle_frames)

        # Render order is assigned at construction time. Construct the top
        # camera first so the later first-person avatar remains on-screen.
        top_camera = api.ThirdPersonCamera(
            avatar_id="top",
            position=options.top_position.as_dict(),
            look_at=options.top_look_at.as_dict(),
            field_of_view=options.top_field_of_view,
        )
        ego = api.FirstPersonAvatar(
            avatar_id="ego",
            position=options.ego_position.as_dict(),
            rotation=options.ego_rotation,
            field_of_view=options.ego_field_of_view,
            height=options.ego_height,
            camera_height=options.ego_camera_height,
            move_speed=options.move_speed,
            look_speed=options.look_speed,
            framerate=options.framerate,
            radius=options.ego_radius,
        )
        avatar_ids = ["top", "ego"] if options.capture_ego else ["top"]
        capture = api.ImageCapture(
            path=options.output,
            avatar_ids=avatar_ids,
            pass_masks=PASS_MASKS,
            png=True,
        )
        _prime_one_shot_capture(capture, avatar_ids)
        metadata = api.SceneMetadata()
        keyboard = api.Keyboard()
        state = {"done": False, "capture_pending": False, "capture_wait_frames": 0}
        top_view_canvas_id = (
            int(api.Controller.get_unique_id()) if options.show_top_view else None
        )
        top_view_ui_id: int | None = None

        def stop() -> None:
            state["done"] = True

        def schedule_capture() -> None:
            if state["capture_pending"]:
                return
            state["capture_pending"] = True
            state["capture_wait_frames"] = 0
            capture.set(
                frequency="once",
                avatar_ids=avatar_ids,
                pass_masks=PASS_MASKS,
            )
            print("Capture scheduled...")

        keyboard.listen(key="Escape", function=stop)
        keyboard.listen(key="C", function=schedule_capture)
        keyboard.listen(key="c", function=schedule_capture)
        # Initialize the visible first-person camera before requesting the six
        # RGB/depth/ID passes. On software renderers this gives Unity a chance
        # to paint the window instead of remaining black during a slow initial
        # multi-camera capture.
        controller.add_ons.extend([top_camera, ego, metadata, keyboard])
        controller.communicate(_display_commands(options))
        print("First-person view initialized; capturing the initial views...", flush=True)
        controller.add_ons.append(capture)
        controller.communicate([])

        _validate_capture(capture, avatar_ids)
        custom_names = {object_id: name for name, object_id in custom_ids.items()}
        first_frame = max(0, capture.frame - 1)
        _write_capture_artifacts(
            options=options,
            capture=capture,
            metadata=metadata,
            custom_names=custom_names,
            frame=first_frame,
            api=api,
        )
        overlay_commands, top_view_ui_id = _top_view_overlay_commands(
            options=options,
            capture=capture,
            api=api,
            canvas_id=top_view_canvas_id,
            previous_ui_id=top_view_ui_id,
        )
        _stop_image_capture(capture)
        controller.communicate(overlay_commands)
        controller.add_ons.remove(top_camera)
        state["capture_pending"] = False

        print(
            "Controls: W/A/S/D move, mouse looks, C captures and refreshes the top view, "
            "Escape or right-click exits."
        )
        print(f"Output: {options.output.resolve()}")
        previous_frame_count = capture.frame
        while not state["done"]:
            controller.communicate([])
            if capture.frame > previous_frame_count:
                _validate_capture(capture, avatar_ids)
                frame = capture.frame - 1
                _write_capture_artifacts(
                    options=options,
                    capture=capture,
                    metadata=metadata,
                    custom_names=custom_names,
                    frame=frame,
                    api=api,
                    save_metadata=False,
                )
                previous_frame_count = capture.frame
                state["capture_pending"] = False
                state["capture_wait_frames"] = 0
                overlay_commands, top_view_ui_id = _top_view_overlay_commands(
                    options=options,
                    capture=capture,
                    api=api,
                    canvas_id=top_view_canvas_id,
                    previous_ui_id=top_view_ui_id,
                )
                _stop_image_capture(capture)
                controller.communicate(overlay_commands)
                print(f"Saved capture frame {frame:04d}")
            elif state["capture_pending"]:
                state["capture_wait_frames"] += 1
                if state["capture_wait_frames"] >= CAPTURE_TIMEOUT_FRAMES:
                    state["capture_pending"] = False
                    state["capture_wait_frames"] = 0
                    _stop_image_capture(capture)
                    controller.communicate([])
                    print(
                        "Capture timed out; press C to retry.",
                        file=sys.stderr,
                    )
            if ego.right_button_pressed:
                state["done"] = True
    finally:
        _terminate(controller)


def run_pose_capture(
    options: InteractiveOptions,
    furniture: Sequence[FurnitureItem],
    poses: Sequence[CapturePose],
) -> None:
    """Capture deterministic ego/top frames at several open-space poses and exit."""

    if not poses:
        raise ValueError("At least one capture pose is required")
    api = _load_tdw_api()
    controller: Any | None = None
    try:
        _ensure_port_available(options.port)
        scene_commands, custom_ids = _prepare_scene_and_furniture_commands(
            api, options, furniture
        )
        scene_commands = _prepare_asset_commands(scene_commands, options)
        controller = _create_controller(api, options)
        _load_prepared_scene(controller, scene_commands, options.settle_frames)

        first_pose = poses[0]
        first_top_position, first_top_look_at = _resolve_top_pose(options, first_pose)
        top_camera = api.ThirdPersonCamera(
            avatar_id="top",
            position=first_top_position.as_dict(),
            look_at=first_top_look_at.as_dict(),
            field_of_view=options.top_field_of_view,
        )
        ego = api.FirstPersonAvatar(
            avatar_id="ego",
            position=first_pose.position.as_dict(),
            rotation=first_pose.rotation,
            field_of_view=options.ego_field_of_view,
            height=options.ego_height,
            camera_height=options.ego_camera_height,
            move_speed=options.move_speed,
            look_speed=options.look_speed,
            framerate=options.framerate,
            radius=options.ego_radius,
        )
        avatar_ids = ["top", "ego"] if options.capture_ego else ["top"]
        capture = api.ImageCapture(
            path=options.output,
            avatar_ids=avatar_ids,
            pass_masks=PASS_MASKS,
            png=True,
        )
        _prime_one_shot_capture(capture, avatar_ids)
        metadata = api.SceneMetadata()
        controller.add_ons.extend([top_camera, ego, metadata])
        controller.communicate(_display_commands(options))
        controller.add_ons.append(capture)

        custom_names = {object_id: name for name, object_id in custom_ids.items()}
        for pose_index, pose in enumerate(poses):
            if pose_index == 0:
                controller.communicate([])
            else:
                # A one-shot request stops transferring images by itself. Keep
                # both sensors enabled between poses; _stop_image_capture()
                # deliberately disables the top sensor for interactive mode,
                # which would make later batch captures return only ego data.
                top_position, top_look_at = _resolve_top_pose(options, pose)
                top_camera.teleport(position=top_position.as_dict(), absolute=True)
                top_camera.look_at(target=top_look_at.as_dict())
                controller.communicate(_avatar_pose_commands(pose))
                controller.communicate([])
                capture.set(
                    frequency="once",
                    avatar_ids=avatar_ids,
                    pass_masks=PASS_MASKS,
                )
                controller.communicate([])

            _validate_capture(capture, avatar_ids)
            frame = capture.frame - 1
            _write_capture_artifacts(
                options=options,
                capture=capture,
                metadata=metadata,
                custom_names=custom_names,
                frame=frame,
                api=api,
                save_metadata=pose_index == 0,
            )
            print(
                f"Saved pose {pose_index + 1}/{len(poses)} as frame {frame:04d}: "
                f"position={pose.position.as_dict()}, yaw={pose.rotation:g}, "
                f"top_position={_resolve_top_pose(options, pose)[0].as_dict()}",
                flush=True,
            )

        _stop_image_capture(capture)
        controller.communicate([])
        print(f"Multi-view capture complete: {options.output.resolve()}")
    finally:
        _terminate(controller)


def _load_tdw_api() -> _TdwApi:
    try:
        from tdw.add_ons.first_person_avatar import FirstPersonAvatar
        from tdw.add_ons.floorplan import Floorplan
        from tdw.add_ons.image_capture import ImageCapture
        from tdw.add_ons.keyboard import Keyboard
        from tdw.add_ons.third_person_camera import ThirdPersonCamera
        from tdw.controller import Controller
        from tdw.tdw_utils import TDWUtils
        from .scene_metadata import SceneMetadata
    except (ImportError, ModuleNotFoundError) as exc:
        version_hint = ""
        if sys.version_info >= (3, 12):
            version_hint = " TDW 1.13 requires Python 3.11; Python 3.12 removed its 'imp' dependency."
        raise RuntimeDependencyError(
            f"Unable to import the TDW runtime.{version_hint} Install the pinned project dependencies."
        ) from exc
    return _TdwApi(
        Controller=Controller,
        Floorplan=Floorplan,
        FirstPersonAvatar=FirstPersonAvatar,
        ThirdPersonCamera=ThirdPersonCamera,
        ImageCapture=ImageCapture,
        Keyboard=Keyboard,
        TDWUtils=TDWUtils,
        SceneMetadata=SceneMetadata,
    )


def _create_controller(api: _TdwApi, options: RuntimeOptions) -> Any:
    _ensure_port_available(options.port)
    try:
        return api.Controller(
            port=options.port,
            check_version=not options.connect_existing,
            launch_build=not options.connect_existing,
        )
    except Exception as exc:
        # pyzmq's ZMQError doesn't inherit from OSError, but exposes the
        # platform errno. Convert this common startup failure into an
        # actionable application error instead of leaking a traceback.
        if getattr(exc, "errno", None) == errno.EADDRINUSE:
            raise _port_in_use_error(options.port) from exc
        raise


def _ensure_port_available(port: int) -> None:
    """Fail before TDW performs its network/version checks if the port is busy."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise _port_in_use_error(port) from exc
        raise


def _port_in_use_error(port: int) -> RuntimeError:
    suggested_port = port + 1 if port < 65535 else port - 1
    return RuntimeError(
        f"TDW port {port} is already in use. Stop the other TDW controller or "
        f"start this one on another free port, for example: --port {suggested_port}"
    )


def _prepare_scene_and_furniture_commands(
    api: _TdwApi,
    options: RuntimeOptions,
    furniture: Sequence[FurnitureItem],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build all scene commands without starting or contacting Unity."""

    if options.layout is None:
        scene_commands = [
            api.Controller.get_add_scene(scene_name=f"floorplan_{options.scene}")
        ]
    else:
        floorplan = api.Floorplan()
        floorplan.init_scene(scene=options.scene, layout=options.layout)
        scene_commands = list(floorplan.commands)

    furniture_commands, custom_ids = build_furniture_commands(api.Controller, furniture)
    scene_commands.extend(furniture_commands)
    return scene_commands, custom_ids


def _prepare_asset_commands(
    commands: list[dict[str, Any]], options: RuntimeOptions
) -> list[dict[str, Any]]:
    """Apply the selected TDW AssetBundle transport mode before Unity starts."""

    if options.asset_mode == "https":
        return commands
    if options.asset_mode == "http":
        print(
            "Warning: --asset-mode http disables transport encryption for TDW assets.",
            file=sys.stderr,
            flush=True,
        )
        return rewrite_tdw_asset_urls_to_http(commands)
    if options.asset_mode != "cache":
        raise ValueError(f"Unsupported TDW asset mode: {options.asset_mode!r}")

    print(
        f"Preparing TDW assets in {options.asset_cache_dir.expanduser()} ...",
        flush=True,
    )
    prepared = cache_tdw_asset_urls(
        commands,
        options.asset_cache_dir,
        progress=lambda message: print(message, flush=True),
    )
    print("TDW assets are ready; starting Unity...", flush=True)
    return prepared


def _load_prepared_scene(
    controller: Any, commands: list[dict[str, Any]], settle_frames: int
) -> None:
    """Send already-localized scene commands to Unity and let physics settle."""

    print("Loading the prepared TDW scene...", flush=True)
    controller.communicate(commands)
    for _ in range(settle_frames):
        controller.communicate([])


def _display_commands(options: RuntimeOptions) -> list[dict[str, Any]]:
    return [
        {"$type": "set_screen_size", "width": options.width, "height": options.height},
        {"$type": "set_floorplan_roof", "show": options.show_roof},
    ]


def _stop_image_capture(capture: Any) -> None:
    """Stop image transfer without re-enabling costly multi-pass sensors."""

    # An empty avatar list produces no set_pass_masks commands. This matters
    # because set_pass_masks implicitly enables a sensor, including a top
    # camera that we deliberately disable while the user is walking.
    capture.set(frequency="never", avatar_ids=[], pass_masks=["_img"])
    # Queue these on the capture add-on itself so Controller appends them
    # *after* any pending send_images/set_pass_masks commands from the capture.
    capture.commands.extend(_interactive_idle_render_commands())


def _prime_one_shot_capture(capture: Any, avatar_ids: Sequence[str]) -> None:
    """Initialize ImageCapture as a clean one-shot instead of its default always mode."""

    capture.set(
        frequency="once",
        avatar_ids=list(avatar_ids),
        pass_masks=PASS_MASKS,
    )
    # Before initialization, set() updates the state used by
    # get_initialization_commands() but also queues duplicate commands. Drop
    # only those pre-initialization duplicates; initialization will send the
    # same pass masks and exactly one image request.
    capture.commands.clear()


def _interactive_idle_render_commands() -> list[dict[str, Any]]:
    """Keep only the visible ego RGB sensor active between captures."""

    return [
        {"$type": "set_pass_masks", "pass_masks": ["_img"], "avatar_id": "ego"},
        {"$type": "enable_image_sensor", "enable": False, "avatar_id": "top"},
    ]


def _avatar_pose_commands(pose: CapturePose) -> list[dict[str, Any]]:
    """Move/rotate the avatar, then clear any local mouse-look rotation."""

    half_yaw = math.radians(pose.rotation) / 2
    return [
        {
            "$type": "teleport_avatar_to",
            "position": pose.position.as_dict(),
            "avatar_id": "ego",
        },
        {
            "$type": "rotate_avatar_to",
            "rotation": {
                "x": 0,
                "y": math.sin(half_yaw),
                "z": 0,
                "w": math.cos(half_yaw),
            },
            "avatar_id": "ego",
        },
        {"$type": "reset_sensor_container_rotation", "avatar_id": "ego"},
    ]


def _resolve_top_pose(
    options: RuntimeOptions, pose: CapturePose
) -> tuple[Vector3, Vector3]:
    """Return per-capture top-camera values, falling back to global options."""

    return (
        pose.top_position if pose.top_position is not None else options.top_position,
        pose.top_look_at if pose.top_look_at is not None else options.top_look_at,
    )


def _top_view_overlay_commands(
    *,
    options: InteractiveOptions,
    capture: Any,
    api: _TdwApi,
    canvas_id: int | None,
    previous_ui_id: int | None,
) -> tuple[list[dict[str, Any]], int | None]:
    """Create or refresh a lightweight top-right top-view overlay."""

    if not options.show_top_view:
        return [], None
    if canvas_id is None:
        raise RuntimeError("Top-view overlay is enabled but has no canvas ID")
    images = capture.images.get("top")
    if images is None:
        raise RuntimeError("TDW returned no top image for the top-view overlay")
    image_index = next(
        (
            index
            for index in range(images.get_num_passes())
            if images.get_pass_mask(index) == "_img"
        ),
        None,
    )
    if image_index is None:
        raise RuntimeError("TDW top-view capture is missing the RGB pass")

    source = api.TDWUtils.get_pil_image(images=images, index=image_index)
    source.load()
    thumbnail = source.convert("RGB")
    source.close()
    margin = 12
    max_width = max(1, min(options.top_view_width, options.width - 2 * margin))
    max_height = max(1, options.height // 2 - margin)
    thumbnail.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    with BytesIO() as encoded:
        thumbnail.save(encoded, "PNG")
        image_data = b64encode(encoded.getvalue()).decode("ascii")

    commands: list[dict[str, Any]] = []
    if previous_ui_id is None:
        commands.append({"$type": "add_ui_canvas", "canvas_id": canvas_id})
    else:
        commands.append(
            {
                "$type": "destroy_ui_element",
                "id": previous_ui_id,
                "canvas_id": canvas_id,
            }
        )
    ui_id = int(api.Controller.get_unique_id())
    commands.append(
        {
            "$type": "add_ui_image",
            "canvas_id": canvas_id,
            "id": ui_id,
            "image": image_data,
            "size": {"x": thumbnail.width, "y": thumbnail.height},
            "rgba": False,
            "scale_factor": {"x": 1, "y": 1},
            "anchor": {"x": 1, "y": 1},
            "pivot": {"x": 1, "y": 1},
            "position": {"x": -margin, "y": -margin},
            "color": {"r": 1, "g": 1, "b": 1, "a": 1},
            "raycast_target": False,
        }
    )
    return commands, ui_id


def _write_capture_artifacts(
    *,
    options: RuntimeOptions,
    capture: Any,
    metadata: Any,
    custom_names: dict[int, str],
    frame: int,
    api: _TdwApi,
    save_metadata: bool = True,
) -> None:
    options.output.mkdir(parents=True, exist_ok=True)
    _save_metric_depth(capture=capture, output=options.output, frame=frame, api=api)
    if save_metadata:
        metadata.save(options.output / "scene_objects.json", custom_names=custom_names)
    if not options.annotations:
        return

    rgb_path = options.output / "top" / f"img_{frame:04d}.png"
    id_path = options.output / "top" / f"id_{frame:04d}.png"
    if not rgb_path.exists() or not id_path.exists():
        print(
            f"Warning: can't annotate frame {frame:04d}; missing {rgb_path.name} or {id_path.name}",
            file=sys.stderr,
        )
        return
    color_to_id = metadata.color_to_object_id()
    if not color_to_id:
        print("Warning: scene contains no segmented objects", file=sys.stderr)
    labels = metadata.labels(custom_names)
    rendered, annotations = annotate_image(
        rgb_image=rgb_path,
        id_image=id_path,
        color_to_object_id=color_to_id,
        object_labels=labels,
        min_pixels=options.min_annotation_pixels,
        show_object_id=True,
    )
    rendered.save(options.output / "top" / f"annotated_{frame:04d}.png")
    save_annotations_json(
        annotations,
        options.output / "top" / f"annotations_{frame:04d}.json",
        image_size=rendered.size,
    )


def _save_metric_depth(*, capture: Any, output: Path, frame: int, api: _TdwApi) -> None:
    import numpy as np

    for avatar_id, images in capture.images.items():
        for pass_index in range(images.get_num_passes()):
            if images.get_pass_mask(pass_index) != "_depth":
                continue
            values = api.TDWUtils.get_depth_values(
                image=images.get_image(pass_index),
                depth_pass="_depth",
                width=images.get_width(),
                height=images.get_height(),
            )
            avatar_output = output / avatar_id
            avatar_output.mkdir(parents=True, exist_ok=True)
            np.save(avatar_output / f"depth_meters_{frame:04d}.npy", values)


def _validate_capture(capture: Any, avatar_ids: Sequence[str]) -> None:
    for avatar_id in avatar_ids:
        if avatar_id not in capture.images:
            raise RuntimeError(f"TDW returned no image data for avatar {avatar_id!r}")
        images = capture.images[avatar_id]
        actual_passes = {
            images.get_pass_mask(index) for index in range(images.get_num_passes())
        }
        missing = set(PASS_MASKS).difference(actual_passes)
        if missing:
            raise RuntimeError(
                f"TDW capture for avatar {avatar_id!r} is missing passes: "
                f"{', '.join(sorted(missing))}"
            )


def _terminate(controller: Any | None) -> None:
    if controller is None:
        return
    with suppress(Exception):
        controller.communicate({"$type": "terminate"})
