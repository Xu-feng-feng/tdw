"""Launch a configurable floorplan with first-person controls."""

from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys

from tdw_custom_house.cli import create_parser, options_from_args
from tdw_custom_house.config import FurnitureConfigError, load_furniture_config
from tdw_custom_house.runtime import RuntimeDependencyError, run_interactive


def main() -> int:
    parser = create_parser("Explore and capture a configurable TDW floorplan.", interactive=True)
    args = parser.parse_args()
    try:
        furniture = load_furniture_config(args.furniture_config)
        if args.validate_config:
            enabled = sum(item.enabled for item in furniture)
            print(f"Valid furniture config: {len(furniture)} entries ({enabled} enabled)")
            return 0
        options = options_from_args(args, interactive=True)
        run_interactive(options, furniture)  # type: ignore[arg-type]
        return 0
    except (FurnitureConfigError, RuntimeDependencyError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
