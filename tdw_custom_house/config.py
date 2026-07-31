"""Validated JSON configuration for custom TDW furniture."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class FurnitureConfigError(ValueError):
    """Raised when a furniture configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Vector3:
    x: float
    y: float
    z: float

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True, slots=True)
class PhysicsConfig:
    """Physics values accepted by ``Controller.get_add_physics_object``."""

    use_default_values: bool = True
    mass: float = 1.0
    dynamic_friction: float = 0.3
    static_friction: float = 0.3
    bounciness: float = 0.0
    kinematic: bool = False
    gravity: bool = True
    scale_mass: bool = True


@dataclass(frozen=True, slots=True)
class FurnitureItem:
    name: str
    model_name: str
    position: Vector3
    rotation: Vector3 = Vector3(0.0, 0.0, 0.0)
    scale: Vector3 | None = None
    library: str = "models_core.json"
    enabled: bool = True
    physics: PhysicsConfig = PhysicsConfig()


_ITEM_KEYS = {
    "name",
    "model_name",
    "position",
    "rotation",
    "scale",
    "library",
    "enabled",
    "physics",
}
_PHYSICS_KEYS = {
    "use_default_values",
    "mass",
    "dynamic_friction",
    "static_friction",
    "bounciness",
    "kinematic",
    "gravity",
    "scale_mass",
}
_CUSTOM_PHYSICS_KEYS = {
    "mass",
    "dynamic_friction",
    "static_friction",
    "bounciness",
}


def load_furniture_config(path: str | Path) -> list[FurnitureItem]:
    """Load and validate a furniture JSON list."""

    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError as exc:
        raise FurnitureConfigError(f"Furniture config does not exist: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise FurnitureConfigError(
            f"Invalid JSON in {config_path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise FurnitureConfigError(f"Unable to read furniture config {config_path}: {exc}") from exc

    if not isinstance(payload, list):
        raise FurnitureConfigError("Furniture config root must be a JSON array")

    items = [_parse_item(value, index) for index, value in enumerate(payload)]
    seen_names: set[str] = set()
    for item in items:
        if item.name in seen_names:
            raise FurnitureConfigError(f"Duplicate furniture name: {item.name!r}")
        seen_names.add(item.name)
    return items


def build_furniture_commands(
    controller: Any,
    furniture: Sequence[FurnitureItem],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Create TDW commands and return the configured-name to object-ID map.

    ``controller`` is duck-typed deliberately so this function can be tested
    without launching the TDW Unity build.
    """

    commands: list[dict[str, Any]] = []
    object_ids: dict[str, int] = {}
    for item in furniture:
        if not item.enabled:
            continue
        object_id = int(controller.get_unique_id())
        physics = item.physics
        try:
            item_commands = controller.get_add_physics_object(
                model_name=item.model_name,
                object_id=object_id,
                position=item.position.as_dict(),
                rotation=item.rotation.as_dict(),
                library=item.library,
                scale_factor=item.scale.as_dict() if item.scale is not None else None,
                kinematic=physics.kinematic,
                gravity=physics.gravity,
                default_physics_values=physics.use_default_values,
                mass=physics.mass,
                dynamic_friction=physics.dynamic_friction,
                static_friction=physics.static_friction,
                bounciness=physics.bounciness,
                scale_mass=physics.scale_mass,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to create furniture {item.name!r} from model {item.model_name!r}: {exc}"
            ) from exc
        if not isinstance(item_commands, list):
            raise TypeError("Controller.get_add_physics_object() must return a list of commands")
        commands.extend(item_commands)
        object_ids[item.name] = object_id
    return commands, object_ids


def _parse_item(value: Any, index: int) -> FurnitureItem:
    location = f"furniture[{index}]"
    item = _mapping(value, location)
    _reject_unknown_keys(item, _ITEM_KEYS, location)

    name = _non_empty_string(item.get("name"), f"{location}.name")
    model_name = _non_empty_string(item.get("model_name"), f"{location}.model_name")
    position = _vector3(item.get("position"), f"{location}.position")
    rotation = _vector3(
        item.get("rotation", {"x": 0, "y": 0, "z": 0}),
        f"{location}.rotation",
    )
    scale_value = item.get("scale")
    scale = None if scale_value is None else _scale(scale_value, f"{location}.scale")
    library = _non_empty_string(item.get("library", "models_core.json"), f"{location}.library")
    enabled = _boolean(item.get("enabled", True), f"{location}.enabled")
    physics = _physics(item.get("physics", {}), f"{location}.physics")
    return FurnitureItem(
        name=name,
        model_name=model_name,
        position=position,
        rotation=rotation,
        scale=scale,
        library=library,
        enabled=enabled,
        physics=physics,
    )


def _physics(value: Any, location: str) -> PhysicsConfig:
    physics = _mapping(value, location)
    _reject_unknown_keys(physics, _PHYSICS_KEYS, location)
    has_custom_values = bool(_CUSTOM_PHYSICS_KEYS.intersection(physics))
    use_defaults = _boolean(
        physics.get("use_default_values", not has_custom_values),
        f"{location}.use_default_values",
    )
    if use_defaults and has_custom_values:
        custom = ", ".join(sorted(_CUSTOM_PHYSICS_KEYS.intersection(physics)))
        raise FurnitureConfigError(
            f"{location} sets use_default_values=true, so custom values would be ignored: {custom}"
        )

    mass = _number(physics.get("mass", 1), f"{location}.mass", minimum=0, exclusive=True)
    dynamic_friction = _number(
        physics.get("dynamic_friction", 0.3),
        f"{location}.dynamic_friction",
        minimum=0,
    )
    static_friction = _number(
        physics.get("static_friction", 0.3),
        f"{location}.static_friction",
        minimum=0,
    )
    bounciness = _number(
        physics.get("bounciness", 0),
        f"{location}.bounciness",
        minimum=0,
        maximum=1,
    )
    return PhysicsConfig(
        use_default_values=use_defaults,
        mass=mass,
        dynamic_friction=dynamic_friction,
        static_friction=static_friction,
        bounciness=bounciness,
        kinematic=_boolean(physics.get("kinematic", False), f"{location}.kinematic"),
        gravity=_boolean(physics.get("gravity", True), f"{location}.gravity"),
        scale_mass=_boolean(physics.get("scale_mass", True), f"{location}.scale_mass"),
    )


def _vector3(value: Any, location: str) -> Vector3:
    mapping = _mapping(value, location)
    _reject_unknown_keys(mapping, {"x", "y", "z"}, location)
    missing = {"x", "y", "z"}.difference(mapping)
    if missing:
        raise FurnitureConfigError(f"{location} is missing: {', '.join(sorted(missing))}")
    return Vector3(
        x=_number(mapping["x"], f"{location}.x"),
        y=_number(mapping["y"], f"{location}.y"),
        z=_number(mapping["z"], f"{location}.z"),
    )


def _scale(value: Any, location: str) -> Vector3:
    if isinstance(value, Mapping):
        result = _vector3(value, location)
    else:
        scalar = _number(value, location, minimum=0, exclusive=True)
        result = Vector3(scalar, scalar, scalar)
    if min(result.x, result.y, result.z) <= 0:
        raise FurnitureConfigError(f"{location} components must be greater than zero")
    return result


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise FurnitureConfigError(f"{location} must be a JSON object")
    return value


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise FurnitureConfigError(f"{location} has unknown fields: {', '.join(sorted(unknown))}")


def _non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FurnitureConfigError(f"{location} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise FurnitureConfigError(f"{location} must be true or false")
    return value


def _number(
    value: Any,
    location: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FurnitureConfigError(f"{location} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise FurnitureConfigError(f"{location} must be finite")
    if minimum is not None and (result <= minimum if exclusive else result < minimum):
        comparison = "greater than" if exclusive else "at least"
        raise FurnitureConfigError(f"{location} must be {comparison} {minimum}")
    if maximum is not None and result > maximum:
        raise FurnitureConfigError(f"{location} must be at most {maximum}")
    return result
