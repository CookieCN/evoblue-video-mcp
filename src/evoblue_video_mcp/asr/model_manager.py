"""Model Manager download and install primitives (ASR-2 step 2).

``download_resumable`` streams with HTTP Range resume and verifies size + SHA-256
without ever buffering the whole artifact in memory. ``install_archive`` extracts
into a bounded staging directory, enforces the manifest file allowlist (name, size,
digest), rejects path traversal / links / devices / duplicates, and atomically
promotes the result without deleting an existing version directory. Both are
side-effect-free apart from the destination path and are unit-testable without a
database.
"""

import hashlib
import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from evoblue_video_mcp.asr.manifest import ModelFile, ModelManifest

# Stream chunk size for hashing and bounded copies (1 MiB).
_CHUNK = 1024 * 1024

# Upper bound on archive members; real model archives carry a handful of files,
# so a pathological archive with far more is rejected before it can amplify.
_MAX_ARCHIVE_MEMBERS = 1000


class _Readable(Protocol):
    def read(self, size: int = -1) -> bytes: ...

_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class DownloadError(ValueError):
    """Base for download failures; ``code`` is a stable, desensitized error token."""

    code = "DOWNLOAD_FAILED"


class ContentRangeError(DownloadError):
    code = "INVALID_CONTENT_RANGE"


class ResponseTooLargeError(DownloadError):
    code = "RESPONSE_TOO_LARGE"


class SizeMismatchError(DownloadError):
    code = "SIZE_MISMATCH"


class DigestMismatchError(DownloadError):
    code = "SHA256_MISMATCH"


class ArchiveError(ValueError):
    """Base for archive extraction/verification failures."""

    code = "ARCHIVE_INVALID"


@dataclass(frozen=True)
class DownloadOutcome:
    """Result of ``download_resumable``; carries resume validators for the caller."""

    downloaded_bytes: int
    sha256: str
    restarted: bool
    etag: str | None
    last_modified: str | None


def _file_sha256(path: Path) -> str:
    """Hash a file incrementally in bounded chunks (never reads it all at once)."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _validate_content_range(value: str | None, start: int, total: int) -> None:
    """Require a 206 to resume exactly from ``start`` for a resource of ``total`` bytes."""
    if not value:
        raise ContentRangeError("206 response missing Content-Range header")
    match = _CONTENT_RANGE_RE.match(value.strip())
    if match is None:
        raise ContentRangeError(f"malformed Content-Range: {value!r}")
    range_start, _range_end, range_total = match.group(1), match.group(2), match.group(3)
    if range_total == "*":
        raise ContentRangeError("Content-Range total is unknown; partial is not resumable")
    if int(range_start) != start:
        raise ContentRangeError(
            f"Content-Range start {range_start} != requested {start}; partial is stale"
        )
    if int(range_total) != total:
        raise ContentRangeError(
            f"Content-Range total {range_total} != expected {total}; resource changed"
        )


async def download_resumable(
    url: str,
    dest_path: str | Path,
    expected_sha256: str,
    expected_size_bytes: int,
    http_client: httpx.AsyncClient,
    *,
    progress: Callable[[int], Awaitable[None]] | None = None,
    on_restart: Callable[[], Awaitable[None]] | None = None,
    on_headers: Callable[[str | None, str | None], Awaitable[None]] | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> DownloadOutcome:
    """Download ``url`` to ``dest_path`` with Range resume, verifying size + SHA-256.

    Streams the body and hashes incrementally, aborting as soon as the accumulated
    byte count would exceed ``expected_size_bytes``, so a malicious or mis-sized
    response cannot exhaust memory or disk. A 206 must resume exactly from the local
    partial length; a 200 signals the server ignored Range and triggers ``on_restart``
    before the file is rewritten from zero. When the local file is already complete,
    no request is issued and the digest is verified in place.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.exists() else 0

    if existing > expected_size_bytes:
        raise ContentRangeError(
            f"existing partial {existing} exceeds expected size {expected_size_bytes}"
        )

    if existing == expected_size_bytes:
        sha = _file_sha256(dest)
        if sha != expected_sha256:
            raise DigestMismatchError("existing file digest does not match expected sha256")
        return DownloadOutcome(
            downloaded_bytes=existing,
            sha256=sha,
            restarted=False,
            etag=None,
            last_modified=None,
        )

    headers: dict[str, str] = {}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        if etag:
            headers["If-Range"] = etag
        elif last_modified:
            headers["If-Range"] = last_modified

    restarted = False
    response_etag: str | None = None
    response_last_modified: str | None = None
    async with http_client.stream("GET", url, headers=headers, follow_redirects=True) as resp:
        if resp.status_code == 206:
            _validate_content_range(
                resp.headers.get("content-range"), existing, expected_size_bytes
            )
            mode = "ab"
        elif resp.status_code == 200:
            if existing > 0:
                restarted = True
                if on_restart is not None:
                    await on_restart()
            mode = "wb"
            existing = 0
        else:
            raise DownloadError(f"unexpected download status {resp.status_code}")

        response_etag = resp.headers.get("etag")
        response_last_modified = resp.headers.get("last-modified")
        if on_headers is not None:
            await on_headers(response_etag, response_last_modified)

        hasher = hashlib.sha256()
        if mode == "ab":
            with open(dest, "rb") as prefix:
                for chunk in iter(lambda: prefix.read(_CHUNK), b""):
                    hasher.update(chunk)
        size = existing

        with open(dest, mode) as out:
            async for chunk in resp.aiter_bytes():
                if size + len(chunk) > expected_size_bytes:
                    raise ResponseTooLargeError(
                        f"response exceeds expected size {expected_size_bytes}"
                    )
                out.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
                if progress is not None:
                    await progress(size)

    if size != expected_size_bytes:
        raise SizeMismatchError(f"downloaded size {size} != expected {expected_size_bytes}")
    sha = hasher.hexdigest()
    if sha != expected_sha256:
        raise DigestMismatchError("downloaded sha256 does not match expected digest")

    return DownloadOutcome(
        downloaded_bytes=size,
        sha256=sha,
        restarted=restarted,
        etag=response_etag,
        last_modified=response_last_modified,
    )


class _Budget:
    """Tracks total extracted bytes against the declared installed size."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.spent = 0

    def add(self, n: int) -> None:
        self.spent += n
        if self.spent > self.limit:
            raise ArchiveError("archive expands beyond the declared installed size")


def _reject_unsafe(raw: str) -> None:
    """Reject absolute, drive-letter, and ``..`` paths (security check for directories)."""
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise ArchiveError(f"absolute path rejected: {raw!r}")
    if any(part == ".." for part in normalized.split("/")):
        raise ArchiveError(f"parent traversal rejected: {raw!r}")


def _member_target(raw: str) -> tuple[str, str | None]:
    """Return ``(declared_name, root)`` for a file member, or reject it.

    Permits ``declared-file`` (root ``None``) and ``single-root/declared-file``
    only. Absolute paths, drive letters, ``..``, empty segments (``a//b``) and
    ``.`` segments (``a/./b``) are rejected, as is any nesting deeper than one
    root directory.
    """
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise ArchiveError(f"absolute path rejected: {raw!r}")
    parts = normalized.split("/")
    for part in parts:
        if part == "..":
            raise ArchiveError(f"parent traversal rejected: {raw!r}")
        if part in ("", "."):
            raise ArchiveError(f"empty or '.' path segment rejected: {raw!r}")
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 2:
        return parts[1], parts[0]
    raise ArchiveError(f"nested path rejected: {raw!r}")


def _stream_copy(
    src: _Readable,
    name: str,
    staging: Path,
    expected: dict[str, ModelFile],
    budget: _Budget,
) -> None:
    """Copy ``src`` into ``staging/name`` with streaming size + digest + budget checks."""
    declared = expected[name]
    hasher = hashlib.sha256()
    size = 0
    out_path = staging / name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as out:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > declared.size_bytes:
                raise ArchiveError(f"size exceeds declared for {name!r}")
            budget.add(len(chunk))
            hasher.update(chunk)
            out.write(chunk)
    if size != declared.size_bytes:
        raise ArchiveError(f"size mismatch for {name!r}")
    if hasher.hexdigest() != declared.sha256:
        raise ArchiveError(f"sha256 mismatch for {name!r}")


def install_archive(
    archive_path: str | Path, manifest: ModelManifest, dest_dir: str | Path
) -> str:
    """Extract and verify the archive, atomically promoting it into ``dest_dir``.

    The version directory is treated as immutable: an existing destination is
    reused when it already matches, and rejected when it differs, so a crash
    never leaves the previous installation missing.
    """
    archive = Path(archive_path)
    dest = Path(dest_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.staging-", dir=dest.parent))
    try:
        _extract_and_verify(archive, staging, manifest)
        _promote(staging, dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return str(dest)


def install_file_set(
    download_dir: str | Path, manifest: ModelManifest, dest_dir: str | Path
) -> str:
    """Verify and atomically promote a pinned set of individually downloaded files."""
    if manifest.archive_format != "file-set":
        raise ArchiveError("install_file_set requires a file-set manifest")
    source = Path(download_dir)
    dest = Path(dest_dir)
    expected = {file.name: file for file in manifest.files}
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    if actual != set(expected):
        raise ArchiveError("downloaded file set does not match the manifest")

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.staging-", dir=dest.parent))
    budget = _Budget(manifest.installed_size_bytes)
    try:
        for name in sorted(expected):
            with open(source / name, "rb") as src:
                _stream_copy(src, name, staging, expected, budget)
        _promote(staging, dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return str(dest)


def _promote(staging: Path, dest: Path) -> None:
    if not dest.exists():
        os.replace(staging, dest)
        return
    if _dir_signature(staging) == _dir_signature(dest):
        shutil.rmtree(staging, ignore_errors=True)
        return
    raise FileExistsError(f"existing installation differs at {dest}; refusing to replace")


def _dir_signature(root: Path) -> frozenset[tuple[str, int, str]]:
    signature: set[tuple[str, int, str]] = set()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entry = (
                path.relative_to(root).as_posix(),
                path.stat().st_size,
                _file_sha256(path),
            )
            signature.add(entry)
    return frozenset(signature)


def _extract_and_verify(archive: Path, staging: Path, manifest: ModelManifest) -> None:
    expected = {file.name: file for file in manifest.files}
    budget = _Budget(manifest.installed_size_bytes)
    extracted: set[str] = set()
    root: str | None = None
    has_bare = False

    def check_root(member_root: str | None) -> None:
        nonlocal root, has_bare
        if member_root is None:
            if root is not None:
                raise ArchiveError("archive mixes bare files with a root directory")
            has_bare = True
            return
        if has_bare:
            raise ArchiveError("archive mixes bare files with a root directory")
        if root is None:
            root = member_root
        elif root != member_root:
            raise ArchiveError(
                f"archive uses multiple root directories: {root!r} vs {member_root!r}"
            )

    def register(name: str, member_root: str | None) -> None:
        if name not in expected:
            raise ArchiveError(f"undeclared file in archive: {name!r}")
        if name in extracted:
            raise ArchiveError(f"duplicate member: {name!r}")
        check_root(member_root)

    if manifest.archive_format == "raw":
        if len(manifest.files) != 1:
            raise ArchiveError("raw archive must declare exactly one file")
        name = manifest.files[0].name
        with open(archive, "rb") as raw_src:
            _stream_copy(raw_src, name, staging, expected, budget)
        extracted.add(name)
    elif manifest.archive_format in ("tar.bz2", "tar.gz"):
        with tarfile.open(archive, "r:*") as tar:
            for index, member in enumerate(tar):
                if index >= _MAX_ARCHIVE_MEMBERS:
                    raise ArchiveError("archive has too many members")
                if member.issym() or member.islnk():
                    raise ArchiveError(f"link rejected: {member.name!r}")
                if member.ischr() or member.isblk() or member.isfifo():
                    raise ArchiveError(f"special file rejected: {member.name!r}")
                if member.isdir():
                    _reject_unsafe(member.name)
                    continue
                if not member.isfile():
                    raise ArchiveError(f"unsupported member type: {member.name!r}")
                name, member_root = _member_target(member.name)
                register(name, member_root)
                if member.size > expected[name].size_bytes:
                    raise ArchiveError(f"declared size for {name!r} exceeds the manifest")
                extracted_file = tar.extractfile(member)
                if extracted_file is None:
                    continue
                _stream_copy(extracted_file, name, staging, expected, budget)
                extracted.add(name)
    elif manifest.archive_format == "zip":
        with zipfile.ZipFile(archive) as zf:
            for index, info in enumerate(zf.infolist()):
                if index >= _MAX_ARCHIVE_MEMBERS:
                    raise ArchiveError("archive has too many members")
                mode = info.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise ArchiveError(f"symlink rejected: {info.filename!r}")
                if info.is_dir():
                    _reject_unsafe(info.filename)
                    continue
                if mode and not stat.S_ISREG(mode):
                    raise ArchiveError(f"special file rejected: {info.filename!r}")
                name, member_root = _member_target(info.filename)
                register(name, member_root)
                if info.file_size > expected[name].size_bytes:
                    raise ArchiveError(f"declared size for {name!r} exceeds the manifest")
                with zf.open(info) as zip_src:
                    _stream_copy(zip_src, name, staging, expected, budget)
                extracted.add(name)
    else:
        raise ArchiveError(f"unsupported archive format {manifest.archive_format!r}")

    missing = set(expected) - extracted
    if missing:
        raise ArchiveError(f"archive is missing declared files: {sorted(missing)}")
