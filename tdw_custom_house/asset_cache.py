"""Download TDW's public AssetBundles with Python and use local file URLs.

Unity 2020's TLS stack can fail to validate the certificate served by TDW's
public S3 bucket even when the host's Python installation can access it.  This
module keeps that workaround deliberately narrow: only URLs whose scheme is
HTTP(S) and whose host is exactly ``tdw-public.s3.amazonaws.com`` are cached.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import unquote, urlsplit, urlunsplit

import requests


TDW_PUBLIC_ASSET_HOST = "tdw-public.s3.amazonaws.com"
DEFAULT_TIMEOUT = (10.0, 120.0)
DEFAULT_CHUNK_SIZE = 1024 * 1024

_Commands = TypeVar("_Commands", dict[Any, Any], list[Any])
_Progress = Callable[[str], None]


class AssetCacheError(RuntimeError):
    """Raised when an official TDW asset can't be safely cached."""


def default_asset_cache_dir() -> Path:
    """Return the per-user persistent cache directory for TDW assets."""

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        candidate = Path(xdg_cache).expanduser()
        if candidate.is_absolute():
            return candidate / "tdw_custom_house" / "assets"
    return Path.home() / ".cache" / "tdw_custom_house" / "assets"


def cache_tdw_asset_urls(
    commands: _Commands,
    cache_dir: str | Path,
    *,
    timeout: float | tuple[float, float] = DEFAULT_TIMEOUT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    show_progress: bool = False,
    progress: _Progress | None = None,
    session: requests.Session | None = None,
) -> _Commands:
    """Cache official TDW URLs in *commands* and return a rewritten copy.

    The input can be one TDW command dictionary or a list of commands. Nested
    dictionaries and lists are traversed too. The original object isn't
    modified, non-TDW URLs are left byte-for-byte unchanged, and repeated HTTP
    and HTTPS forms of the same TDW URL are downloaded only once.

    Downloads always use HTTPS, stream to a temporary ``.part`` file in
    *cache_dir*, and become visible through an atomic replace only after the
    response has completed. A SHA-256/size sidecar validates cache hits so a
    truncated or manually modified bundle is never silently reused.

    Set *show_progress* to print short status lines, or pass a *progress*
    callback to route those lines elsewhere. A caller-provided requests
    *session* remains owned by the caller.
    """

    if not isinstance(commands, (dict, list)):
        raise TypeError("commands must be a TDW command dict or a list of commands")
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    urls: dict[str, None] = {}
    for value in _walk_values(commands):
        if isinstance(value, str):
            canonical = _canonical_tdw_url(value)
            if canonical is not None:
                urls.setdefault(canonical, None)
    if not urls:
        return cast(_Commands, _replace_urls(commands, {}))

    root = _prepare_cache_dir(cache_dir)
    emit = progress if progress is not None else (print if show_progress else None)

    owns_session = session is None
    client = requests.Session() if session is None else session
    local_urls: dict[str, str] = {}
    total = len(urls)
    try:
        for index, url in enumerate(urls, start=1):
            destination = _cache_path(root, url)
            metadata_path = _cache_metadata_path(destination)
            label = destination.name.split("-", 1)[-1]
            if destination.is_symlink():
                raise AssetCacheError(
                    f"Refusing to reuse symlinked TDW cache entry: {destination}. "
                    "Remove that symlink or choose a different cache directory."
                )
            if metadata_path.is_symlink():
                raise AssetCacheError(
                    f"Refusing to reuse symlinked TDW cache metadata: {metadata_path}. "
                    "Remove that symlink or choose a different cache directory."
                )
            if destination.exists() and not destination.is_file():
                raise AssetCacheError(
                    f"TDW cache target isn't a regular file: {destination}. "
                    "Remove it or choose a different cache directory."
                )
            if metadata_path.exists() and not metadata_path.is_file():
                raise AssetCacheError(
                    f"TDW cache metadata target isn't a regular file: {metadata_path}. "
                    "Remove it or choose a different cache directory."
                )
            if _cache_entry_is_valid(destination, metadata_path, url, chunk_size):
                _emit(emit, f"TDW assets [{index}/{total}] reusing {label}")
            else:
                verb = "refreshing" if destination.exists() else "downloading"
                _emit(emit, f"TDW assets [{index}/{total}] {verb} {label}")
                size, sha256, etag = _download(
                    client, url, destination, timeout, chunk_size
                )
                _write_cache_metadata(
                    metadata_path,
                    url=url,
                    size=size,
                    sha256=sha256,
                    etag=etag,
                )
                _emit(
                    emit,
                    f"TDW assets [{index}/{total}] ready {label} ({_format_bytes(size)})",
                )
            local_urls[url] = destination.as_uri()
    finally:
        if owns_session:
            client.close()

    return cast(_Commands, _replace_urls(commands, local_urls))


def rewrite_tdw_asset_urls_to_http(commands: _Commands) -> _Commands:
    """Return a copy whose official TDW URLs use insecure plain HTTP.

    This is an explicit compatibility fallback for old Unity TLS stacks. It
    must never affect third-party URLs, lookalike hosts, or the input object.
    Prefer :func:`cache_tdw_asset_urls`, which retains HTTPS verification in
    Python and gives Unity local ``file:///`` URLs.
    """

    if not isinstance(commands, (dict, list)):
        raise TypeError("commands must be a TDW command dict or a list of commands")
    replacements: dict[str, str] = {}
    for value in _walk_values(commands):
        if not isinstance(value, str):
            continue
        canonical = _canonical_tdw_url(value)
        if canonical is None:
            continue
        parsed = urlsplit(canonical)
        replacements[canonical] = urlunsplit(
            ("http", TDW_PUBLIC_ASSET_HOST, parsed.path, parsed.query, "")
        )
    return cast(_Commands, _replace_urls(commands, replacements))


def _prepare_cache_dir(cache_dir: str | Path) -> Path:
    try:
        root = Path(cache_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    except (OSError, TypeError) as exc:
        raise AssetCacheError(
            f"Unable to create the TDW asset cache at {cache_dir!s}: {exc}. "
            "Choose a writable directory."
        ) from exc
    if not root.is_dir():
        raise AssetCacheError(
            f"TDW asset cache path isn't a directory: {root}. "
            "Choose a writable directory."
        )
    return root


def _canonical_tdw_url(value: str) -> str | None:
    """Return an HTTPS cache key for an official TDW URL, else ``None``."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    # Compare netloc rather than accepting hostname suffixes. This rejects
    # credentials, non-default ports, and lookalike domains as well.
    if parsed.netloc.lower() != TDW_PUBLIC_ASSET_HOST:
        return None
    return urlunsplit(("https", TDW_PUBLIC_ASSET_HOST, parsed.path, parsed.query, ""))


def _walk_values(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _replace_urls(value: Any, local_urls: dict[str, str]) -> Any:
    if isinstance(value, str):
        canonical = _canonical_tdw_url(value)
        return local_urls.get(canonical, value) if canonical is not None else value
    if isinstance(value, dict):
        return {key: _replace_urls(child, local_urls) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_urls(child, local_urls) for child in value]
    return value


def _cache_path(root: Path, url: str) -> Path:
    parsed = urlsplit(url)
    raw_name = unquote(parsed.path.rsplit("/", 1)[-1]) or "asset"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._") or "asset"
    safe_name = safe_name[:100]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return root / f"{digest}-{safe_name}"


def _cache_metadata_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.metadata.json")


def _cache_entry_is_valid(
    destination: Path,
    metadata_path: Path,
    url: str,
    chunk_size: int,
) -> bool:
    if not destination.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_size = metadata["size"]
        expected_sha256 = metadata["sha256"]
        if (
            metadata.get("url") != url
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size <= 0
            or not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or destination.stat().st_size != expected_size
        ):
            return False
        digest = hashlib.sha256()
        with destination.open("rb") as cached:
            for chunk in iter(lambda: cached.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        return False


def _write_cache_metadata(
    metadata_path: Path,
    *,
    url: str,
    size: int,
    sha256: str,
    etag: str | None,
) -> None:
    part_path: Path | None = None
    payload: dict[str, Any] = {"url": url, "size": size, "sha256": sha256}
    if etag:
        payload["etag"] = etag
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=metadata_path.parent,
            prefix=f".{metadata_path.name}.",
            suffix=".part",
            delete=False,
        ) as part:
            part_path = Path(part.name)
            json.dump(payload, part, ensure_ascii=True, sort_keys=True)
            part.write("\n")
            part.flush()
            os.fsync(part.fileno())
        os.replace(part_path, metadata_path)
        part_path = None
    except Exception as exc:
        raise AssetCacheError(
            f"Unable to write TDW cache metadata in {metadata_path.parent}: "
            f"{type(exc).__name__}: {exc}. Check that the cache directory is writable."
        ) from exc
    finally:
        if part_path is not None:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass


def _download(
    client: Any,
    url: str,
    destination: Path,
    timeout: float | tuple[float, float],
    chunk_size: int,
) -> tuple[int, str, str | None]:
    response: Any | None = None
    part_path: Path | None = None
    try:
        response = client.get(
            url,
            stream=True,
            timeout=timeout,
            allow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )
        status_code = int(getattr(response, "status_code", 200))
        if 300 <= status_code < 400:
            raise AssetCacheError(
                f"TDW asset download unexpectedly redirected (HTTP {status_code}) "
                f"for {url}. Refusing to follow a redirect outside the official host."
            )
        response.raise_for_status()
        if status_code != 200:
            raise AssetCacheError(
                f"TDW asset download returned unexpected HTTP {status_code} for {url}."
            )
        expected_size = _content_length(response)
        written = 0
        digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
            delete=False,
        ) as part:
            part_path = Path(part.name)
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                part.write(chunk)
                written += len(chunk)
                digest.update(chunk)
            part.flush()
            os.fsync(part.fileno())

        if expected_size is not None and written != expected_size:
            raise AssetCacheError(
                f"Incomplete TDW asset download for {url}: expected "
                f"{expected_size} bytes but received {written}. Retry the command; "
                "a partial cache file was not retained."
            )
        if written == 0:
            raise AssetCacheError(
                f"TDW returned an empty asset for {url}. Retry later or verify that "
                "the asset URL still exists."
            )

        os.replace(part_path, destination)
        part_path = None
        raw_etag = response.headers.get("ETag")
        etag = raw_etag if isinstance(raw_etag, str) and raw_etag else None
        return written, digest.hexdigest(), etag
    except AssetCacheError:
        raise
    except Exception as exc:
        raise AssetCacheError(
            f"Unable to cache TDW asset {url} in {destination.parent}: "
            f"{type(exc).__name__}: {exc}. Check Python's network/proxy/CA "
            "settings and confirm the cache directory is writable, then retry."
        ) from exc
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if part_path is not None:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass


def _content_length(response: Any) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise AssetCacheError(
            f"TDW returned an invalid Content-Length header: {value!r}. "
            "Retry later or check whether a proxy is altering the response."
        ) from exc
    if length < 0:
        raise AssetCacheError(
            f"TDW returned an invalid negative Content-Length header: {value!r}. "
            "Retry later or check whether a proxy is altering the response."
        )
    return length


def _emit(progress: _Progress | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"
