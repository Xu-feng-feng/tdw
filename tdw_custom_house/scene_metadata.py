"""A lightweight TDW add-on that records one frame of scene metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from tdw.add_ons.add_on import AddOn
from tdw.output_data import Bounds, OutputData, SegmentationColors, StaticRigidbodies, Transforms


class SceneMetadata(AddOn):
    """Request object labels, colors, transforms, bounds, and physics once."""

    def __init__(self) -> None:
        super().__init__()
        self.segmentation_colors: dict[int, tuple[int, int, int]] = {}
        self.names: dict[int, str] = {}
        self.categories: dict[int, str] = {}
        self.positions: dict[int, list[float]] = {}
        self.rotations: dict[int, list[float]] = {}
        self.bounds: dict[int, dict[str, list[float]]] = {}
        self.physics: dict[int, dict[str, Any]] = {}

    def get_initialization_commands(self) -> list[dict[str, Any]]:
        return [
            {"$type": "send_segmentation_colors"},
            {"$type": "send_transforms", "frequency": "once"},
            {"$type": "send_bounds", "frequency": "once"},
            {"$type": "send_static_rigidbodies"},
        ]

    def on_send(self, resp: list[bytes]) -> None:
        for packet in resp[:-1]:
            data_type = OutputData.get_data_type_id(packet)
            if data_type == "segm":
                data = SegmentationColors(packet)
                for index in range(data.get_num()):
                    object_id = data.get_object_id(index)
                    color = data.get_object_color(index)
                    self.segmentation_colors[object_id] = tuple(int(component) for component in color)
                    self.names[object_id] = data.get_object_name(index)
                    self.categories[object_id] = data.get_object_category(index)
            elif data_type == "tran":
                data = Transforms(packet)
                for index in range(data.get_num()):
                    object_id = data.get_id(index)
                    self.positions[object_id] = _float_list(data.get_position(index))
                    self.rotations[object_id] = _float_list(data.get_rotation(index))
            elif data_type == "boun":
                data = Bounds(packet)
                for index in range(data.get_num()):
                    object_id = data.get_id(index)
                    self.bounds[object_id] = {
                        "front": _float_list(data.get_front(index)),
                        "back": _float_list(data.get_back(index)),
                        "left": _float_list(data.get_left(index)),
                        "right": _float_list(data.get_right(index)),
                        "top": _float_list(data.get_top(index)),
                        "bottom": _float_list(data.get_bottom(index)),
                        "center": _float_list(data.get_center(index)),
                    }
            elif data_type == "srig":
                data = StaticRigidbodies(packet)
                for index in range(data.get_num()):
                    self.physics[data.get_id(index)] = {
                        "mass": float(data.get_mass(index)),
                        "kinematic": bool(data.get_kinematic(index)),
                        "dynamic_friction": float(data.get_dynamic_friction(index)),
                        "static_friction": float(data.get_static_friction(index)),
                        "bounciness": float(data.get_bounciness(index)),
                    }

    def color_to_object_id(self) -> dict[tuple[int, int, int], int]:
        return {color: object_id for object_id, color in self.segmentation_colors.items()}

    def labels(self, custom_names: Mapping[int, str] | None = None) -> dict[int, str]:
        labels = dict(self.names)
        if custom_names:
            labels.update({int(object_id): str(name) for object_id, name in custom_names.items()})
        return labels

    def records(self, custom_names: Mapping[int, str] | None = None) -> list[dict[str, Any]]:
        labels = self.labels(custom_names)
        records: list[dict[str, Any]] = []
        for object_id in sorted(self.segmentation_colors):
            record: dict[str, Any] = {
                "object_id": object_id,
                "name": self.names.get(object_id, str(object_id)),
                "label": labels.get(object_id, str(object_id)),
                "category": self.categories.get(object_id, ""),
                "segmentation_color": list(self.segmentation_colors[object_id]),
            }
            if object_id in self.positions:
                record["position"] = self.positions[object_id]
            if object_id in self.rotations:
                record["rotation_xyzw"] = self.rotations[object_id]
            if object_id in self.bounds:
                record["bounds"] = self.bounds[object_id]
            if object_id in self.physics:
                record["physics"] = self.physics[object_id]
            records.append(record)
        return records

    def save(
        self,
        path: str | Path,
        custom_names: Mapping[int, str] | None = None,
    ) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"objects": self.records(custom_names)}
        with output.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        return output


def _float_list(values: Any) -> list[float]:
    return [float(value) for value in values]
