"""Draw labels and boxes from saved TDW RGB/ID/metadata files."""

from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tdw_custom_house.instance_annotations import annotate_image, save_annotations_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw instance boxes on a saved TDW capture.")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "output")
    parser.add_argument("--avatar", default="top")
    parser.add_argument("--frame", type=_non_negative_int, default=0)
    parser.add_argument("--rgb", type=Path, help="Override the RGB image path.")
    parser.add_argument("--id-image", type=Path, help="Override the instance-ID image path.")
    parser.add_argument("--metadata", type=Path, help="Override scene_objects.json.")
    parser.add_argument("--output", type=Path, help="Override annotated PNG output.")
    parser.add_argument("--annotations-json", type=Path, help="Override annotation JSON output.")
    parser.add_argument("--min-pixels", type=_positive_int, default=8)
    parser.add_argument("--hide-object-ids", action="store_true")
    args = parser.parse_args()

    frame_name = f"{args.frame:04d}"
    avatar_dir = args.output_dir / args.avatar
    rgb_path = args.rgb or avatar_dir / f"img_{frame_name}.png"
    id_path = args.id_image or avatar_dir / f"id_{frame_name}.png"
    metadata_path = args.metadata or args.output_dir / "scene_objects.json"
    output_path = args.output or avatar_dir / f"annotated_{frame_name}.png"
    json_path = args.annotations_json or avatar_dir / f"annotations_{frame_name}.json"

    try:
        color_to_id, labels = _load_metadata(metadata_path)
        rendered, annotations = annotate_image(
            rgb_image=rgb_path,
            id_image=id_path,
            color_to_object_id=color_to_id,
            object_labels=labels,
            min_pixels=args.min_pixels,
            show_object_id=not args.hide_object_ids,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rendered.save(output_path)
        save_annotations_json(annotations, json_path, image_size=rendered.size)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {len(annotations)} annotations to {output_path}")
    return 0


def _load_metadata(path: Path) -> tuple[dict[tuple[int, int, int], int], dict[int, str]]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("objects"), list):
        raise ValueError(f"Invalid scene metadata: {path}")

    color_to_id: dict[tuple[int, int, int], int] = {}
    labels: dict[int, str] = {}
    for index, raw_record in enumerate(payload["objects"]):
        if not isinstance(raw_record, dict):
            raise ValueError(f"objects[{index}] must be an object")
        object_id = _plain_int(raw_record.get("object_id"), f"objects[{index}].object_id")
        raw_color: Any = raw_record.get("segmentation_color")
        if (
            not isinstance(raw_color, list)
            or len(raw_color) != 3
            or any(not isinstance(component, int) or isinstance(component, bool) for component in raw_color)
            or any(component < 0 or component > 255 for component in raw_color)
        ):
            raise ValueError(f"objects[{index}].segmentation_color must be an RGB triplet")
        color = tuple(raw_color)
        if color in color_to_id and color_to_id[color] != object_id:
            raise ValueError(f"Segmentation color {color} is duplicated")
        color_to_id[color] = object_id  # type: ignore[index]
        labels[object_id] = str(raw_record.get("label") or raw_record.get("name") or object_id)
    return color_to_id, labels


def _plain_int(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{location} must be an integer")
    return value


def _non_negative_int(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return result


def _positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
