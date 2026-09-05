"""Deterministic report-directory rescan — docs/INDEX_REBUILD.md §2-§5.

One algorithm for every trigger (startup backfill, needs-rebuild self-heal,
manual API call): a sorted full-directory rescan that skips unchanged files by
hash, enforces the frozen ``analysis_id`` ownership rule (winner = sorted-min
path of the CURRENT file set, independent of database history), quarantines
violations into ``index_issues``, marks every document NOT successfully claimed
by this run as ``missing`` (physical deletion only on explicit purge), and
finishes with the frozen ``verify_fts`` check. The scan NEVER writes to a
user's ``.md`` file.

Crash recovery is an idempotent re-run (§4): a persisted ``running`` state is
always orphaned at boot (single Engine owns the loopback port), the in-process
mutex plus a synchronously-set scheduled flag guarantee at most one rebuild,
and the report directory resolves from settings with the Engine-injected
fallback directory (so deleting the SQLite file still self-heals). A missing
or unusable directory (stale pointer, renamed folder, offline drive — not a
readable directory) is a fail-safe: the rebuild fails and parks
``needs_rebuild`` — it is never treated as an empty directory (that would let
the absence pass wipe the whole index), and it never silently falls through
to a different candidate directory (scanning the wrong root would mark every
real document missing). The usability gate runs at the endpoint pre-flight,
the scan entry, on batch read failures, and again before the absence pass —
a drive that dies mid-scan aborts the whole run with the existing index
intact. A pointer file that exists but cannot be read fails closed too
(``REPORT_POINTER_UNAVAILABLE``), never degrading into a fallback scan. The
resolver is shared with the report-READ path so a recovered index is fully
readable, not just searchable.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.reports.parser import (
    MD_BODY_STRUCTURE_INVALID,
    MD_EMPTY_BODY,
    MD_ENCODING_ERROR,
    MD_FRONTMATTER_INVALID,
    MD_MISSING_FIELD,
    MD_UNKNOWN_SCHEMA_VERSION,
    MarkdownParseError,
    ParsedReport,
    decode_report_bytes,
)
from evoblue_video_mcp.storage.db import IMMEDIATE_WRITE_OPTS
from evoblue_video_mcp.storage.report_repository import (
    FtsBodyTexts,
    ReportIndexEntry,
    get_index_status,
    get_report_document,
    list_document_paths,
    mark_report_missing,
    purge_report_document,
    record_index_issue,
    record_rebuild_counters,
    resolve_index_issue,
    resolve_issues_for_absent_paths,
    set_index_state,
    upsert_report_document,
    verify_fts,
)
from evoblue_video_mcp.storage.repository import get_app_settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50
_MD_DIAGNOSTIC_CODES = (
    MD_FRONTMATTER_INVALID,
    MD_UNKNOWN_SCHEMA_VERSION,
    MD_MISSING_FIELD,
    MD_BODY_STRUCTURE_INVALID,
    MD_ENCODING_ERROR,
    MD_EMPTY_BODY,
)


class RebuildRootUnavailable(RuntimeError):
    """The report directory is not configured or is not usable (§4 fail-safe).

    ``code`` is the stable diagnostic persisted in ``index_status`` and used
    by sanitized logging; the exception message is user-facing (endpoint 503)
    and deliberately stays free of filesystem details.
    """

    code = "ROOT_UNAVAILABLE"


class ReportPointerUnavailable(RebuildRootUnavailable):
    """The recovery pointer EXISTS but cannot be read (§4 fail-closed).

    Distinct from the bare :class:`RebuildRootUnavailable` (nothing
    configured): folding an unreadable pointer into "no pointer" would send
    the resolver to the fallback directory while a custom directory is
    configured -- scanning the wrong root would mark every real document
    missing, so this must fail closed instead.
    """

    code = "REPORT_POINTER_UNAVAILABLE"


REPORT_POINTER_FILENAME = "report-root.txt"


def read_report_pointer(data_dir: Path) -> Path | None:
    """Read the out-of-database report-directory pointer (§4).

    ``None`` means "no pointer / cleared" (file absent, or an empty
    tombstone). A pointer that exists but cannot be read raises
    :class:`ReportPointerUnavailable` -- collapsing that into ``None`` would
    fail OPEN to the fallback directory.
    """
    pointer = data_dir / REPORT_POINTER_FILENAME
    try:
        raw = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportPointerUnavailable(
            "recovery pointer exists but cannot be read"
        ) from exc
    return Path(raw) if raw else None


def write_report_pointer(data_dir: Path, report_dir: Path | None) -> None:
    """Persist (or clear, with ``None``) the report-directory pointer so a
    deleted database cannot lose a user-chosen custom directory.

    Crash-safe (§4): EVERY write -- setting and clearing alike -- is a temp
    file + fsync + atomic ``os.replace``. Clearing writes an EMPTY tombstone
    instead of unlinking, and any failure propagates so the caller can refuse
    the database commit: a silently swallowed failure here would let the
    settings row move on while a stale pointer later revives the old
    directory after the database is deleted.
    """
    pointer = data_dir / REPORT_POINTER_FILENAME
    pointer.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer.with_name(f"{pointer.name}.tmp-{os.getpid()}-{time.monotonic_ns()}")
    payload = b"" if report_dir is None else f"{report_dir}\n".encode()
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, pointer)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def root_is_usable(root: Path | None) -> TypeGuard[Path]:
    """§4 usability: configured, an existing directory, and enumerable.

    An unavailable root (stale pointer after a rename, offline external drive,
    permission change) must FAIL the rebuild -- it must never degrade into
    "empty directory" (the absence pass would wipe the whole index), nor
    silently fall through to a different candidate directory (scanning the
    wrong root would mark every real document missing).
    """
    if root is None:
        return False
    try:
        if not root.is_dir():
            return False
        with os.scandir(root) as entries:
            next(iter(entries), None)  # enumeration probe; OSError → unusable
    except OSError:
        return False
    return True


async def resolve_report_root(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    fallback_report_root: Path | None = None,
    pointer_file: Path | None = None,
) -> Path | None:
    """§4 resolution order: settings directory -> out-of-database pointer file
    (survives SQLite deletion) -> Engine-injected fallback -> ``None``."""
    async with session_factory() as sess:
        settings = await get_app_settings(sess)
    if settings is not None and settings.report_directory:
        return Path(settings.report_directory)
    if pointer_file is not None:
        pointer = read_report_pointer(pointer_file.parent)
        if pointer is not None:
            return pointer
    if fallback_report_root is not None:
        return Path(fallback_report_root)
    return None


@dataclass(frozen=True)
class RebuildOutcome:
    """Counters of one completed rescan (``index_status`` columns, §2)."""

    scanned: int
    indexed: int
    unchanged: int
    quarantined: int
    duplicates: int
    removed: int
    purged: int
    consistent: bool


@dataclass(frozen=True)
class _Claim:
    """A file successfully claimed as the winner for its ``analysis_id``."""

    analysis_id: str
    rel_path: str
    analyzed_at: float
    content_hash: str


@dataclass(frozen=True)
class _FileOutcome:
    """Immutable phase-1 result of one file, gathered WITHOUT any transaction
    (§6: the rescan never holds the write lock across disk I/O)."""

    rel_path: str
    raw: bytes | None
    parsed: ParsedReport | None
    parse_error: MarkdownParseError | None
    read_error_type: str | None


class IndexRebuildService:
    """Owns the single in-process rebuild and the startup reconciliation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        fallback_report_root: Path | None = None,
        pointer_file: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._fallback_report_root = fallback_report_root
        self._pointer_file = pointer_file
        self._lock = asyncio.Lock()
        self._scheduled = False
        self._background_tasks: set[asyncio.Task[None]] = set()

    # --- public API ---------------------------------------------------------

    async def reconcile_on_startup(self) -> None:
        """§4 startup reconciliation; auto-triggers a rebuild when needed.

        Never raises: a failed auto-rebuild parks the state at
        ``needs_rebuild`` for the next manual run and is logged instead.
        """
        try:
            if await self._startup_needs_rebuild():
                await self._locked_run(purge=False)
        except asyncio.CancelledError:
            raise
        except RebuildRootUnavailable as exc:
            # Keep the SPECIFIC diagnosis: _run already parked ROOT_UNAVAILABLE
            # (or the pointer variant) -- re-parking the generic REBUILD_FAILED
            # here would erase the reason from /api/index/status (§4).
            self._log_failure(exc, "startup index reconciliation failed")
            await self._park_needs_rebuild(exc.code)
        except Exception as exc:
            self._log_failure(exc, "startup index reconciliation failed")
            await self._park_needs_rebuild("REBUILD_FAILED")

    async def start_rebuild(self, *, purge: bool = False) -> bool:
        """Spawn a background rebuild (``POST /api/index/rebuild``, §5).

        Returns ``False`` when a rebuild is already scheduled or running (the
        API maps this to 409 ``REBUILD_ALREADY_RUNNING``). The scheduled flag
        is claimed synchronously BEFORE any await, so back-to-back and
        concurrent calls cannot both return True. The report root is
        PRE-FLIGHTED here -- not configured or not usable parks
        ``needs_rebuild``/``ROOT_UNAVAILABLE`` and raises, surfacing as 503 at
        the endpoint instead of an async failure after a 202 (§4 fail-safe).
        """
        if self._scheduled or self._lock.locked():
            return False
        self._scheduled = True  # synchronous claim: nothing may await before it
        try:
            try:
                root = await self.resolve_report_root()
            except RebuildRootUnavailable as exc:
                # Park BEFORE raising so /api/index/status shows the failure
                # with the SPECIFIC code (e.g. REPORT_POINTER_UNAVAILABLE),
                # not a stale idle (§4) — same discipline as the run itself.
                await self._park_needs_rebuild(exc.code)
                raise
            if not root_is_usable(root):
                await self._park_needs_rebuild("ROOT_UNAVAILABLE")
                raise RebuildRootUnavailable(
                    "report directory is not configured or cannot be read"
                )
        except BaseException:
            self._scheduled = False
            raise
        task = asyncio.create_task(self._scheduled_run(purge=purge))
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return True

    async def run_rebuild(self, *, purge: bool = False) -> RebuildOutcome:
        """Await a rebuild to completion (for callers that need the result)."""
        async with self._lock:
            return await self._run(purge=purge)

    async def shutdown(self) -> None:
        """Gracefully finish spawned background rebuilds (engine shutdown).

        A rebuild completing during shutdown is harmless (it only writes the
        database), so we await it; cancel only if it exceeds the grace period
        — cancelling mid-statement would strand an aiosqlite worker thread
        racing the event-loop teardown.
        """
        for task in list(self._background_tasks):
            try:
                await asyncio.wait_for(task, timeout=30.0)
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_failure(exc, "background rebuild failed during shutdown")

    # --- internals ----------------------------------------------------------

    def _log_failure(self, exc: BaseException, context: str) -> None:
        """Sanitized failure log: exception TYPE plus the stable error code —
        never str(exc)/traceback, which may carry absolute paths or database
        details (same policy as the worker error boundary)."""
        code = getattr(exc, "code", None) or "REBUILD_FAILED"
        logger.error("%s: %s (code=%s)", context, type(exc).__name__, code)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Consume the task result: swallow cancellation, log failures."""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._log_failure(exc, "background index rebuild failed")

    async def resolve_report_root(self) -> Path | None:
        """Public §4 resolver (settings → pointer file → fallback → ``None``).

        Shared with the report-READ path (history/report endpoints): after a
        deleted-database recovery the settings row is gone, yet the recovered
        index must be fully readable, not just listed and searchable.
        """
        return await resolve_report_root(
            self._session_factory,
            fallback_report_root=self._fallback_report_root,
            pointer_file=self._pointer_file,
        )

    async def _scheduled_run(self, *, purge: bool) -> None:
        try:
            async with self._lock:
                await self._run(purge=purge)
        finally:
            self._scheduled = False

    async def _startup_needs_rebuild(self) -> bool:
        """Apply §4 reconciliation and return whether a rebuild must run now."""
        async with self._session_factory() as sess:
            snapshot = await get_index_status(sess)
            document_count = int(
                (
                    await sess.execute(text("SELECT count(*) FROM report_documents"))
                ).scalar_one()
            )
        if snapshot.state == "running":
            # Single Engine owns the loopback port: booting proves the writer
            # of that state is dead. No heartbeat grace (§4).
            async with self._session_factory() as sess:
                await _begin_immediate(sess)
                await set_index_state(
                    sess,
                    state="needs_rebuild",
                    now=time.time(),
                    error_code="ORPHANED_BY_RESTART",
                )
                await sess.commit()
                snapshot = await get_index_status(sess)
        if snapshot.state == "needs_rebuild":
            return True
        if document_count == 0:
            try:
                root = await self.resolve_report_root()
            except RebuildRootUnavailable as exc:
                # A deleted database with an unusable root must not sit at a
                # lying "idle": park the specific code so /api/index/status
                # shows WHY history is empty (§4).
                await self._park_needs_rebuild(exc.code)
                return False
            if root_is_usable(root):
                if self._collect(root):
                    return True  # first-boot backfill / deleted-database self-heal
            else:
                await self._park_needs_rebuild(RebuildRootUnavailable.code)
                return False
        return False

    async def _locked_run(self, *, purge: bool) -> None:
        if self._lock.locked() or self._scheduled:
            return
        async with self._lock:
            await self._run(purge=purge)

    async def _run(self, *, purge: bool) -> RebuildOutcome:
        counters = {
            "scanned": 0,
            "indexed": 0,
            "unchanged": 0,
            "quarantined": 0,
            "duplicates": 0,
            "removed": 0,
            "purged": 0,
        }
        try:
            root = await self.resolve_report_root()
            if root is None or not root_is_usable(root):
                # Fail-safe (§4), re-checked at the SCAN ENTRY -- not only at
                # the endpoint pre-flight, closing the TOCTOU window between
                # the two: a missing or unreadable root is never an empty
                # directory, so the absence pass cannot mark (or with purge,
                # delete) the whole index.
                raise RebuildRootUnavailable(
                    "report directory is not configured or cannot be read"
                )
            async with self._session_factory() as sess:
                await _begin_immediate(sess)
                await set_index_state(sess, state="running", now=time.time())
                await sess.commit()
            files = self._collect(root)
            claims: dict[str, _Claim] = {}
            scanned: list[str] = []
            for chunk in _chunks(files, _BATCH_SIZE):
                batch_counts = await self._process_batch(root, chunk, claims, scanned)
                for key, value in batch_counts.items():
                    counters[key] += value
            # §4: the drive can drop AFTER enumeration and batches succeeded.
            # Absence decisions are destructive (missing / purge), so they run
            # only against a root that is STILL enumerable -- re-verified here,
            # a third time after the scan entry.
            if not await asyncio.to_thread(root_is_usable, root):
                raise RebuildRootUnavailable(
                    "report directory is not configured or cannot be read"
                )
            removed, purged = await self._mark_absent(claims, scanned, purge=purge)
            counters["removed"] += removed
            counters["purged"] += purged
            async with self._session_factory() as sess:
                verification = await verify_fts(sess)
            consistent = verification.consistent
        except RebuildRootUnavailable as exc:
            # Park the SPECIFIC code (ROOT_UNAVAILABLE or the pointer
            # variant): a root that died mid-scan is a different diagnosis
            # from a generic rebuild failure (§4).
            await self._park_needs_rebuild(exc.code)
            raise
        except Exception:
            await self._park_needs_rebuild("REBUILD_FAILED")
            raise
        async with self._session_factory() as sess:
            await _begin_immediate(sess)
            await record_rebuild_counters(
                sess,
                scanned=counters["scanned"],
                indexed=counters["indexed"],
                unchanged=counters["unchanged"],
                quarantined=counters["quarantined"],
                duplicates=counters["duplicates"],
                removed=counters["removed"],
                purged=counters["purged"],
            )
            await set_index_state(
                sess,
                state="idle" if consistent else "needs_rebuild",
                now=time.time(),
                error_code=None if consistent else "FTS_DRIFT",
            )
            await sess.commit()
        return RebuildOutcome(consistent=consistent, **counters)

    def _collect(self, root: Path) -> list[str]:
        """Recursive ``*.md`` walk in sorted relative-POSIX order (§2)."""
        found: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            parts = rel.split("/")
            if any(part.startswith(".") for part in parts):
                continue
            name = parts[-1].lower()
            if not name.endswith(".md") or name.endswith(".tmp"):
                continue
            found.append(rel)
        return sorted(found)

    async def _scan_files(
        self, root: Path, rel_paths: list[str]
    ) -> list[_FileOutcome]:
        """Phase 1 — read and parse WITHOUT any transaction (§6): the write
        lock is never held across disk I/O. A read failure probes the root
        first: once the drive has dropped mid-scan, EVERY read fails, and
        quarantining them one by one would hand the absence pass a full
        wipe — so an unusable root aborts the whole run."""
        outcomes: list[_FileOutcome] = []
        for rel in rel_paths:
            path = root / rel
            raw: bytes | None = None
            read_error_type: str | None = None
            try:
                raw = await asyncio.to_thread(path.read_bytes)
            except OSError as exc:
                if not await asyncio.to_thread(root_is_usable, root):
                    raise RebuildRootUnavailable(
                        "report directory is not configured or cannot be read"
                    ) from exc
                # Sanitized detail (§3): ``str(OSError)`` carries the absolute
                # path — the exception TYPE alone is stable and path-free; the
                # raw message goes to the local log only.
                logger.error(
                    "report file unreadable: %s (path=%s)",
                    type(exc).__name__,
                    rel,
                )
                read_error_type = type(exc).__name__
            parsed: ParsedReport | None = None
            parse_error: MarkdownParseError | None = None
            if raw is not None:
                try:
                    parsed = decode_report_bytes(raw)
                except MarkdownParseError as exc:
                    parse_error = exc
            outcomes.append(
                _FileOutcome(
                    rel_path=rel,
                    raw=raw,
                    parsed=parsed,
                    parse_error=parse_error,
                    read_error_type=read_error_type,
                )
            )
        return outcomes

    async def _process_batch(
        self,
        root: Path,
        rel_paths: list[str],
        claims: dict[str, _Claim],
        scanned: list[str],
    ) -> dict[str, int]:
        """Two phases (§6): ``_scan_files`` gathers immutable results with no
        transaction held; the SHORT IMMEDIATE transaction here only reconciles
        them into the database — milliseconds, never disk I/O."""
        counts = {
            "scanned": 0,
            "indexed": 0,
            "unchanged": 0,
            "quarantined": 0,
            "duplicates": 0,
        }
        outcomes = await self._scan_files(root, rel_paths)
        now = time.time()
        async with self._session_factory() as sess:
            await _begin_immediate(sess)
            for outcome in outcomes:
                rel = outcome.rel_path
                counts["scanned"] += 1
                scanned.append(rel)
                if outcome.read_error_type is not None:
                    await record_index_issue(
                        sess,
                        issue_code=MD_FRONTMATTER_INVALID,
                        relative_path=rel,
                        detail=f"unreadable ({outcome.read_error_type})",
                        now=now,
                    )
                    counts["quarantined"] += 1
                    continue
                if outcome.parse_error is not None or outcome.parsed is None:
                    exc = outcome.parse_error
                    if exc is not None:
                        await record_index_issue(
                            sess,
                            issue_code=exc.code,
                            relative_path=rel,
                            detail=str(exc),
                            now=now,
                        )
                        for other in _MD_DIAGNOSTIC_CODES:
                            if other != exc.code:
                                await resolve_index_issue(
                                    sess, issue_code=other, relative_path=rel, now=now
                                )
                    counts["quarantined"] += 1
                    continue

                parsed = outcome.parsed
                raw = outcome.raw
                assert raw is not None  # a readable file always yields bytes
                content_hash = hashlib.sha256(raw).hexdigest()
                analyzed_at = parsed.analyzed_at.timestamp()
                if not parsed.has_analysis_content:
                    # §3 warning: identity is valid, index it, but record it.
                    await record_index_issue(
                        sess,
                        issue_code=MD_EMPTY_BODY,
                        relative_path=rel,
                        detail="no analysis sections in body",
                        now=now,
                    )
                else:
                    await resolve_index_issue(
                        sess, issue_code=MD_EMPTY_BODY, relative_path=rel, now=now
                    )
                # The blocking parse problems of this path are gone this run;
                # a successful claim also clears an earlier duplicate loss.
                for code in _MD_DIAGNOSTIC_CODES:
                    if code == MD_EMPTY_BODY:
                        continue
                    await resolve_index_issue(
                        sess, issue_code=code, relative_path=rel, now=now
                    )
                await resolve_index_issue(
                    sess, issue_code="DUPLICATE_ANALYSIS_ID", relative_path=rel, now=now
                )

                analysis_id = parsed.analysis_id
                incumbent = claims.get(analysis_id)
                if incumbent is not None:
                    # Sorted iteration ⇒ the incumbent path sorts ≤ this one;
                    # this file is a losing duplicate (contract §2.4).
                    await record_index_issue(
                        sess,
                        issue_code="DUPLICATE_ANALYSIS_ID",
                        relative_path=rel,
                        detail=(
                            f"analysis_id {analysis_id}: winner {incumbent.rel_path} "
                            f"(analyzed_at={incumbent.analyzed_at}, "
                            f"sha256={incumbent.content_hash[:12]}); "
                            f"this file {rel} (analyzed_at={analyzed_at}, "
                            f"sha256={content_hash[:12]})"
                        ),
                        now=now,
                    )
                    counts["duplicates"] += 1
                    continue
                claim = _Claim(
                    analysis_id=analysis_id,
                    rel_path=rel,
                    analyzed_at=analyzed_at,
                    content_hash=content_hash,
                )
                claims[analysis_id] = claim

                doc = await get_report_document(sess, job_id=analysis_id)
                if (
                    doc is not None
                    and doc.relative_path == rel
                    and doc.content_hash == content_hash
                    and doc.doc_status == "active"
                ):
                    counts["unchanged"] += 1
                    continue
                entry = ReportIndexEntry(
                    job_id=analysis_id,
                    analysis_id=analysis_id,
                    title=parsed.title,
                    platform=parsed.platform,
                    author=parsed.author,
                    video_id=parsed.video_id,
                    source_url=parsed.source_url,
                    published_at=(
                        parsed.published_at.timestamp() if parsed.published_at else None
                    ),
                    analyzed_at=analyzed_at,
                    summary_mode=parsed.summary_mode,
                    language=parsed.language,
                    asr_provider=parsed.asr_provider,
                    asr_model=parsed.asr_model,
                    asr_model_version=parsed.asr_model_version,
                    tags=parsed.tags,
                    summary_preview=parsed.core_summary[:200],
                    relative_path=rel,
                    content_hash=content_hash,
                    byte_size=len(raw),
                    doc_source="rebuild",
                )
                await upsert_report_document(
                    sess,
                    entry=entry,
                    body=FtsBodyTexts(
                        summary=parsed.core_summary,
                        transcript=parsed.transcript or "",
                    ),
                    now=now,
                )
                counts["indexed"] += 1
            await sess.commit()
        return counts

    async def _mark_absent(
        self, claims: dict[str, _Claim], scanned: list[str], *, purge: bool
    ) -> tuple[int, int]:
        """Documents NOT claimed by this run exit history and search (§5):
        ``missing`` + FTS row dropped; purge upgrades to physical deletion.
        A scanned-but-unclaimed path (corrupted, re-identified, duplicate
        loser) therefore cannot keep its stale active row. Issue resolution
        for vanished FILES uses the scanned path list (a file still on disk
        keeps its genuine diagnostics)."""
        removed = purged = 0
        now = time.time()
        scanned_set = set(scanned)
        async with self._session_factory() as sess:
            await _begin_immediate(sess)  # reads then writes below (§6)
            for job_id, rel_path in await list_document_paths(sess):
                claim = claims.get(job_id)
                if claim is not None and claim.rel_path == rel_path:
                    continue
                # Physical purge is ONLY for files genuinely gone from disk.
                # A present-but-unclaimed file (corrupted / re-identified /
                # duplicate loser) keeps its audit row and merely exits search.
                if purge and rel_path not in scanned_set:
                    if await purge_report_document(sess, job_id=job_id):
                        purged += 1
                else:
                    await mark_report_missing(sess, job_id=job_id, now=now)
                    removed += 1
            await resolve_issues_for_absent_paths(
                sess, present_paths=scanned, now=now
            )
            await sess.commit()
        return removed, purged

    async def _park_needs_rebuild(self, error_code: str) -> None:
        async with self._session_factory() as sess:
            await _begin_immediate(sess)
            await set_index_state(
                sess, state="needs_rebuild", now=time.time(), error_code=error_code
            )
            await sess.commit()


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _begin_immediate(sess: AsyncSession) -> None:
    """Open the next transaction as ``BEGIN IMMEDIATE`` (§6): rescan batches
    run concurrently with pipeline writes, and a deferred read-then-write
    transaction dies with an unretryable BUSY_SNAPSHOT when another writer
    commits in between (see ``db.immediate_write_transaction``). These
    sessions are freshly opened, so there is no read transaction to close."""
    await sess.connection(execution_options=IMMEDIATE_WRITE_OPTS)
