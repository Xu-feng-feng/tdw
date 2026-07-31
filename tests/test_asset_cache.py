import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import requests

from tdw_custom_house.asset_cache import (
    AssetCacheError,
    cache_tdw_asset_urls,
    default_asset_cache_dir,
    rewrite_tdw_asset_urls_to_http,
)


ASSET_HTTPS = "https://tdw-public.s3.amazonaws.com/models/linux/2020.3/chair"
ASSET_HTTP = "http://tdw-public.s3.amazonaws.com/models/linux/2020.3/chair"


class _Response:
    def __init__(self, chunks, *, content_length=None, error=None, status_code=200):
        self._chunks = chunks
        self._error = error
        self.status_code = status_code
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.closed = False
        self.chunk_size = None

    def raise_for_status(self):
        return None

    def iter_content(self, *, chunk_size):
        self.chunk_size = chunk_size
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, stream, timeout, allow_redirects, headers):
        self.calls.append(
            {
                "url": url,
                "stream": stream,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
                "headers": headers,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected download")
        return self.responses.pop(0)


def _uri_path(uri: str) -> Path:
    parsed = urlsplit(uri)
    assert parsed.scheme == "file"
    return Path(unquote(parsed.path))


class AssetCacheTests(unittest.TestCase):
    def test_rewrites_only_official_urls_and_deduplicates_http_and_https(self) -> None:
        external = "https://example.com/object.bundle"
        lookalike = "https://tdw-public.s3.amazonaws.com.evil.example/object"
        commands = [
            {
                "$type": "add_object",
                "url": ASSET_HTTPS,
                "nested": [ASSET_HTTP, external, lookalike],
            },
            {"$type": "custom", "url": external},
        ]
        response = _Response([b"abc", b"", b"def"], content_length=6)
        session = _Session(response)

        with tempfile.TemporaryDirectory() as cache_dir:
            rewritten = cache_tdw_asset_urls(
                commands,
                cache_dir,
                timeout=(2.0, 9.0),
                chunk_size=3,
                session=session,  # type: ignore[arg-type]
            )

            local_url = rewritten[0]["url"]
            self.assertTrue(local_url.startswith("file:///"))
            self.assertEqual(rewritten[0]["nested"][0], local_url)
            self.assertEqual(rewritten[0]["nested"][1:], [external, lookalike])
            self.assertEqual(rewritten[1]["url"], external)
            self.assertEqual(_uri_path(local_url).read_bytes(), b"abcdef")

        self.assertEqual(commands[0]["url"], ASSET_HTTPS)
        self.assertEqual(commands[0]["nested"][0], ASSET_HTTP)
        self.assertEqual(
            session.calls,
            [
                {
                    "url": ASSET_HTTPS,
                    "stream": True,
                    "timeout": (2.0, 9.0),
                    "allow_redirects": False,
                    "headers": {"Accept-Encoding": "identity"},
                }
            ],
        )
        self.assertEqual(response.chunk_size, 3)
        self.assertTrue(response.closed)

    def test_reuses_existing_cache_entry_without_a_request(self) -> None:
        first_session = _Session(_Response([b"asset data"], content_length=10))
        with tempfile.TemporaryDirectory() as cache_dir:
            first = cache_tdw_asset_urls(
                {"url": ASSET_HTTPS},
                cache_dir,
                session=first_session,  # type: ignore[arg-type]
            )
            cached_path = _uri_path(first["url"])
            second_session = _Session()
            second = cache_tdw_asset_urls(
                {"url": ASSET_HTTPS},
                cache_dir,
                session=second_session,  # type: ignore[arg-type]
            )

            self.assertEqual(second, first)
            self.assertEqual(cached_path.read_bytes(), b"asset data")
            self.assertEqual(second_session.calls, [])

    def test_refreshes_a_cache_entry_whose_content_hash_changed(self) -> None:
        first_session = _Session(_Response([b"asset data"], content_length=10))
        second_session = _Session(_Response([b"fresh data"], content_length=10))
        with tempfile.TemporaryDirectory() as cache_dir:
            first = cache_tdw_asset_urls(
                {"url": ASSET_HTTPS},
                cache_dir,
                session=first_session,  # type: ignore[arg-type]
            )
            cached_path = _uri_path(first["url"])
            cached_path.write_bytes(b"asset DATA")

            second = cache_tdw_asset_urls(
                {"url": ASSET_HTTPS},
                cache_dir,
                session=second_session,  # type: ignore[arg-type]
            )

            self.assertEqual(second, first)
            self.assertEqual(cached_path.read_bytes(), b"fresh data")
            self.assertEqual(len(second_session.calls), 1)

    def test_failed_stream_removes_part_file_and_reports_next_steps(self) -> None:
        failure = requests.ConnectionError("connection dropped")
        session = _Session(_Response([b"partial"], error=failure))

        with tempfile.TemporaryDirectory() as cache_dir:
            with self.assertRaises(AssetCacheError) as raised:
                cache_tdw_asset_urls(
                    {"url": ASSET_HTTPS},
                    cache_dir,
                    session=session,  # type: ignore[arg-type]
                )

            message = str(raised.exception)
            self.assertIn(ASSET_HTTPS, message)
            self.assertIn("network/proxy/CA", message)
            self.assertIn("retry", message.lower())
            self.assertEqual(list(Path(cache_dir).iterdir()), [])

    def test_rejects_incomplete_response_without_replacing_cache(self) -> None:
        session = _Session(_Response([b"short"], content_length=20))
        with tempfile.TemporaryDirectory() as cache_dir:
            with self.assertRaisesRegex(AssetCacheError, "expected 20 bytes"):
                cache_tdw_asset_urls(
                    {"url": ASSET_HTTPS},
                    cache_dir,
                    session=session,  # type: ignore[arg-type]
                )
            self.assertEqual(list(Path(cache_dir).iterdir()), [])

    def test_rejects_redirects_without_following_them(self) -> None:
        response = _Response([], status_code=302)
        session = _Session(response)
        with tempfile.TemporaryDirectory() as cache_dir:
            with self.assertRaisesRegex(AssetCacheError, "Refusing to follow"):
                cache_tdw_asset_urls(
                    {"url": ASSET_HTTPS},
                    cache_dir,
                    session=session,  # type: ignore[arg-type]
                )

            self.assertFalse(session.calls[0]["allow_redirects"])
            self.assertTrue(response.closed)
            self.assertEqual(list(Path(cache_dir).iterdir()), [])

    def test_progress_callback_gets_concise_status(self) -> None:
        messages = []
        session = _Session(_Response([b"abc"], content_length=3))
        with tempfile.TemporaryDirectory() as cache_dir:
            cache_tdw_asset_urls(
                {"url": ASSET_HTTPS},
                cache_dir,
                progress=messages.append,
                session=session,  # type: ignore[arg-type]
            )

        self.assertEqual(len(messages), 2)
        self.assertIn("[1/1] downloading chair", messages[0])
        self.assertIn("[1/1] ready chair (3 B)", messages[1])

    def test_non_official_commands_do_not_touch_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            cache_dir = Path(parent) / "not-created"
            commands = {"url": "https://example.com/object", "values": [1, 2]}
            rewritten = cache_tdw_asset_urls(commands, cache_dir)

            self.assertEqual(rewritten, commands)
            self.assertIsNot(rewritten, commands)
            self.assertFalse(cache_dir.exists())

    def test_explicit_http_fallback_rewrites_only_the_official_host(self) -> None:
        external = "https://example.com/object"
        lookalike = "https://tdw-public.s3.amazonaws.com.evil.example/object"
        commands = {"urls": [ASSET_HTTPS, ASSET_HTTP, external, lookalike]}

        rewritten = rewrite_tdw_asset_urls_to_http(commands)

        self.assertEqual(rewritten["urls"][:2], [ASSET_HTTP, ASSET_HTTP])
        self.assertEqual(rewritten["urls"][2:], [external, lookalike])
        self.assertEqual(commands["urls"][0], ASSET_HTTPS)

    def test_default_cache_dir_honors_absolute_xdg_path(self) -> None:
        with tempfile.TemporaryDirectory() as cache_home:
            with patch.dict("os.environ", {"XDG_CACHE_HOME": cache_home}, clear=False):
                self.assertEqual(
                    default_asset_cache_dir(),
                    Path(cache_home) / "tdw_custom_house" / "assets",
                )

    def test_validates_input_and_chunk_size(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            with self.assertRaisesRegex(TypeError, "command dict"):
                cache_tdw_asset_urls("not a command", cache_dir)  # type: ignore[arg-type]
            with self.assertRaisesRegex(ValueError, "positive integer"):
                cache_tdw_asset_urls({}, cache_dir, chunk_size=0)


if __name__ == "__main__":
    unittest.main()
