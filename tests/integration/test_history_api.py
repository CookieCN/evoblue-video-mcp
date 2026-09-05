"""P3 history/search/index REST API against the frozen contract
(docs/HISTORY_SEARCH_API.md): envelope shapes, pagination, sections,
search semantics, and index diagnostics."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.reports.renderer import render_markdown
from evoblue_video_mcp.reports.schema import ReportDocument
from evoblue_video_mcp.storage.report_repository import (
    FtsBodyTexts,
    ReportIndexEntry,
    mark_report_missing,
    mark_report_stale,
    record_index_issue,
    resolve_index_issue,
    upsert_report_document,
)
from evoblue_video_mcp.storage.repository import save_app_settings
from evoblue_video_mcp.web import create_app

NOW = 1756350000.0


def _report_markdown(
    *,
    job_id: str,
    title: str,
    platform: str = "youtube",
    summary: str = "本期讲跨境电商的选品方法论。",
    transcript: str | None = "大家好今天讲选品策略。",
    comments: str | None = None,
    tags: tuple[str, ...] = ("选品", "跨境电商"),
    outline: str = "",
    marketing: str = "",
    video_information: str = "",
    author: str = "Wilson",
    url: str | None = None,
) -> str:
    doc = ReportDocument(
        analysis_id=job_id,
        source_url=url or f"https://www.youtube.com/watch?v={job_id}",
        platform=platform,
        video_id=job_id,
        title=title,
        author=author,
        analyzed_at=datetime(2026, 1, 1, tzinfo=UTC),
        summary_mode="auto",
        tags=list(tags),
        core_summary=summary,
        timeline_outline=outline,
        content_analysis=marketing,
        video_information=video_information,
        transcript=transcript,
        comment_sentiment=comments or "",
    )
    return render_markdown(doc)


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    report_dir: Path,
    *,
    job_id: str,
    title: str,
    analyzed_at: float = NOW,
    platform: str = "youtube",
    summary: str = "本期讲跨境电商的选品方法论。",
    transcript: str | None = "大家好今天讲选品策略。",
    comments: str | None = None,
    write_file: bool = True,
    tags: tuple[str, ...] = ("选品", "跨境电商"),
    outline: str = "",
    marketing: str = "",
    video_information: str = "",
    author: str = "Wilson",
    url: str | None = None,
) -> None:
    """Index one report; files land under ``report_dir`` (= settings root)."""
    content = _report_markdown(
        job_id=job_id,
        title=title,
        platform=platform,
        summary=summary,
        transcript=transcript,
        comments=comments,
        tags=tags,
        outline=outline,
        marketing=marketing,
        video_information=video_information,
        author=author,
        url=url,
    )
    relative_path = f"{job_id}.md"
    if write_file:
        path = report_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    data = content.encode("utf-8")
    entry = ReportIndexEntry(
        job_id=job_id,
        analysis_id=job_id,
        title=title,
        platform=platform,
        author=author,
        video_id=job_id,
        source_url=url or f"https://www.youtube.com/watch?v={job_id}",
        published_at=None,
        analyzed_at=analyzed_at,
        summary_mode="auto",
        tags=tags,
        summary_preview=summary[:200],
        relative_path=relative_path,
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        doc_source="pipeline",
    )
    async with session_factory() as sess:
        await upsert_report_document(
            sess,
            entry=entry,
            body=FtsBodyTexts(summary=summary, transcript=transcript or ""),
            now=NOW,
        )
        await sess.commit()


@pytest.fixture
async def seeded_client(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> httpx.AsyncClient:
    """Settings root = tmp_path; seed files are written directly under it."""
    async with session_factory() as sess:
        await save_app_settings(
            sess, setup_completed=True, now=NOW, report_directory=str(tmp_path)
        )
    app = create_app(session_factory=session_factory)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_history_list_pagination_and_filters(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    await _seed(session_factory, tmp_path, job_id="a", title="视频A", analyzed_at=NOW)
    await _seed(session_factory, tmp_path, job_id="b", title="视频B", analyzed_at=NOW + 10)
    await _seed(
        session_factory, tmp_path, job_id="c", title="视频C",
        platform="bilibili", analyzed_at=NOW + 20,
    )
    async with seeded_client as client:
        resp = await client.get("/api/history")
        filtered = (await client.get("/api/history", params={"platform": "youtube"})).json()
        paged = (await client.get("/api/history", params={"limit": 1, "offset": 1})).json()
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert [item["job_id"] for item in body["items"]] == ["c", "b", "a"]  # analyzed desc
    item = body["items"][0]
    assert item["doc_status"] == "active"
    assert item["summary_mode"] == "auto"
    assert item["tags"] == ["选品", "跨境电商"]
    assert item["file_path"] == "c.md"
    assert "core_summary" not in item  # the list never embeds summary bodies
    assert filtered["total"] == 2
    assert [i["job_id"] for i in filtered["items"]] == ["b", "a"]
    assert paged["total"] == 3 and [i["job_id"] for i in paged["items"]] == ["b"]
    assert paged["limit"] == 1 and paged["offset"] == 1


async def test_history_detail_envelope_and_404(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    await _seed(session_factory, tmp_path, job_id="a", title="视频A")
    async with seeded_client as client:
        resp = await client.get("/api/history/a")
        missing = await client.get("/api/history/unknown")
    assert resp.status_code == 200
    body = resp.json()
    assert body["core_summary"] == "本期讲跨境电商的选品方法论。"
    assert body["job_id"] == "a"
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "HISTORY_ITEM_NOT_FOUND", "message": "history item not found"}
    }


async def test_history_detail_missing_file_returns_null_summary(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    await _seed(session_factory, tmp_path, job_id="gone", title="视频G")
    async with session_factory() as sess:
        await mark_report_missing(sess, job_id="gone", now=NOW + 1)
        await sess.commit()
    async with seeded_client as client:
        resp = await client.get("/api/history/gone")
    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_status"] == "missing"
    assert body["core_summary"] is None


async def test_report_sections_truncation_and_errors(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    comments = "评论区普遍看好。" * 7000  # > 50k chars to exercise truncation
    await _seed(
        session_factory, tmp_path, job_id="a", title="视频A",
        comments="评论区普遍看好。",
        outline="00:00 开场\n02:00 方法论",
        marketing="深度分析正文。",
        video_information="UP 主:Wilson;时长 12:34",
    )
    await _seed(
        session_factory, tmp_path, job_id="big", title="大文件",
        comments=comments, transcript=None,
    )
    async with seeded_client as client:
        full = await client.get("/api/history/a/report", params={"section": "full"})
        summary = await client.get("/api/history/a/report")
        outline = await client.get("/api/history/a/report", params={"section": "outline"})
        marketing = await client.get("/api/history/a/report", params={"section": "marketing"})
        meta = await client.get("/api/history/a/report", params={"section": "metadata"})
        transcript = await client.get("/api/history/a/report", params={"section": "transcript"})
        comments_resp = await client.get("/api/history/a/report", params={"section": "comments"})
        empty_resp = await client.get(
            "/api/history/big/report", params={"section": "transcript"}
        )
        bad = await client.get("/api/history/a/report", params={"section": "nope"})
        trunc = await client.get("/api/history/big/report", params={"section": "comments"})
    assert full.status_code == 200
    assert full.json()["truncated"] is False
    assert full.json()["markdown"].startswith("---\n")
    assert summary.status_code == 200
    assert summary.json()["markdown"] == "## 核心摘要\n\n本期讲跨境电商的选品方法论。"
    assert outline.json()["markdown"] == "## 时间轴大纲\n\n00:00 开场\n02:00 方法论"
    assert marketing.json()["markdown"] == "## 内容分析\n\n深度分析正文。"
    transcript_body = transcript.json()
    assert transcript_body["markdown"] == "## 完整字幕\n\n大家好今天讲选品策略。"
    comments_body = comments_resp.json()
    assert comments_body["markdown"].startswith("## 评论风向\n\n评论区普遍看好。")
    assert comments_body["truncated"] is False
    meta_body = meta.json()
    assert meta_body["markdown"].startswith("---\n")
    assert "## 视频信息\n\nUP 主:Wilson;时长 12:34" in meta_body["markdown"]
    assert empty_resp.status_code == 404
    assert empty_resp.json()["error"]["code"] == "SECTION_NOT_AVAILABLE"
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_FILTER"
    assert trunc.json()["truncated"] is True
    assert len(trunc.json()["markdown"]) == 50_000
    assert trunc.json()["markdown"].startswith("## 评论风向\n\n")


async def test_report_file_missing_returns_410(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    # Indexed as active, but the file was removed from disk behind our back.
    await _seed(session_factory, tmp_path, job_id="ghost", title="视频G", write_file=False)
    async with seeded_client as client:
        resp = await client.get("/api/history/ghost/report", params={"section": "full"})
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "REPORT_FILE_MISSING"


async def test_search_endpoint_semantics(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    await _seed(session_factory, tmp_path, job_id="a", title="跨境电商选品策略")
    await _seed(
        session_factory, tmp_path, job_id="b", title="选择产品拆解",
        summary="本期讲如何选择产品。",
        transcript="选择产品前先做调研。",  # counter-example must not contain 选品
        tags=("运营", "方法论"),
    )
    async with seeded_client as client:
        resp = await client.get("/api/search", params={"q": "选品"})
        empty = await client.get("/api/search", params={"q": "不存在的词组"})
        invalid = await client.get("/api/search", params={"q": "!!!"})
        toolong = await client.get("/api/search", params={"q": "x" * 501})
        badlimit = await client.get("/api/search", params={"q": "选品", "limit": 51})
    assert resp.status_code == 200
    body = resp.json()
    assert [item["job_id"] for item in body["items"]] == ["a"]  # adjacency: not b
    hit = body["items"][0]
    assert hit["doc_status"] == "active"
    assert "title" in hit["matched_fields"]
    assert "[" in hit["snippet"] and "]" in hit["snippet"]
    assert empty.json()["total"] == 0 and empty.json()["items"] == []
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "QUERY_INVALID"
    assert toolong.status_code == 422
    assert badlimit.status_code == 422
    assert badlimit.json()["error"]["code"] == "INVALID_FILTER"


async def test_search_includes_stale_and_excludes_missing(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    await _seed(session_factory, tmp_path, job_id="stale", title="过期文档选品")
    await _seed(session_factory, tmp_path, job_id="gone", title="消失文档选品")
    async with session_factory() as sess:
        await mark_report_stale(sess, job_id="stale", now=NOW + 1)
        await mark_report_missing(sess, job_id="gone", now=NOW + 1)
        await sess.commit()
    async with seeded_client as client:
        body = (await client.get("/api/search", params={"q": "选品"})).json()
    ids = {item["job_id"]: item["doc_status"] for item in body["items"]}
    assert ids == {"stale": "stale"}  # stale searchable, missing gone


async def test_index_status_and_issues_endpoints(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    async with session_factory() as sess:
        await record_index_issue(
            sess,
            issue_code="MD_MISSING_FIELD",
            relative_path="broken.md",
            detail="missing title",
            now=NOW,
        )
        await record_index_issue(
            sess,
            issue_code="MD_ENCODING_ERROR",
            relative_path="other.md",
            detail="not utf-8",
            now=NOW + 1,
        )
        await sess.commit()
    async with seeded_client as client:
        issues = (await client.get("/api/index/issues")).json()
        by_code = (
            await client.get(
                "/api/index/issues", params={"issue_code": "MD_MISSING_FIELD"}
            )
        ).json()
        status = (await client.get("/api/index/status")).json()
        assert issues["total"] == 2
        assert issues["items"][0]["relative_path"] == "other.md"  # last_seen desc
        assert by_code["total"] == 1
        assert status["state"] == "idle"
        assert status["open_issues"] == 2

        async with session_factory() as sess:
            await resolve_index_issue(
                sess, issue_code="MD_MISSING_FIELD", relative_path="broken.md", now=NOW + 5
            )
            await sess.commit()
        open_only = (await client.get("/api/index/issues")).json()
        all_issues = (await client.get("/api/index/issues", params={"open": "false"})).json()
    assert open_only["total"] == 1
    assert all_issues["total"] == 2  # resolved rows stay queryable
    resolved = [item for item in all_issues["items"] if item["resolved_at"] is not None]
    assert len(resolved) == 1 and resolved[0]["issue_code"] == "MD_MISSING_FIELD"


async def test_search_snippet_from_actually_matched_column(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    """Review P2-5: a tags/author/url-only hit must snippet from ITS column,
    with highlight markers, never fall back to an unrelated summary."""
    await _seed(
        session_factory, tmp_path, job_id="tg", title="标签命中文档",
        summary="完全无关的摘要内容。",
        transcript=None,
        tags=("达人访谈", "选品调研"),
    )
    await _seed(
        session_factory, tmp_path, job_id="au", title="作者命中文档",
        summary="完全无关的摘要内容。",
        transcript=None,
        tags=("杂谈",),
        author="李自然说",
        url="https://www.youtube.com/watch?v=authorcase",
    )
    await _seed(
        session_factory, tmp_path, job_id="ur", title="链接命中文档",
        summary="完全无关的摘要内容。",
        transcript=None,
        tags=("杂谈",),
        author="别人",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    async with seeded_client as client:
        tags_hit = (await client.get("/api/search", params={"q": "选品调研"})).json()
        author_hit = (await client.get("/api/search", params={"q": "李自然说"})).json()
        url_hit = (await client.get("/api/search", params={"q": "dQw4w9WgXcQ"})).json()
    assert tags_hit["items"][0]["matched_fields"] == ["tags"]
    # snippets are display-denormalized: natural Chinese, markers intact
    assert "[选品调研]" in tags_hit["items"][0]["snippet"]
    assert author_hit["items"][0]["matched_fields"] == ["author"]
    assert "[李自然说]" in author_hit["items"][0]["snippet"]
    assert "自 然" not in author_hit["items"][0]["snippet"]
    assert url_hit["items"][0]["matched_fields"] == ["url"]
    assert "[" in url_hit["items"][0]["snippet"]


async def test_search_index_failure_maps_to_503_envelope(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    """Review P1-1: a missing/corrupt FTS table is 503 SEARCH_INDEX_UNAVAILABLE,
    not a bare 500 — clients must tell 'no results' from 'index broken'."""
    await _seed(session_factory, tmp_path, job_id="a", title="视频A")
    async with session_factory() as sess:
        await sess.execute(text("DROP TABLE report_fts"))
        await sess.commit()
    async with seeded_client as client:
        resp = await client.get("/api/search", params={"q": "选品"})
    assert resp.status_code == 503
    assert resp.json() == {
        "error": {"code": "SEARCH_INDEX_UNAVAILABLE", "message": "search index is unavailable"}
    }


async def test_p3_validation_errors_use_envelope_legacy_keep_detail(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_client: httpx.AsyncClient,
) -> None:
    """Review P1-2: FastAPI's pre-endpoint parameter validation must answer in
    the frozen envelope on P3 routes, while legacy endpoints keep the default
    ``{"detail": ...}`` shape."""
    async with seeded_client as client:
        bad_limit = await client.get("/api/search", params={"q": "选品", "limit": "abc"})
        bad_open = await client.get("/api/index/issues", params={"open": "maybe"})
        bad_history_limit = await client.get("/api/history", params={"limit": "abc"})
        legacy = await client.get("/api/jobs", params={"limit": "abc"})
    for resp in (bad_limit, bad_open, bad_history_limit):
        assert resp.status_code == 422
        assert resp.json() == {
            "error": {"code": "INVALID_FILTER", "message": "invalid query parameters"}
        }
    assert legacy.status_code == 422
    assert "detail" in legacy.json()  # legacy shape preserved
    assert "error" not in legacy.json()
