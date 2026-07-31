import unittest
from pathlib import Path

from tdw_custom_house.cli import create_parser, options_from_args
from tdw_custom_house.runtime import InteractiveOptions, RuntimeOptions


class CliTests(unittest.TestCase):
    def test_empty_layout_maps_to_none(self) -> None:
        parser = create_parser("test", interactive=False)
        args = parser.parse_args(["--layout", "empty", "--top-position", "1", "30", "2"])
        options = options_from_args(args, interactive=False)

        self.assertIsInstance(options, RuntimeOptions)
        self.assertIsNone(options.layout)
        self.assertEqual(options.top_position.as_dict(), {"x": 1.0, "y": 30.0, "z": 2.0})
        self.assertEqual(options.asset_mode, "cache")

    def test_interactive_options(self) -> None:
        parser = create_parser("test", interactive=True)
        args = parser.parse_args(
            [
                "--layout",
                "2",
                "--ego-position",
                "3",
                "0",
                "-4",
                "--ego-height",
                "2.1",
                "--ego-camera-height",
                "2.0",
                "--top-view-width",
                "320",
                "--no-ego-capture",
                "--no-top-view",
            ]
        )
        options = options_from_args(args, interactive=True)

        self.assertIsInstance(options, InteractiveOptions)
        self.assertEqual(options.layout, 2)
        self.assertEqual(options.ego_position.z, -4)
        self.assertEqual(options.ego_height, 2.1)
        self.assertEqual(options.ego_camera_height, 2.0)
        self.assertFalse(options.capture_ego)
        self.assertFalse(options.show_top_view)
        self.assertEqual(options.top_view_width, 320)

    def test_interactive_camera_defaults_are_raised_and_clear_of_the_wall(self) -> None:
        parser = create_parser("test", interactive=True)
        options = options_from_args(parser.parse_args([]), interactive=True)

        self.assertEqual(options.ego_position.as_dict(), {"x": -3.6, "y": 0.0, "z": 1.8})
        self.assertEqual(options.ego_rotation, 270.0)
        self.assertEqual(options.ego_height, 1.9)
        self.assertEqual(options.ego_camera_height, 1.8)
        self.assertTrue(options.show_top_view)
        self.assertEqual(options.top_view_width, 384)

    def test_asset_transport_options(self) -> None:
        parser = create_parser("test", interactive=False)
        args = parser.parse_args(
            ["--asset-mode", "http", "--asset-cache-dir", "/tmp/tdw-test-cache"]
        )
        options = options_from_args(args, interactive=False)

        self.assertEqual(options.asset_mode, "http")
        self.assertEqual(options.asset_cache_dir, Path("/tmp/tdw-test-cache"))


if __name__ == "__main__":
    unittest.main()
