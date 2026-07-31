import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tdw_custom_house.instance_annotations import (
    BoundingBox,
    annotate_image,
    draw_instance_annotations,
    extract_instance_annotations,
    mapping_from_tdw_segmentation_colors,
    save_annotations_json,
)


class _FakeSegmentationColors:
    def __init__(self) -> None:
        self._objects = [
            (19, np.array([12, 34, 56], dtype=np.uint8), "chair"),
            (7, np.array([200, 100, 3], dtype=np.uint8), "table"),
        ]

    def get_num(self) -> int:
        return len(self._objects)

    def get_object_id(self, index: int) -> int:
        return self._objects[index][0]

    def get_object_color(self, index: int) -> np.ndarray:
        return self._objects[index][1]

    def get_object_name(self, index: int) -> str:
        return self._objects[index][2]


class InstanceAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.id_image = np.zeros((6, 8, 3), dtype=np.uint8)
        self.id_image[1:3, 1:4] = (10, 20, 30)
        self.id_image[4, 5] = (10, 20, 30)
        self.id_image[0:2, 6:8] = (200, 100, 50)
        self.mapping = {
            (10, 20, 30): 101,
            (200, 100, 50): 9,
            (1, 2, 3): 404,
        }

    def test_extracts_visible_objects_and_inclusive_boxes(self) -> None:
        annotations = extract_instance_annotations(
            self.id_image,
            self.mapping,
            object_labels={101: "chair", 9: "table", 404: "not visible"},
        )

        self.assertEqual([annotation.object_id for annotation in annotations], [9, 101])
        table, chair = annotations
        self.assertEqual(table.label, "table")
        self.assertEqual(table.bbox, BoundingBox(6, 0, 7, 1))
        self.assertEqual(table.bbox.as_xywh(), (6, 0, 2, 2))
        self.assertEqual(table.pixel_count, 4)
        self.assertEqual(chair.label, "chair")
        self.assertEqual(chair.bbox.as_xyxy(), (1, 1, 5, 4))
        self.assertEqual(chair.pixel_count, 7)

    def test_min_pixels_and_default_label(self) -> None:
        annotations = extract_instance_annotations(
            self.id_image,
            self.mapping,
            min_pixels=5,
        )

        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0].object_id, 101)
        self.assertEqual(annotations[0].label, "101")

    def test_accepts_pillow_and_rgba_images(self) -> None:
        rgba = np.dstack(
            [self.id_image, np.full(self.id_image.shape[:2], 255, dtype=np.uint8)]
        )
        from_rgba = extract_instance_annotations(rgba, self.mapping)
        from_pillow = extract_instance_annotations(Image.fromarray(self.id_image), self.mapping)

        self.assertEqual(from_rgba, from_pillow)

    def test_tdw_mapping_adapter(self) -> None:
        color_mapping, labels = mapping_from_tdw_segmentation_colors(
            _FakeSegmentationColors()
        )

        self.assertEqual(color_mapping, {(12, 34, 56): 19, (200, 100, 3): 7})
        self.assertEqual(labels, {19: "chair", 7: "table"})

    def test_draws_without_mutating_source(self) -> None:
        annotations = extract_instance_annotations(self.id_image, self.mapping)
        rgb = np.full_like(self.id_image, 240)
        original = rgb.copy()
        rendered = draw_instance_annotations(
            rgb,
            annotations,
            box_color=(255, 0, 0),
            line_width=1,
        )

        self.assertTrue(np.array_equal(rgb, original))
        self.assertEqual(rendered.mode, "RGB")
        self.assertEqual(rendered.size, (8, 6))
        self.assertEqual(rendered.getpixel((6, 0)), (255, 0, 0))

    def test_draws_unicode_label_with_supported_pillow(self) -> None:
        annotations = extract_instance_annotations(
            self.id_image,
            self.mapping,
            object_labels={101: "茶壶", 9: "桌子"},
        )
        rendered = draw_instance_annotations(
            np.full_like(self.id_image, 255),
            annotations,
        )
        self.assertEqual(rendered.size, (8, 6))

    def test_combined_annotate_api(self) -> None:
        rgb = np.full_like(self.id_image, 255)
        rendered, annotations = annotate_image(
            rgb,
            self.id_image,
            self.mapping,
            {101: "chair", 9: "table"},
            min_pixels=5,
            line_width=1,
        )

        self.assertIsInstance(rendered, Image.Image)
        self.assertEqual([annotation.label for annotation in annotations], ["chair"])

    def test_combined_api_rejects_mismatched_image_sizes(self) -> None:
        rgb = np.zeros((5, 8, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "identical dimensions"):
            annotate_image(rgb, self.id_image, self.mapping)

    def test_saves_json(self) -> None:
        annotations = extract_instance_annotations(self.id_image, self.mapping)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "nested", "annotations.json")
            returned_path = save_annotations_json(
                annotations,
                output,
                image_size=(8, 6),
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(returned_path, output)
        self.assertEqual(payload["image_size"], {"width": 8, "height": 6})
        self.assertEqual(payload["annotations"][0]["bbox_xyxy"], [6, 0, 7, 1])

    def test_rejects_ambiguous_object_color_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "maps from both"):
            extract_instance_annotations(
                self.id_image,
                {(10, 20, 30): 1, (30, 20, 10): 1},
            )

    def test_rejects_non_rgb_array(self) -> None:
        with self.assertRaisesRegex(ValueError, "shape"):
            extract_instance_annotations(
                np.zeros((3, 4), dtype=np.uint8),
                self.mapping,
            )


if __name__ == "__main__":
    unittest.main()
