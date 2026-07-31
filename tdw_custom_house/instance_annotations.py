"""Create object annotations from a TDW instance-ID image.

TDW renders each object with a unique RGB value in the ``_id`` pass.  The
``send_segmentation_colors`` command supplies the corresponding RGB-to-object
ID mapping.  This module joins those two pieces of data without requiring a
running TDW build.

Bounding boxes use inclusive ``(x_min, y_min, x_max, y_max)`` coordinates.
This matches the pixels found in the ID image and Pillow's rectangle API.
"""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont


RGBColor = tuple[int, int, int]
ImageSource = Image.Image | np.ndarray | str | PathLike[str]


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """An inclusive axis-aligned image-space bounding box."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    def __post_init__(self) -> None:
        if self.x_min < 0 or self.y_min < 0:
            raise ValueError("Bounding-box coordinates must be non-negative")
        if self.x_max < self.x_min or self.y_max < self.y_min:
            raise ValueError("Bounding-box maxima must not be smaller than minima")

    @property
    def width(self) -> int:
        return self.x_max - self.x_min + 1

    @property
    def height(self) -> int:
        return self.y_max - self.y_min + 1

    def as_xyxy(self) -> tuple[int, int, int, int]:
        return self.x_min, self.y_min, self.x_max, self.y_max

    def as_xywh(self) -> tuple[int, int, int, int]:
        return self.x_min, self.y_min, self.width, self.height


@dataclass(frozen=True, slots=True)
class InstanceAnnotation:
    """The visible image-space annotation for one TDW object."""

    object_id: int
    label: str
    segmentation_color: RGBColor
    bbox: BoundingBox
    pixel_count: int

    def __post_init__(self) -> None:
        if self.pixel_count < 1:
            raise ValueError("pixel_count must be positive")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "object_id": self.object_id,
            "label": self.label,
            "segmentation_color": list(self.segmentation_color),
            "bbox_xyxy": list(self.bbox.as_xyxy()),
            "bbox_xywh": list(self.bbox.as_xywh()),
            "pixel_count": self.pixel_count,
        }


def extract_instance_annotations(
    id_image: ImageSource,
    color_to_object_id: Mapping[Sequence[int], int],
    object_labels: Mapping[int, str] | None = None,
    *,
    min_pixels: int = 1,
) -> list[InstanceAnnotation]:
    """Extract visible object boxes and labels from a TDW ``_id`` pass.

    Parameters
    ----------
    id_image:
        A path, Pillow image, or ``height x width x 3`` integer NumPy array.
        Four-channel NumPy images are also accepted; the alpha channel is
        ignored.
    color_to_object_id:
        Mapping from the exact RGB segmentation color to its TDW object ID.
        Each object ID must have exactly one color, as in TDW's
        ``SegmentationColors`` output.
    object_labels:
        Optional object-ID-to-display-name mapping.  Missing names fall back
        to the decimal object ID.
    min_pixels:
        Ignore objects with fewer than this many visible pixels.

    Returns
    -------
    list[InstanceAnnotation]
        Visible objects sorted by object ID.  Mapped objects absent from the
        image are omitted.
    """

    min_pixels = _positive_integer(min_pixels, "min_pixels")
    rgb = _load_rgb_array(id_image)
    entries = _normalize_color_mapping(color_to_object_id)
    if not entries:
        return []

    height, width, _ = rgb.shape
    packed_pixels = (
        (rgb[..., 0].astype(np.uint32) << np.uint32(16))
        | (rgb[..., 1].astype(np.uint32) << np.uint32(8))
        | rgb[..., 2].astype(np.uint32)
    ).reshape(-1)

    target_colors = np.fromiter(
        (_pack_color(color) for color, _ in entries),
        dtype=np.uint32,
        count=len(entries),
    )
    color_order = np.argsort(target_colors)
    sorted_colors = target_colors[color_order]

    # Match every pixel to a requested segmentation color in one vectorized
    # pass.  This avoids scanning a megapixel image once per scene object.
    sorted_positions = np.searchsorted(sorted_colors, packed_pixels)
    in_range = sorted_positions < len(sorted_colors)
    pixel_indices = np.flatnonzero(in_range)
    sorted_positions = sorted_positions[in_range]
    exact_matches = sorted_colors[sorted_positions] == packed_pixels[in_range]
    pixel_indices = pixel_indices[exact_matches]
    entry_indices = color_order[sorted_positions[exact_matches]]

    if pixel_indices.size == 0:
        return []

    counts = np.bincount(entry_indices, minlength=len(entries))
    y_coordinates, x_coordinates = np.divmod(pixel_indices, width)
    x_min = np.full(len(entries), width, dtype=np.int64)
    y_min = np.full(len(entries), height, dtype=np.int64)
    x_max = np.full(len(entries), -1, dtype=np.int64)
    y_max = np.full(len(entries), -1, dtype=np.int64)
    np.minimum.at(x_min, entry_indices, x_coordinates)
    np.minimum.at(y_min, entry_indices, y_coordinates)
    np.maximum.at(x_max, entry_indices, x_coordinates)
    np.maximum.at(y_max, entry_indices, y_coordinates)

    labels = object_labels or {}
    annotations: list[InstanceAnnotation] = []
    for index, (color, object_id) in enumerate(entries):
        pixel_count = int(counts[index])
        if pixel_count < min_pixels:
            continue
        annotations.append(
            InstanceAnnotation(
                object_id=object_id,
                label=str(labels.get(object_id, object_id)),
                segmentation_color=color,
                bbox=BoundingBox(
                    x_min=int(x_min[index]),
                    y_min=int(y_min[index]),
                    x_max=int(x_max[index]),
                    y_max=int(y_max[index]),
                ),
                pixel_count=pixel_count,
            )
        )

    return sorted(
        annotations,
        key=lambda annotation: (annotation.object_id, annotation.segmentation_color),
    )


def mapping_from_tdw_segmentation_colors(
    segmentation_colors: Any,
) -> tuple[dict[RGBColor, int], dict[int, str]]:
    """Convert TDW ``SegmentationColors`` output to mappings used above.

    The argument is intentionally duck-typed, so importing this module does
    not import TDW.  A normal ``tdw.output_data.SegmentationColors`` instance
    provides all methods used here.
    """

    color_to_object_id: dict[RGBColor, int] = {}
    object_labels: dict[int, str] = {}
    object_id_to_color: dict[int, RGBColor] = {}

    for index in range(_non_negative_integer(segmentation_colors.get_num(), "get_num()")):
        color = _normalize_color(segmentation_colors.get_object_color(index))
        object_id = _integer(segmentation_colors.get_object_id(index), "object ID")

        previous_id = color_to_object_id.get(color)
        if previous_id is not None and previous_id != object_id:
            raise ValueError(
                f"Segmentation color {color} is assigned to object IDs "
                f"{previous_id} and {object_id}"
            )
        previous_color = object_id_to_color.get(object_id)
        if previous_color is not None and previous_color != color:
            raise ValueError(
                f"Object ID {object_id} is assigned colors {previous_color} and {color}"
            )

        color_to_object_id[color] = object_id
        object_id_to_color[object_id] = color
        object_labels[object_id] = str(segmentation_colors.get_object_name(index))

    return color_to_object_id, object_labels


def draw_instance_annotations(
    image: ImageSource,
    annotations: Sequence[InstanceAnnotation],
    *,
    box_color: Sequence[int] = (255, 128, 0),
    text_color: Sequence[int] = (255, 255, 255),
    line_width: int = 3,
    font_path: str | PathLike[str] | None = None,
    font_size: int = 14,
    show_object_id: bool = False,
) -> Image.Image:
    """Draw boxes and labels on a copy of an RGB image."""

    line_width = _positive_integer(line_width, "line_width")
    font_size = _positive_integer(font_size, "font_size")
    outline = _normalize_color(box_color)
    foreground = _normalize_color(text_color)

    result = Image.fromarray(_load_rgb_array(image).copy())
    draw = ImageDraw.Draw(result)
    font = (
        ImageFont.truetype(str(font_path), size=font_size)
        if font_path is not None
        else ImageFont.load_default()
    )

    image_width, image_height = result.size
    for annotation in annotations:
        box = annotation.bbox
        if box.x_max >= image_width or box.y_max >= image_height:
            raise ValueError(
                f"Annotation for object {annotation.object_id} lies outside the "
                f"{image_width}x{image_height} image"
            )

        draw.rectangle(box.as_xyxy(), outline=outline, width=line_width)
        label = annotation.label
        if show_object_id:
            label = f"{label} [{annotation.object_id}]"

        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        padding = 2
        background_width = text_width + 2 * padding
        background_height = text_height + 2 * padding
        text_x = min(box.x_min, max(0, image_width - background_width))
        text_y = max(0, box.y_min - background_height)
        draw.rectangle(
            (
                text_x,
                text_y,
                min(image_width - 1, text_x + background_width - 1),
                min(image_height - 1, text_y + background_height - 1),
            ),
            fill=outline,
        )
        draw.text(
            (text_x + padding, text_y + padding - text_box[1]),
            label,
            fill=foreground,
            font=font,
        )

    return result


def annotate_image(
    rgb_image: ImageSource,
    id_image: ImageSource,
    color_to_object_id: Mapping[Sequence[int], int],
    object_labels: Mapping[int, str] | None = None,
    *,
    min_pixels: int = 1,
    **draw_options: Any,
) -> tuple[Image.Image, list[InstanceAnnotation]]:
    """Extract annotations and draw them on the corresponding RGB image."""

    rgb_array = _load_rgb_array(rgb_image)
    id_array = _load_rgb_array(id_image)
    if rgb_array.shape[:2] != id_array.shape[:2]:
        raise ValueError(
            "RGB and instance-ID images must have identical dimensions; "
            f"got {rgb_array.shape[1]}x{rgb_array.shape[0]} and "
            f"{id_array.shape[1]}x{id_array.shape[0]}"
        )
    annotations = extract_instance_annotations(
        id_image=id_array,
        color_to_object_id=color_to_object_id,
        object_labels=object_labels,
        min_pixels=min_pixels,
    )
    return (
        draw_instance_annotations(rgb_array, annotations, **draw_options),
        annotations,
    )


def save_annotations_json(
    annotations: Sequence[InstanceAnnotation],
    output_path: str | PathLike[str],
    *,
    image_size: tuple[int, int] | None = None,
) -> Path:
    """Save annotations as UTF-8 JSON and return the output path."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "annotations": [annotation.to_dict() for annotation in annotations],
    }
    if image_size is not None:
        if len(image_size) != 2:
            raise ValueError("image_size must be a (width, height) pair")
        width = _positive_integer(image_size[0], "image width")
        height = _positive_integer(image_size[1], "image height")
        payload["image_size"] = {"width": width, "height": height}

    with output.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return output


def _load_rgb_array(image: ImageSource) -> np.ndarray:
    if isinstance(image, (str, PathLike)):
        with Image.open(image) as opened_image:
            return np.asarray(opened_image.convert("RGB"), dtype=np.uint8)
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGB"), dtype=np.uint8)

    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] not in (3, 4):
        raise ValueError("Image arrays must have shape (height, width, 3) or (..., 4)")
    if array.shape[0] == 0 or array.shape[1] == 0:
        raise ValueError("Images must have positive width and height")
    if not np.issubdtype(array.dtype, np.integer) or np.issubdtype(array.dtype, np.bool_):
        raise TypeError("Image arrays must contain integer RGB values")
    if np.any(array < 0) or np.any(array > 255):
        raise ValueError("Image RGB values must be between 0 and 255")
    return np.asarray(array[..., :3], dtype=np.uint8)


def _normalize_color_mapping(
    color_to_object_id: Mapping[Sequence[int], int],
) -> list[tuple[RGBColor, int]]:
    entries: list[tuple[RGBColor, int]] = []
    normalized_colors: dict[RGBColor, int] = {}
    object_id_to_color: dict[int, RGBColor] = {}

    for raw_color, raw_object_id in color_to_object_id.items():
        color = _normalize_color(raw_color)
        object_id = _integer(raw_object_id, "object ID")
        previous_id = normalized_colors.get(color)
        if previous_id is not None and previous_id != object_id:
            raise ValueError(
                f"Segmentation color {color} maps to both {previous_id} and {object_id}"
            )
        previous_color = object_id_to_color.get(object_id)
        if previous_color is not None and previous_color != color:
            raise ValueError(
                f"Object ID {object_id} maps from both {previous_color} and {color}"
            )
        if previous_id is None:
            entries.append((color, object_id))
        normalized_colors[color] = object_id
        object_id_to_color[object_id] = color
    return entries


def _normalize_color(color: Sequence[int]) -> RGBColor:
    if isinstance(color, (str, bytes)):
        raise TypeError("RGB colors must be three-component integer sequences")
    try:
        components = tuple(color)
    except TypeError as exc:
        raise TypeError("RGB colors must be three-component integer sequences") from exc
    if len(components) != 3:
        raise ValueError(f"Expected an RGB triplet, got {components!r}")
    normalized = tuple(_integer(component, "RGB component") for component in components)
    if any(component < 0 or component > 255 for component in normalized):
        raise ValueError(f"RGB components must be between 0 and 255, got {normalized}")
    return normalized  # type: ignore[return-value]


def _pack_color(color: RGBColor) -> int:
    return (color[0] << 16) | (color[1] << 8) | color[2]


def _integer(value: Any, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be an integer") from exc


def _non_negative_integer(value: Any, name: str) -> int:
    result = _integer(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _positive_integer(value: Any, name: str) -> int:
    result = _integer(value, name)
    if result < 1:
        raise ValueError(f"{name} must be positive")
    return result
