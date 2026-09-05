"""Report index persistence — docs/FTS5_SCHEMA.md §5/§6.

Contract invariants enforced here:

- **Pairing-atomic writes**: ``report_documents`` and ``report_fts`` rows are
  written inside a SAVEPOINT (``session.begin_nested()``), so a failure in
  either half rolls the pair back even when the caller does not roll back the
  outer transaction — e.g. a worker that catches the exception and then
  commits a failure status on the same session can never durably land a
  half-written pair. Functions in this module never commit.
- **No rowid drift**: updates reuse the existing ``report_documents.id`` as
  the FTS ``rowid`` (delete old row, insert new one); ``INSERT OR REPLACE``
  side effects are never relied upon.
- **Single source of truth for index text** (§3): FTS ``title``/``tags``/
  ``author``/``url`` are derived from the :class:`ReportIndexEntry` metadata;
  callers only supply the body sections (``summary``/``transcript``).
  Contradicting metadata vs. index text is unrepresentable.
- **Status semantics**: only ``active → stale`` is allowed (``stale`` keeps
  its FTS row; ``missing`` must never reach ``stale`` — its FTS row is gone
  and recovery is a full re-index); ``missing`` drops the FTS row.
  ``verify_fts`` counts ``active + stale``.
- **Open-issue protocol**: an open ``(issue_code, relative_path)`` row is
  updated in place (``last_seen_at``), resolved rows get ``resolved_at``, a
  recurrence inserts a fresh row — enabled by the partial unique index
  ``uq_index_issues_open``.

Raw SQL is deliberate: the DDL is frozen contract text and the FTS virtual
table is not ORM-mappable; keeping every statement adjacent to the contract
text makes drift reviewable.
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from evoblue_video_mcp.storage.fts import denormalize_for_display, normalize_for_fts

IndexState = Literal["idle", "running", "needs_rebuild"]
_INDEX_STATES: tuple[str, ...] = ("idle", "running", "needs_rebuild")
_DOC_SOURCES = ("pipeline", "rebuild")
# Markdown Schema v1: summary_mode is exactly one of the three modes. Empty is
# NOT allowed — report_documents is a fresh v8 table, so there is no legacy
# record to accommodate, and the history API must never emit an off-contract mode.
_SUMMARY_MODES = ("auto", "standard", "unboxing")


@dataclass(frozen=True)
class ReportIndexEntry:
    """Everything needed to index one report (``report_documents`` columns).

    The frozen invariants (Markdown Schema v1 identity, ``doc_source`` enum,
    sha-256 hash shape, preview cap) are validated centrally at construction
    so a bad entry can never reach the database.
    """

    job_id: str
    analysis_id: str
    title: str
    platform: str
    author: str
    video_id: str
    source_url: str
    published_at: float | None
    analyzed_at: float
    summary_mode: str
    language: str = ""
    asr_provider: str = ""
    asr_model: str = ""
    asr_model_version: str = ""
    tags: tuple[str, ...] = ()
    summary_preview: str = ""
    relative_path: str = ""
    content_hash: str = ""
    byte_size: int | None = None
    doc_source: str = "pipeline"

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")
        if self.analysis_id != self.job_id:
            raise ValueError(
                "Markdown Schema v1 mandates analysis_id == job_id "
                f"(got {self.analysis_id!r} != {self.job_id!r})"
            )
        for name in ("title", "platform", "video_id", "source_url", "relative_path"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")
        if self.doc_source not in _DOC_SOURCES:
            raise ValueError(
                f"doc_source must be one of {_DOC_SOURCES}, got {self.doc_source!r}"
            )
        if len(self.content_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.content_hash
        ):
            raise ValueError("content_hash must be a lowercase sha-256 hex digest")
        if self.byte_size is not None and self.byte_size < 0:
            raise ValueError("byte_size must be non-negative")
        if len(self.summary_preview) > 200:
            raise ValueError("summary_preview must be at most 200 characters")
        if self.summary_mode not in _SUMMARY_MODES:
            raise ValueError(
                f"summary_mode must be one of {_SUMMARY_MODES} (Markdown Schema v1), "
                f"got {self.summary_mode!r}"
            )
        parsed_url = urlsplit(self.source_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise ValueError(
                f"source_url must be an absolute HTTP(S) URL, got {self.source_url!r}"
            )
        if self.platform != self.platform.lower():
            raise ValueError(
                f"platform must be lowercase, got {self.platform!r}"
            )
        if (
            not math.isfinite(self.analyzed_at)
            or self.analyzed_at <= 0
            or (
                self.published_at is not None
                and (not math.isfinite(self.published_at) or self.published_at <= 0)
            )
        ):
            raise ValueError(
                "timestamps must be finite positive epoch seconds "
                "(NaN/inf cannot be anchored to an epoch)"
            )


@dataclass(frozen=True)
class FtsBodyTexts:
    """Body-section texts (§3); title/tags/author/url come from the entry."""

    summary: str = ""
    transcript: str = ""


@dataclass(frozen=True)
class ReportDocumentRecord:
    """A stored report document with its index bookkeeping."""

    id: int
    job_id: str
    analysis_id: str
    title: str
    platform: str
    author: str
    video_id: str
    source_url: str
    published_at: float | None
    analyzed_at: float
    language: str
    summary_mode: str
    asr_provider: str
    asr_model: str
    asr_model_version: str
    tags: tuple[str, ...]
    summary_preview: str
    relative_path: str
    content_hash: str
    byte_size: int | None
    doc_source: str
    doc_status: str
    indexed_at: float
    updated_at: float


@dataclass(frozen=True)
class IndexIssueRecord:
    id: int
    issue_code: str
    relative_path: str
    detail: str | None
    first_seen_at: float
    last_seen_at: float
    resolved_at: float | None


@dataclass(frozen=True)
class IndexStatusSnapshot:
    state: str
    started_at: float | None
    finished_at: float | None
    scanned: int
    indexed: int
    unchanged: int
    quarantined: int
    duplicates: int
    removed: int
    purged: int
    last_error_code: str | None


@dataclass(frozen=True)
class FtsVerification:
    consistent: bool
    document_count: int
    fts_row_count: int
    documents_missing_fts: tuple[int, ...]
    fts_orphan_rows: tuple[int, ...]


@dataclass(frozen=True)
class ReportSearchHit:
    """One search result — the ``/api/search`` item shape (contract §4)."""

    job_id: str
    title: str
    platform: str
    analyzed_at: float
    snippet: str
    matched_fields: tuple[str, ...]
    doc_status: str


_DOCUMENT_COLUMNS = (
    "id, job_id, analysis_id, title, platform, author, video_id, source_url, "
    "published_at, analyzed_at, language, summary_mode, asr_provider, "
    "asr_model, asr_model_version, tags_json, summary_preview, relative_path, "
    "content_hash, byte_size, doc_source, doc_status, indexed_at, updated_at"
)


def _fts_row_values(entry: ReportIndexEntry, body: FtsBodyTexts) -> dict[str, str]:
    """Build the six FTS columns from ONE source: the entry + body sections (§3)."""
    return {
        "title": normalize_for_fts(entry.title),
        "tags": normalize_for_fts(" ".join(entry.tags)),
        "author": normalize_for_fts(entry.author),
        "summary": normalize_for_fts(body.summary),
        "transcript": normalize_for_fts(body.transcript),
        "url": entry.source_url.lower(),
    }


async def upsert_report_document(
    session: AsyncSession, *, entry: ReportIndexEntry, body: FtsBodyTexts, now: float
) -> int:
    """Insert or refresh one indexed report; returns ``report_documents.id``.

    Frozen pairing protocol (§5): look up the id by ``job_id``; on update the
    same id is reused and the old FTS row is deleted before the new one is
    inserted; ``doc_status`` resets to ``active``. The whole pair runs inside a
    SAVEPOINT so a mid-pair failure cannot survive an outer commit (a worker
    that swallows the exception and commits a failure state stays clean).
    No commit here — the caller owns the transaction.
    """
    async with session.begin_nested():
        existing = (
            await session.execute(
                text("SELECT id FROM report_documents WHERE job_id = :job_id"),
                {"job_id": entry.job_id},
            )
        ).scalar()
        values: dict[str, object] = {
            "job_id": entry.job_id,
            "analysis_id": entry.analysis_id,
            "title": entry.title,
            "platform": entry.platform,
            "author": entry.author,
            "video_id": entry.video_id,
            "source_url": entry.source_url,
            "published_at": entry.published_at,
            "analyzed_at": entry.analyzed_at,
            "language": entry.language,
            "summary_mode": entry.summary_mode,
            "asr_provider": entry.asr_provider,
            "asr_model": entry.asr_model,
            "asr_model_version": entry.asr_model_version,
            "tags_json": json.dumps(list(entry.tags), ensure_ascii=False),
            "summary_preview": entry.summary_preview,
            "relative_path": entry.relative_path,
            "content_hash": entry.content_hash,
            "byte_size": entry.byte_size,
            "doc_source": entry.doc_source,
            "doc_status": "active",
            "indexed_at": now,
            "updated_at": now,
        }
        if existing is None:
            doc_id = int(
                (
                    await session.execute(
                        text(
                            "INSERT INTO report_documents ("
                            "job_id, analysis_id, title, platform, author, video_id, "
                            "source_url, published_at, analyzed_at, language, "
                            "summary_mode, asr_provider, asr_model, asr_model_version, "
                            "tags_json, summary_preview, relative_path, content_hash, "
                            "byte_size, doc_source, doc_status, indexed_at, updated_at"
                            ") VALUES ("
                            ":job_id, :analysis_id, :title, :platform, :author, :video_id, "
                            ":source_url, :published_at, :analyzed_at, :language, "
                            ":summary_mode, :asr_provider, :asr_model, :asr_model_version, "
                            ":tags_json, :summary_preview, :relative_path, :content_hash, "
                            ":byte_size, :doc_source, :doc_status, :indexed_at, :updated_at"
                            ") RETURNING id"
                        ),
                        values,
                    )
                ).scalar_one()
            )
        else:
            doc_id = int(existing)
            values["id"] = doc_id
            await session.execute(
                text(
                    "UPDATE report_documents SET "
                    "title = :title, platform = :platform, author = :author, "
                    "video_id = :video_id, source_url = :source_url, "
                    "published_at = :published_at, analyzed_at = :analyzed_at, "
                    "language = :language, summary_mode = :summary_mode, "
                    "asr_provider = :asr_provider, asr_model = :asr_model, "
                    "asr_model_version = :asr_model_version, tags_json = :tags_json, "
                    "summary_preview = :summary_preview, relative_path = :relative_path, "
                    "content_hash = :content_hash, byte_size = :byte_size, "
                    "doc_source = :doc_source, doc_status = :doc_status, "
                    "indexed_at = :indexed_at, updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                values,
            )
        await session.execute(
            text("DELETE FROM report_fts WHERE rowid = :id"), {"id": doc_id}
        )
        await session.execute(
            text(
                "INSERT INTO report_fts (rowid, title, tags, author, summary, "
                "transcript, url) VALUES (:id, :title, :tags, :author, :summary, "
                ":transcript, :url)"
            ),
            {"id": doc_id, **_fts_row_values(entry, body)},
        )
    return doc_id


async def get_report_document(
    session: AsyncSession, *, job_id: str
) -> ReportDocumentRecord | None:
    row = (
        await session.execute(
            text(f"SELECT {_DOCUMENT_COLUMNS} FROM report_documents WHERE job_id = :job_id"),
            {"job_id": job_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return _to_record(row)


def _to_record(row: Any) -> ReportDocumentRecord:
    """Map one ``report_documents`` row (column set fixed by frozen DDL)."""
    tags = json.loads(row["tags_json"])
    return ReportDocumentRecord(
        id=row["id"],
        job_id=row["job_id"],
        analysis_id=row["analysis_id"],
        title=row["title"],
        platform=row["platform"],
        author=row["author"],
        video_id=row["video_id"],
        source_url=row["source_url"],
        published_at=row["published_at"],
        analyzed_at=row["analyzed_at"],
        language=row["language"],
        summary_mode=row["summary_mode"],
        asr_provider=row["asr_provider"],
        asr_model=row["asr_model"],
        asr_model_version=row["asr_model_version"],
        tags=tuple(tags),
        summary_preview=row["summary_preview"],
        relative_path=row["relative_path"],
        content_hash=row["content_hash"],
        byte_size=row["byte_size"],
        doc_source=row["doc_source"],
        doc_status=row["doc_status"],
        indexed_at=row["indexed_at"],
        updated_at=row["updated_at"],
    )


async def mark_report_stale(session: AsyncSession, *, job_id: str, now: float) -> bool:
    """Hash drift on read: keep the FTS row (searchable), flag content edited.

    Only ``active → stale`` is legal: a ``missing`` document has no FTS row, so
    flipping it to ``stale`` would instantly trip ``verify_fts`` — recovery for
    missing files is a full re-index (upsert), never a status edit. Returns
    whether a row changed; idempotent.
    """
    doc = (
        await session.execute(
            text(
                "SELECT id, doc_status FROM report_documents WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        )
    ).mappings().first()
    if doc is None or doc["doc_status"] != "active":
        return False
    await session.execute(
        text(
            "UPDATE report_documents SET doc_status = 'stale', updated_at = :now "
            "WHERE id = :id AND doc_status = 'active'"
        ),
        {"id": doc["id"], "now": now},
    )
    return True


async def mark_report_missing(session: AsyncSession, *, job_id: str, now: float) -> bool:
    """File gone from disk: drop the FTS row, keep the audit record.

    The status update and FTS delete are one SAVEPOINT-guarded pair. Returns
    whether a row changed; idempotent (a ``missing`` document is left as is).
    """
    async with session.begin_nested():
        doc = (
            await session.execute(
                text(
                    "SELECT id, doc_status FROM report_documents "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )
        ).mappings().first()
        if doc is None or doc["doc_status"] == "missing":
            return False
        await session.execute(
            text(
                "UPDATE report_documents SET doc_status = 'missing', updated_at = :now "
                "WHERE id = :id"
            ),
            {"id": doc["id"], "now": now},
        )
        await session.execute(
            text("DELETE FROM report_fts WHERE rowid = :id"), {"id": doc["id"]}
        )
    return True


async def record_index_issue(
    session: AsyncSession,
    *,
    issue_code: str,
    relative_path: str,
    detail: str,
    now: float,
) -> int:
    """Record a scan issue: update the open row's ``last_seen_at`` or insert.

    Open uniqueness is enforced by ``uq_index_issues_open``; resolved rows are
    never touched, so a recurrence after resolution inserts a fresh row.
    """
    open_id = (
        await session.execute(
            text(
                "SELECT id FROM index_issues WHERE issue_code = :code "
                "AND relative_path = :path AND resolved_at IS NULL"
            ),
            {"code": issue_code, "path": relative_path},
        )
    ).scalar()
    if open_id is not None:
        await session.execute(
            text("UPDATE index_issues SET last_seen_at = :now WHERE id = :id"),
            {"id": open_id, "now": now},
        )
        return int(open_id)
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO index_issues (issue_code, relative_path, detail, "
                    "first_seen_at, last_seen_at) "
                    "VALUES (:code, :path, :detail, :now, :now) RETURNING id"
                ),
                {"code": issue_code, "path": relative_path, "detail": detail, "now": now},
            )
        ).scalar_one()
    )


async def resolve_index_issue(
    session: AsyncSession, *, issue_code: str, relative_path: str, now: float
) -> int:
    """Mark open issues for one path resolved; returns how many rows changed."""
    open_rows = (
        await session.execute(
            text(
                "SELECT id FROM index_issues WHERE issue_code = :code "
                "AND relative_path = :path AND resolved_at IS NULL"
            ),
            {"code": issue_code, "path": relative_path},
        )
    ).scalars().all()
    if not open_rows:
        return 0
    await session.execute(
        text(
            "UPDATE index_issues SET resolved_at = :now WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(open_rows), "now": now},
    )
    return len(open_rows)


async def list_open_issues(
    session: AsyncSession,
    *,
    issue_code: str | None = None,
    open_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[IndexIssueRecord]:
    """Index issues, ``last_seen_at`` descending — the ``/api/index/issues`` source.

    ``open_only=True`` (default) mirrors the API's ``open=true`` filter;
    ``False`` returns resolved rows as well (contract §5).
    """
    conditions = "resolved_at IS NULL" if open_only else "1 = 1"
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if issue_code is not None:
        conditions += " AND issue_code = :code"
        params["code"] = issue_code
    rows = (
        await session.execute(
            text(
                "SELECT id, issue_code, relative_path, detail, first_seen_at, "
                "last_seen_at, resolved_at FROM index_issues "
                f"WHERE {conditions} "
                "ORDER BY last_seen_at DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return [
        IndexIssueRecord(
            id=row["id"],
            issue_code=row["issue_code"],
            relative_path=row["relative_path"],
            detail=row["detail"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            resolved_at=row["resolved_at"],
        )
        for row in rows
    ]


async def count_open_issues(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM index_issues WHERE resolved_at IS NULL")
            )
        ).scalar_one()
    )


async def count_index_issues(
    session: AsyncSession,
    *,
    issue_code: str | None = None,
    open_only: bool = True,
) -> int:
    """Total for ``/api/index/issues`` pagination (same filters as the list)."""
    conditions = "resolved_at IS NULL" if open_only else "1 = 1"
    params: dict[str, object] = {}
    if issue_code is not None:
        conditions += " AND issue_code = :code"
        params["code"] = issue_code
    return int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM index_issues WHERE {conditions}"), params
            )
        ).scalar_one()
    )


async def set_index_state(
    session: AsyncSession,
    *,
    state: IndexState,
    now: float,
    error_code: str | None = None,
) -> None:
    """Transition the singleton rebuild-state row (``index_status``, id = 1).

    Entering ``running`` starts a fresh attempt (timestamps and counters reset);
    ``idle`` stamps ``finished_at``; ``needs_rebuild`` records the reason and
    leaves timestamps for the startup reconciliation to inspect.
    """
    if state not in _INDEX_STATES:
        raise ValueError(f"unknown index state: {state!r}")
    if state == "running":
        await session.execute(
            text(
                "UPDATE index_status SET state = 'running', started_at = :now, "
                "finished_at = NULL, scanned = 0, indexed = 0, unchanged = 0, "
                "quarantined = 0, duplicates = 0, removed = 0, purged = 0, "
                "last_error_code = NULL WHERE id = 1"
            ),
            {"now": now},
        )
    elif state == "idle":
        await session.execute(
            text(
                "UPDATE index_status SET state = 'idle', finished_at = :now, "
                "last_error_code = :code WHERE id = 1"
            ),
            {"now": now, "code": error_code},
        )
    else:
        await session.execute(
            text(
                "UPDATE index_status SET state = 'needs_rebuild', "
                "last_error_code = :code WHERE id = 1"
            ),
            {"code": error_code},
        )


async def get_index_status(session: AsyncSession) -> IndexStatusSnapshot:
    row = (
        await session.execute(
            text(
                "SELECT state, started_at, finished_at, scanned, indexed, "
                "unchanged, quarantined, duplicates, removed, purged, "
                "last_error_code FROM index_status WHERE id = 1"
            )
        )
    ).mappings().one()
    return IndexStatusSnapshot(
        state=row["state"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        scanned=row["scanned"],
        indexed=row["indexed"],
        unchanged=row["unchanged"],
        quarantined=row["quarantined"],
        duplicates=row["duplicates"],
        removed=row["removed"],
        purged=row["purged"],
        last_error_code=row["last_error_code"],
    )


async def record_rebuild_counters(
    session: AsyncSession,
    *,
    scanned: int = 0,
    indexed: int = 0,
    unchanged: int = 0,
    quarantined: int = 0,
    duplicates: int = 0,
    removed: int = 0,
    purged: int = 0,
) -> None:
    """Accumulate batch counters into ``index_status`` (single row)."""
    await session.execute(
        text(
            "UPDATE index_status SET "
            "scanned = scanned + :scanned, indexed = indexed + :indexed, "
            "unchanged = unchanged + :unchanged, "
            "quarantined = quarantined + :quarantined, "
            "duplicates = duplicates + :duplicates, removed = removed + :removed, "
            "purged = purged + :purged WHERE id = 1"
        ),
        {
            "scanned": scanned,
            "indexed": indexed,
            "unchanged": unchanged,
            "quarantined": quarantined,
            "duplicates": duplicates,
            "removed": removed,
            "purged": purged,
        },
    )


async def verify_fts(session: AsyncSession) -> FtsVerification:
    """Frozen completeness check (§6): FTS rows == ``active + stale`` documents.

    ``missing`` documents dropped their FTS row by design and must not count.
    Repair is always a rebuild, never an in-place patch — the caller decides.
    """
    document_count = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM report_documents "
                    "WHERE doc_status IN ('active', 'stale')"
                )
            )
        ).scalar_one()
    )
    fts_row_count = int(
        (await session.execute(text("SELECT count(*) FROM report_fts"))).scalar_one()
    )
    documents_missing_fts = tuple(
        (
            await session.execute(
                text(
                    "SELECT d.id FROM report_documents d "
                    "WHERE d.doc_status IN ('active', 'stale') "
                    "AND NOT EXISTS (SELECT 1 FROM report_fts f WHERE f.rowid = d.id) "
                    "ORDER BY d.id"
                )
            )
        ).scalars()
    )
    fts_orphan_rows = tuple(
        (
            await session.execute(
                text(
                    "SELECT f.rowid FROM report_fts f WHERE NOT EXISTS ("
                    "SELECT 1 FROM report_documents d WHERE d.id = f.rowid "
                    "AND d.doc_status IN ('active', 'stale')) ORDER BY f.rowid"
                )
            )
        ).scalars()
    )
    return FtsVerification(
        consistent=(
            document_count == fts_row_count
            and not documents_missing_fts
            and not fts_orphan_rows
        ),
        document_count=document_count,
        fts_row_count=fts_row_count,
        documents_missing_fts=documents_missing_fts,
        fts_orphan_rows=fts_orphan_rows,
    )


async def list_report_documents(
    session: AsyncSession,
    *,
    platform: str | None = None,
    language: str | None = None,
    asr_provider: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ReportDocumentRecord], int]:
    """Indexed reports for ``GET /api/history`` (contract §1): ``active``/``stale``
    documents, ``analyzed_at`` descending, optional exact-match filters."""
    conditions = ["doc_status IN ('active', 'stale')"]
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if platform is not None:
        conditions.append("platform = :platform")
        params["platform"] = platform
    if language is not None:
        conditions.append("language = :language")
        params["language"] = language
    if asr_provider is not None:
        conditions.append("asr_provider = :asr_provider")
        params["asr_provider"] = asr_provider
    where = " AND ".join(conditions)
    total = int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM report_documents WHERE {where}"), params
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            text(
                f"SELECT {_DOCUMENT_COLUMNS} FROM report_documents WHERE {where} "
                "ORDER BY analyzed_at DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()
    return [_to_record(row) for row in rows], total


_FTS_FIELD_INDEX: dict[str, int] = {
    "title": 0,
    "tags": 1,
    "author": 2,
    "summary": 3,
    "transcript": 4,
    "url": 5,
}


async def search_reports(
    session: AsyncSession,
    *,
    match_query: str,
    or_query: str,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[ReportSearchHit], int]:
    """FTS search for ``GET /api/search`` (contract §4).

    ``match_query``/``or_query`` come from ``storage.fts.build_match_query`` and
    its OR-joined variant (phrase list for per-column matched-field checks).
    Ranking is ``bm25`` then ``analyzed_at`` descending; ``missing`` documents
    never match (their FTS rows are gone); ``stale`` documents participate.
    """
    base = (
        "FROM report_fts JOIN report_documents d ON d.id = report_fts.rowid "
        "WHERE report_fts MATCH :match AND d.doc_status IN ('active', 'stale')"
    )
    params: dict[str, object] = {"match": match_query, "limit": limit, "offset": offset}
    total = int(
        (
            await session.execute(text(f"SELECT count(*) {base}"), params)
        ).scalar_one()
    )
    rows = (
        await session.execute(
            text(
                f"SELECT report_fts.rowid AS rowid, d.job_id AS job_id, d.title AS title, "
                "d.platform AS platform, d.analyzed_at AS analyzed_at, "
                "d.doc_status AS doc_status, "
                # snippet() needs the MATCH context of the same statement; compute
                # all six columns here and pick the first ACTUALLY matched column
                # per hit (a tags/author/url-only hit must snippet from its own
                # column, not fall back to an unrelated summary).
                "snippet(report_fts, 0, '[', ']', '…', 12) AS snippet_title, "
                "snippet(report_fts, 1, '[', ']', '…', 12) AS snippet_tags, "
                "snippet(report_fts, 2, '[', ']', '…', 12) AS snippet_author, "
                "snippet(report_fts, 3, '[', ']', '…', 12) AS snippet_summary, "
                "snippet(report_fts, 4, '[', ']', '…', 12) AS snippet_transcript, "
                "snippet(report_fts, 5, '[', ']', '…', 12) AS snippet_url "
                f"{base} ORDER BY bm25(report_fts), d.analyzed_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings().all()

    hits: list[ReportSearchHit] = []
    for row in rows:
        rowid = row["rowid"]
        matched: list[str] = []
        for field in _FTS_FIELD_INDEX:
            hit = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM report_fts "
                        f"WHERE rowid = :rowid AND {field} MATCH :orq"
                    ),
                    {"rowid": rowid, "orq": or_query},
                )
            ).scalar_one()
            if int(hit) > 0:
                matched.append(field)
        snippet_by_column = {
            "title": row["snippet_title"],
            "tags": row["snippet_tags"],
            "author": row["snippet_author"],
            "summary": row["snippet_summary"],
            "transcript": row["snippet_transcript"],
            "url": row["snippet_url"],
        }
        # First matched column in canonical field order (matched is built in
        # that order); a hit always matches at least one column. The snippet is
        # display text — denormalize the CJK storage form before it leaves the
        # repository (HISTORY_SEARCH_API §4 shows natural Chinese).
        raw_snippet = str(
            snippet_by_column[matched[0]] if matched else snippet_by_column["summary"]
        )
        snippet = denormalize_for_display(raw_snippet)
        hits.append(
            ReportSearchHit(
                job_id=row["job_id"],
                title=row["title"],
                platform=row["platform"],
                analyzed_at=row["analyzed_at"],
                snippet=snippet,
                matched_fields=tuple(matched),
                doc_status=row["doc_status"],
            )
        )
    return hits, total


async def purge_report_document(session: AsyncSession, *, job_id: str) -> bool:
    """Physically delete one indexed report (explicit ``purge=true`` rebuild).

    The FTS row and the ``report_documents`` row die together; the file on disk
    is never touched. Returns whether a row was deleted.
    """
    async with session.begin_nested():
        doc = (
            await session.execute(
                text("SELECT id FROM report_documents WHERE job_id = :job_id"),
                {"job_id": job_id},
            )
        ).scalar()
        if doc is None:
            return False
        await session.execute(
            text("DELETE FROM report_fts WHERE rowid = :id"), {"id": doc}
        )
        await session.execute(
            text("DELETE FROM report_documents WHERE id = :id"), {"id": doc}
        )
    return True


async def list_document_paths(session: AsyncSession) -> list[tuple[str, str]]:
    """All ``(job_id, relative_path)`` pairs — the rebuild's absence pass input."""
    rows = (
        await session.execute(
            text("SELECT job_id, relative_path FROM report_documents ORDER BY job_id")
        )
    ).all()
    return [(str(job_id), str(path)) for job_id, path in rows]


async def resolve_issues_for_absent_paths(
    session: AsyncSession, *, present_paths: list[str], now: float
) -> int:
    """Resolve open issues whose file is no longer in the scan (§3: the problem
    is gone once the file is gone). Returns how many rows changed."""
    conditions = "resolved_at IS NULL"
    params: dict[str, object] = {"now": now}
    statements: list[TextClause] = []
    if present_paths:
        conditions += " AND relative_path NOT IN :paths"
        params["paths"] = present_paths
        statements.append(
            text(f"SELECT count(*) FROM index_issues WHERE {conditions}").bindparams(
                bindparam("paths", expanding=True)
            )
        )
        statements.append(
            text(f"UPDATE index_issues SET resolved_at = :now WHERE {conditions}").bindparams(
                bindparam("paths", expanding=True)
            )
        )
    else:
        statements.append(
            text(f"SELECT count(*) FROM index_issues WHERE {conditions}")
        )
        statements.append(
            text(f"UPDATE index_issues SET resolved_at = :now WHERE {conditions}")
        )
    affected = int((await session.execute(statements[0], params)).scalar_one())
    if affected:
        await session.execute(statements[1], params)
    return affected
