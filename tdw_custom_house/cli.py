"""Shared command-line parsing for the TDW house applications."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from .asset_cache import default_asset_cache_dir
from .config import Vector3
from .runtime import InteractiveOptions, RuntimeOptions


APP_DIR = Path(__file__).resolve().parent
DEFAULT_FURNITURE_CONFIG = APP_DIR / "furniture_config.json"
DEFAULT_OUTPUT = Path.cwd() / "output"
SCENES = [f"{number}{variant}" for number in (1, 2, 4, 5) for variant in "abc"]


def create_parser(description: str, *, interactive: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--scene", choices=SCENES, default="1a", help="Floorplan geometry variant.")
    parser.add_argument(
        "--layout",
        choices=("empty", "0", "1", "2"),
        default="0",
        help="Preset furniture layout, or 'empty' for geometry only.",
    )
    parser.add_argument(
        "--furniture-config",
        type=Path,
        default=DEFAULT_FURNITURE_CONFIG,
        help="Custom furniture JSON file.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Capture output directory.")
    parser.add_argument("--width", type=_positive_int, default=1280)
    parser.add_argument("--height", type=_positive_int, default=720)
    parser.add_argument(
        "--top-position",
        type=_finite_float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=(0.0, 40.0, 0.0),
    )
    parser.add_argument(
        "--top-look-at",
        type=_finite_float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=(0.0, 0.0, 0.0),
    )
    parser.add_argument("--top-fov", type=_field_of_view, default=60)
    parser.add_argument("--port", type=_port, default=1071)
    parser.add_argument(
        "--asset-mode",
        choices=("cache", "https", "http"),
        default="cache",
        help=(
            "AssetBundle transport: Python-verified local cache (default), "
            "Unity HTTPS, or explicitly insecure Unity HTTP."
        ),
    )
    parser.add_argument(
        "--asset-cache-dir",
        type=Path,
        default=default_asset_cache_dir(),
        help="Persistent directory used by --asset-mode cache.",
    )
    parser.add_argument(
        "--connect-existing",
        action="store_true",
        help="Don't launch a build; wait for an externally started TDW build on --port.",
    )
    parser.add_argument("--show-roof", action="store_true", help="Keep the floorplan roof visible.")
    parser.add_argument("--settle-frames", type=_non_negative_int, default=0)
    parser.add_argument("--no-annotations", dest="annotations", action="store_false")
    parser.add_argument("--min-annotation-pixels", type=_positive_int, default=8)
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate furniture JSON without importing TDW or launching a build.",
    )
    parser.set_defaults(annotations=True)

    if interactive:
        parser.add_argument(
            "--ego-position",
            type=_finite_float,
            nargs=3,
            metavar=("X", "Y", "Z"),
            default=(-3.6, 0.0, 1.8),
            help="First-person spawn point; adjust it for the chosen layout.",
        )
        parser.add_argument(
            "--ego-rotation",
            type=_finite_float,
            default=270.0,
            help="Initial yaw in degrees.",
        )
        parser.add_argument("--ego-fov", type=_field_of_view, default=75)
        parser.add_argument(
            "--ego-height",
            type=_positive_float,
            default=1.9,
            help="First-person collision body height in meters.",
        )
        parser.add_argument(
            "--ego-camera-height",
            type=_positive_float,
            default=1.8,
            help="First-person eye height in meters.",
        )
        parser.add_argument("--move-speed", type=_positive_float, default=1.5)
        parser.add_argument("--look-speed", type=_positive_float, default=50.0)
        parser.add_argument("--framerate", type=_positive_int, default=60)
        parser.add_argument("--ego-radius", type=_positive_float, default=0.35)
        parser.add_argument(
            "--no-ego-capture",
            dest="capture_ego",
            action="store_false",
            help="Capture only the top camera, not the initial/on-demand ego frame.",
        )
        parser.add_argument(
            "--top-view-width",
            type=_positive_int,
            default=384,
            help="Width in pixels of the top-view overlay.",
        )
        parser.add_argument(
            "--no-top-view",
            dest="show_top_view",
            action="store_false",
            help="Don't show the captured top view in the application window.",
        )
        parser.set_defaults(capture_ego=True, show_top_view=True)
    return parser


def options_from_args(args: argparse.Namespace, *, interactive: bool) -> RuntimeOptions:
    common: dict[str, Any] = {
        "scene": args.scene,
        "layout": None if args.layout == "empty" else int(args.layout),
        "output": args.output,
        "width": args.width,
        "height": args.height,
        "top_position": Vector3(*args.top_position),
        "top_look_at": Vector3(*args.top_look_at),
        "top_field_of_view": args.top_fov,
        "port": args.port,
        "connect_existing": args.connect_existing,
        "show_roof": args.show_roof,
        "settle_frames": args.settle_frames,
        "annotations": args.annotations,
        "min_annotation_pixels": args.min_annotation_pixels,
        "asset_mode": args.asset_mode,
        "asset_cache_dir": args.asset_cache_dir,
    }
    if not interactive:
        return RuntimeOptions(**common)
    return InteractiveOptions(
        **common,
        ego_position=Vector3(*args.ego_position),
        ego_rotation=args.ego_rotation,
        ego_field_of_view=args.ego_fov,
        ego_height=args.ego_height,
        ego_camera_height=args.ego_camera_height,
        move_speed=args.move_speed,
        look_speed=args.look_speed,
        framerate=args.framerate,
        ego_radius=args.ego_radius,
        capture_ego=args.capture_ego,
        show_top_view=args.show_top_view,
        top_view_width=args.top_view_width,
    )


def _positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _non_negative_int(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return result


def _positive_float(value: str) -> float:
    result = _finite_float(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return result


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("must be finite")
    return result


def _field_of_view(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 179:
        raise argparse.ArgumentTypeError("must be between 1 and 179 degrees")
    return result


def _port(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return result
