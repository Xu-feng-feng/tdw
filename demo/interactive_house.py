"""Backward-compatible launcher for the complete custom-house project."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tdw_custom_house.interactive_house import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
