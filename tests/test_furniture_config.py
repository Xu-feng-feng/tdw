import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tdw_custom_house.config import (
    FurnitureConfigError,
    build_furniture_commands,
    load_furniture_config,
)


class _FakeController:
    def __init__(self) -> None:
        self.next_id = 100
        self.calls: list[dict[str, Any]] = []

    def get_unique_id(self) -> int:
        result = self.next_id
        self.next_id += 1
        return result

    def get_add_physics_object(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return [{"$type": "add_object", "id": kwargs["object_id"]}]


class FurnitureConfigTests(unittest.TestCase):
    def _load(self, payload: Any):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "furniture.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_furniture_config(path)

    def test_loads_scale_and_custom_physics(self) -> None:
        items = self._load(
            [
                {
                    "name": "table",
                    "model_name": "table_square",
                    "position": {"x": 1, "y": 0, "z": 2},
                    "scale": 1.25,
                    "physics": {
                        "mass": 20,
                        "dynamic_friction": 0.4,
                        "static_friction": 0.5,
                        "bounciness": 0.1,
                        "kinematic": True,
                        "gravity": False,
                    },
                }
            ]
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.scale.as_dict(), {"x": 1.25, "y": 1.25, "z": 1.25})
        self.assertFalse(item.physics.use_default_values)
        self.assertEqual(item.physics.mass, 20)
        self.assertTrue(item.physics.kinematic)
        self.assertFalse(item.physics.gravity)

    def test_builds_physics_commands_and_skips_disabled_items(self) -> None:
        items = self._load(
            [
                {
                    "name": "active",
                    "model_name": "jug01",
                    "position": {"x": 0, "y": 1, "z": 0},
                    "rotation": {"x": 0, "y": 90, "z": 0},
                },
                {
                    "name": "disabled",
                    "model_name": "wood_chair",
                    "position": {"x": 0, "y": 0, "z": 0},
                    "enabled": False,
                },
            ]
        )
        controller = _FakeController()

        commands, object_ids = build_furniture_commands(controller, items)

        self.assertEqual(commands, [{"$type": "add_object", "id": 100}])
        self.assertEqual(object_ids, {"active": 100})
        self.assertEqual(len(controller.calls), 1)
        call = controller.calls[0]
        self.assertEqual(call["model_name"], "jug01")
        self.assertEqual(call["rotation"], {"x": 0.0, "y": 90.0, "z": 0.0})
        self.assertTrue(call["default_physics_values"])

    def test_rejects_duplicate_names(self) -> None:
        item = {
            "name": "same",
            "model_name": "jug01",
            "position": {"x": 0, "y": 0, "z": 0},
        }
        with self.assertRaisesRegex(FurnitureConfigError, "Duplicate"):
            self._load([item, item])

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(FurnitureConfigError, "unknown fields: positon"):
            self._load(
                [
                    {
                        "name": "jug",
                        "model_name": "jug01",
                        "position": {"x": 0, "y": 0, "z": 0},
                        "positon": {},
                    }
                ]
            )

    def test_rejects_ignored_custom_physics(self) -> None:
        with self.assertRaisesRegex(FurnitureConfigError, "would be ignored"):
            self._load(
                [
                    {
                        "name": "jug",
                        "model_name": "jug01",
                        "position": {"x": 0, "y": 0, "z": 0},
                        "physics": {"use_default_values": True, "mass": 2},
                    }
                ]
            )

    def test_rejects_invalid_root_and_vector(self) -> None:
        with self.assertRaisesRegex(FurnitureConfigError, "root"):
            self._load({})
        with self.assertRaisesRegex(FurnitureConfigError, "missing: z"):
            self._load(
                [
                    {
                        "name": "jug",
                        "model_name": "jug01",
                        "position": {"x": 0, "y": 0},
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
