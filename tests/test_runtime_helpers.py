import errno
import base64
import unittest
from dataclasses import fields, replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from tdw_custom_house.config import Vector3
from tdw_custom_house.runtime import (
    PASS_MASKS,
    CapturePose,
    InteractiveOptions,
    RuntimeOptions,
    _create_controller,
    _avatar_pose_commands,
    _ensure_port_available,
    _load_prepared_scene,
    _interactive_idle_render_commands,
    _prepare_asset_commands,
    _prepare_scene_and_furniture_commands,
    _prime_one_shot_capture,
    _resolve_top_pose,
    _stop_image_capture,
    _top_view_overlay_commands,
    _validate_capture,
    run_interactive,
    run_top_capture,
)


class _Images:
    def __init__(self, passes):
        self.passes = passes

    def get_num_passes(self):
        return len(self.passes)

    def get_pass_mask(self, index):
        return self.passes[index]


class _Capture:
    def __init__(self, images):
        self.images = images


class _AddressInUseController:
    def __init__(self, **_kwargs):
        error = RuntimeError("Address already in use")
        error.errno = errno.EADDRINUSE
        raise error


class _Api:
    Controller = _AddressInUseController


class _CommandController:
    @staticmethod
    def get_add_scene(*, scene_name):
        return {"$type": "add_scene", "name": scene_name, "url": "https://scene"}


class _CommandFloorplan:
    def __init__(self):
        self.commands = []

    def init_scene(self, *, scene, layout):
        self.commands = [
            {
                "$type": "add_scene",
                "name": f"floorplan_{scene}",
                "layout": layout,
                "url": "https://tdw-public.s3.amazonaws.com/scenes/linux/scene",
            }
        ]


class _CommandApi:
    Controller = _CommandController
    Floorplan = _CommandFloorplan


class _RecordingController:
    def __init__(self):
        self.calls = []

    def communicate(self, commands):
        self.calls.append(commands)


class _StopBeforeController(RuntimeError):
    pass


class _StopAfterAvatar(RuntimeError):
    pass


class _OverlayImages:
    def get_num_passes(self):
        return 1

    def get_pass_mask(self, _index):
        return "_img"


class _OverlayCapture:
    images = {"top": _OverlayImages()}


class _OverlayTdwUtils:
    @staticmethod
    def get_pil_image(*, images, index):
        return Image.new("RGB", (1280, 720), color=(20, 40, 60))


class _OverlayController:
    next_id = 700

    @classmethod
    def get_unique_id(cls):
        cls.next_id += 1
        return cls.next_id


class _OverlayApi:
    TDWUtils = _OverlayTdwUtils
    Controller = _OverlayController


class _CaptureSettings:
    def __init__(self):
        self.calls = []
        self.commands = []

    def set(self, **kwargs):
        self.calls.append(kwargs)


class _PrimedCapture(_CaptureSettings):
    def set(self, **kwargs):
        super().set(**kwargs)
        self.commands.extend([{"$type": "set_pass_masks"}, {"$type": "send_images"}])


class _BusySocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def bind(self, _address):
        raise OSError(errno.EADDRINUSE, "Address already in use")


def _runtime_options() -> RuntimeOptions:
    return RuntimeOptions(
        scene="1a",
        layout=0,
        output=Path("output"),
        width=1280,
        height=720,
        top_position=Vector3(0, 40, 0),
        top_look_at=Vector3(0, 0, 0),
        top_field_of_view=60,
        port=1071,
        connect_existing=False,
        show_roof=False,
        settle_frames=0,
        annotations=True,
        min_annotation_pixels=8,
    )


def _interactive_options() -> InteractiveOptions:
    base = _runtime_options()
    common = {item.name: getattr(base, item.name) for item in fields(RuntimeOptions)}
    return InteractiveOptions(
        **common,
        ego_position=Vector3(1.5, 0, -4.2),
        ego_rotation=30,
        ego_field_of_view=75,
        ego_height=1.9,
        ego_camera_height=1.8,
        move_speed=1.5,
        look_speed=50,
        framerate=60,
        ego_radius=0.35,
        capture_ego=True,
        show_top_view=True,
        top_view_width=320,
    )


class RuntimeHelperTests(unittest.TestCase):
    @patch("tdw_custom_house.runtime.socket.socket", return_value=_BusySocket())
    def test_detects_busy_port_before_controller_startup(self, _socket) -> None:
        with self.assertRaisesRegex(RuntimeError, r"port 1071.*--port 1072"):
            _ensure_port_available(1071)

    def test_reports_an_actionable_error_when_port_is_in_use(self) -> None:
        with patch("tdw_custom_house.runtime._ensure_port_available"):
            with self.assertRaisesRegex(RuntimeError, r"port 1071.*--port 1072"):
                _create_controller(_Api(), _runtime_options())  # type: ignore[arg-type]

    def test_validates_expected_capture_passes(self) -> None:
        _validate_capture(_Capture({"top": _Images(PASS_MASKS)}), ["top"])

    def test_rejects_missing_avatar_or_pass(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no image data"):
            _validate_capture(_Capture({}), ["top"])
        with self.assertRaisesRegex(RuntimeError, "missing passes: _id"):
            _validate_capture(_Capture({"top": _Images(["_img", "_depth"])}), ["top"])

    def test_prepares_floorplan_commands_without_a_live_controller(self) -> None:
        commands, custom_ids = _prepare_scene_and_furniture_commands(
            _CommandApi(), _runtime_options(), []  # type: ignore[arg-type]
        )

        self.assertEqual(commands[0]["name"], "floorplan_1a")
        self.assertEqual(commands[0]["layout"], 0)
        self.assertEqual(custom_ids, {})

    def test_prepares_empty_floorplan_geometry(self) -> None:
        options = replace(_runtime_options(), layout=None)
        commands, _ = _prepare_scene_and_furniture_commands(
            _CommandApi(), options, []  # type: ignore[arg-type]
        )

        self.assertEqual(commands, [_CommandController.get_add_scene(scene_name="floorplan_1a")])

    def test_cache_mode_localizes_commands_before_launch(self) -> None:
        options = replace(_runtime_options(), asset_cache_dir=Path("cache"))
        commands = [{"url": "https://tdw-public.s3.amazonaws.com/asset"}]
        localized = [{"url": "file:///cache/asset"}]
        with patch(
            "tdw_custom_house.runtime.cache_tdw_asset_urls", return_value=localized
        ) as cache, patch("builtins.print"):
            result = _prepare_asset_commands(commands, options)

        self.assertEqual(result, localized)
        cache.assert_called_once()
        self.assertEqual(cache.call_args.args, (commands, Path("cache")))

    def test_direct_asset_modes_bypass_the_python_downloader(self) -> None:
        commands = [{"url": "https://tdw-public.s3.amazonaws.com/asset"}]
        with patch("tdw_custom_house.runtime.cache_tdw_asset_urls") as cache:
            https_result = _prepare_asset_commands(
                commands, replace(_runtime_options(), asset_mode="https")
            )
        self.assertIs(https_result, commands)
        cache.assert_not_called()

        http_result = [{"url": "http://tdw-public.s3.amazonaws.com/asset"}]
        with patch(
            "tdw_custom_house.runtime.rewrite_tdw_asset_urls_to_http",
            return_value=http_result,
        ) as rewrite, patch("builtins.print"):
            result = _prepare_asset_commands(
                commands, replace(_runtime_options(), asset_mode="http")
            )
        self.assertEqual(result, http_result)
        rewrite.assert_called_once_with(commands)

    def test_rejects_unknown_asset_mode(self) -> None:
        options = replace(_runtime_options(), asset_mode="invalid")
        with self.assertRaisesRegex(ValueError, "Unsupported TDW asset mode"):
            _prepare_asset_commands([], options)

    def test_loads_scene_in_one_batch_then_settles(self) -> None:
        controller = _RecordingController()
        commands = [{"$type": "add_scene"}, {"$type": "add_object"}]

        with patch("builtins.print"):
            _load_prepared_scene(controller, commands, settle_frames=2)

        self.assertEqual(controller.calls, [commands, [], []])

    def test_top_capture_prepares_assets_before_constructing_controller(self) -> None:
        events = []

        def prepare_scene(*_args):
            events.append("commands")
            return ([{"$type": "add_scene"}], {})

        def prepare_assets(commands, _options):
            events.append("assets")
            return commands

        def create_controller(*_args):
            events.append("controller")
            raise _StopBeforeController()

        with patch("tdw_custom_house.runtime._load_tdw_api", return_value=object()), patch(
            "tdw_custom_house.runtime._ensure_port_available"
        ), patch(
            "tdw_custom_house.runtime._prepare_scene_and_furniture_commands",
            side_effect=prepare_scene,
        ), patch(
            "tdw_custom_house.runtime._prepare_asset_commands",
            side_effect=prepare_assets,
        ), patch(
            "tdw_custom_house.runtime._create_controller",
            side_effect=create_controller,
        ):
            with self.assertRaises(_StopBeforeController):
                run_top_capture(_runtime_options(), [])

        self.assertEqual(events, ["commands", "assets", "controller"])

    def test_idle_rendering_disables_top_and_keeps_only_ego_rgb(self) -> None:
        self.assertEqual(
            _interactive_idle_render_commands(),
            [
                {
                    "$type": "set_pass_masks",
                    "pass_masks": ["_img"],
                    "avatar_id": "ego",
                },
                {
                    "$type": "enable_image_sensor",
                    "enable": False,
                    "avatar_id": "top",
                },
            ],
        )

    def test_builds_deterministic_avatar_pose_commands(self) -> None:
        pose = CapturePose(position=Vector3(2.5, 0, -3.5), rotation=270)

        self.assertEqual(
            _avatar_pose_commands(pose),
            [
                {
                    "$type": "teleport_avatar_to",
                    "position": {"x": 2.5, "y": 0.0, "z": -3.5},
                    "avatar_id": "ego",
                },
                {
                    "$type": "rotate_avatar_to",
                    "rotation": {
                        "x": 0,
                        "y": 0.7071067811865476,
                        "z": 0,
                        "w": -0.7071067811865475,
                    },
                    "avatar_id": "ego",
                },
                {"$type": "reset_sensor_container_rotation", "avatar_id": "ego"},
            ],
        )

    def test_resolves_optional_per_capture_top_pose(self) -> None:
        options = _runtime_options()
        default_pose = CapturePose(position=Vector3(1, 0, 2), rotation=90)
        custom_pose = CapturePose(
            position=Vector3(1, 0, 2),
            rotation=90,
            top_position=Vector3(-4, 39, -4),
            top_look_at=Vector3(-0.5, 0, 0),
        )

        self.assertEqual(
            _resolve_top_pose(options, default_pose),
            (options.top_position, options.top_look_at),
        )
        self.assertEqual(
            _resolve_top_pose(options, custom_pose),
            (Vector3(-4, 39, -4), Vector3(-0.5, 0, 0)),
        )

    def test_stopping_capture_does_not_emit_pass_masks_for_any_avatar(self) -> None:
        capture = _CaptureSettings()

        _stop_image_capture(capture)

        self.assertEqual(
            capture.calls,
            [{"frequency": "never", "avatar_ids": [], "pass_masks": ["_img"]}],
        )
        self.assertEqual(capture.commands, _interactive_idle_render_commands())

    def test_primes_initial_capture_as_a_clean_one_shot(self) -> None:
        capture = _PrimedCapture()

        _prime_one_shot_capture(capture, ["top", "ego"])

        self.assertEqual(
            capture.calls,
            [
                {
                    "frequency": "once",
                    "avatar_ids": ["top", "ego"],
                    "pass_masks": PASS_MASKS,
                }
            ],
        )
        self.assertEqual(capture.commands, [])

    def test_builds_and_refreshes_top_view_overlay(self) -> None:
        options = _interactive_options()
        commands, ui_id = _top_view_overlay_commands(
            options=options,
            capture=_OverlayCapture(),
            api=_OverlayApi(),  # type: ignore[arg-type]
            canvas_id=55,
            previous_ui_id=None,
        )

        self.assertEqual(commands[0], {"$type": "add_ui_canvas", "canvas_id": 55})
        self.assertEqual(commands[1]["$type"], "add_ui_image")
        self.assertEqual(commands[1]["size"], {"x": 320, "y": 180})
        self.assertFalse(commands[1]["raycast_target"])
        decoded = Image.open(BytesIO(base64.b64decode(commands[1]["image"])))
        self.assertEqual(decoded.size, (320, 180))

        refreshed, new_ui_id = _top_view_overlay_commands(
            options=options,
            capture=_OverlayCapture(),
            api=_OverlayApi(),  # type: ignore[arg-type]
            canvas_id=55,
            previous_ui_id=ui_id,
        )
        self.assertEqual(
            refreshed[0],
            {"$type": "destroy_ui_element", "id": ui_id, "canvas_id": 55},
        )
        self.assertNotEqual(new_ui_id, ui_id)

    def test_interactive_runtime_passes_raised_avatar_heights(self) -> None:
        first_person = MagicMock(side_effect=_StopAfterAvatar())
        api = SimpleNamespace(
            ThirdPersonCamera=MagicMock(return_value=object()),
            FirstPersonAvatar=first_person,
        )
        controller = _RecordingController()
        with patch("tdw_custom_house.runtime._load_tdw_api", return_value=api), patch(
            "tdw_custom_house.runtime._ensure_port_available"
        ), patch(
            "tdw_custom_house.runtime._prepare_scene_and_furniture_commands",
            return_value=([], {}),
        ), patch(
            "tdw_custom_house.runtime._prepare_asset_commands", return_value=[]
        ), patch(
            "tdw_custom_house.runtime._create_controller", return_value=controller
        ), patch("tdw_custom_house.runtime._load_prepared_scene"):
            with self.assertRaises(_StopAfterAvatar):
                run_interactive(_interactive_options(), [])

        self.assertEqual(first_person.call_args.kwargs["height"], 1.9)
        self.assertEqual(first_person.call_args.kwargs["camera_height"], 1.8)


if __name__ == "__main__":
    unittest.main()
