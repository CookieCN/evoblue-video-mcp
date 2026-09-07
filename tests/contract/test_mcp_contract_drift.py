"""P4 contract drift gate: docs/MCP_TOOLS.md ↔ mcp contract models.

The contract document is the source of truth. If this test fails, the contract
and ``evoblue_video_mcp.mcp`` diverged — fix the contract first, then mirror it
back (AGENTS.md work rule: contract before code).
"""

import json
import re
from pathlib import Path
from typing import get_args

from pydantic import RootModel, TypeAdapter

from evoblue_video_mcp.mcp.errors import STABLE_ERROR_CODES
from evoblue_video_mcp.mcp.schemas import (
    CHECK_NAMES,
    TOOL_NAMES,
    AnalysisReportResult,
    AnalysisStatusResult,
    CancelAnalysisResult,
    CheckName,
    DiagnoseEnvironmentResult,
    ListAnalysisJobsResult,
    ReportSection,
    SearchAnalysisHistoryResult,
    SubmitVideoAnalysisResult,
    ToolFailure,
)
from evoblue_video_mcp.web.app import _SECTION_HEADINGS

_ROOT = Path(__file__).resolve().parents[2]
_JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.S)

_RESULT_BY_TOOL: dict[str, type[RootModel]] = {  # type: ignore[type-arg]
    "submit_video_analysis": SubmitVideoAnalysisResult,
    "get_analysis_status": AnalysisStatusResult,
    "get_analysis_report": AnalysisReportResult,
    "list_analysis_jobs": ListAnalysisJobsResult,
    "search_analysis_history": SearchAnalysisHistoryResult,
    "cancel_analysis": CancelAnalysisResult,
    "diagnose_environment": DiagnoseEnvironmentResult,
}


def _doc() -> str:
    return (_ROOT / "docs" / "MCP_TOOLS.md").read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    return doc.split(heading, 1)[1]


def _success_example(section_text: str) -> dict[str, object]:
    after_success = section_text.split("成功", 1)[1]
    block = _JSON_BLOCK.search(after_success)
    assert block is not None, "success example block missing"
    return json.loads(block.group(1))  # type: ignore[no-any-return]


def test_doc_defines_exactly_the_seven_tool_sections_in_order() -> None:
    headings = re.findall(r"^## \d+\. ([a-z_]+)$", _doc(), re.M)
    assert headings == list(TOOL_NAMES)


def test_result_unions_serialize_flat_with_top_level_schema() -> None:
    """RootModel spike: no ``{"result": ...}`` wrap, flat payload round-trip."""
    adapter = TypeAdapter(SubmitVideoAnalysisResult)
    schema = adapter.json_schema()
    assert "oneOf" in schema
    assert "properties" not in schema
    success = {
        "ok": True,
        "job_id": "01J",
        "status": "queued",
        "next_action": "get_analysis_status",
        "reused": False,
    }
    validated = adapter.validate_python(success)
    assert validated.root.ok is True
    assert json.loads(adapter.dump_json(validated)) == success
    failure = {
        "ok": False,
        "error": {"code": "ENGINE_NOT_READY", "message": "m", "retryable": True, "detail": None},
    }
    failed = adapter.validate_python(failure)
    assert failed.root.error.code == "ENGINE_NOT_READY"


def test_generic_error_envelope_example_matches_tool_failure() -> None:
    common = _section(_doc(), "## 通用约定").split("## 0.1", 1)[0]
    envelope = json.loads(_JSON_BLOCK.search(common).group(1))  # type: ignore[union-attr]
    failure = TypeAdapter(ToolFailure).validate_python(envelope)
    assert failure.ok is False
    assert failure.error.code == "STABLE_ERROR_CODE"
    assert failure.error.retryable is False
    assert failure.error.detail is not None


def test_every_tool_success_example_validates_against_its_result() -> None:
    doc = _doc()
    for index, name in enumerate(TOOL_NAMES, start=1):
        # "\n## " (not "## ") — §3's success example embeds "## 核心摘要" mid-line.
        section = _section(doc, f"## {index}. {name}").split("\n## ", 1)[0]
        example = _success_example(section)
        validated = TypeAdapter(_RESULT_BY_TOOL[name]).validate_python(example)
        assert validated.root.ok is True, name


def test_error_code_table_matches_frozen_codes_in_order() -> None:
    table = _section(_doc(), "## 0.2").split("## 0.3", 1)[0]
    codes = re.findall(r"^\| `([A-Z_]+)` \|", table, re.M)
    assert codes == list(STABLE_ERROR_CODES)


def test_check_names_match_contract_order() -> None:
    assert list(CHECK_NAMES) == list(get_args(CheckName))
    section = _section(_doc(), "## 7.")
    line = next(ln for ln in section.splitlines() if ln.startswith("检查名冻结枚举"))
    assert re.findall(r"`([a-z_]+)`", line) == list(CHECK_NAMES)


def test_report_sections_match_web_section_headings() -> None:
    assert set(get_args(ReportSection)) == set(_SECTION_HEADINGS)
    assert set(get_args(ReportSection)) == {
        "summary",
        "outline",
        "marketing",
        "comments",
        "metadata",
        "transcript",
        "full",
    }


def test_timeout_budget_table_covers_every_tool() -> None:
    table = _section(_doc(), "## 0.3").split("## 1. ", 1)[0]
    rows = re.findall(r"^\| `([a-z_]+)`", table, re.M)
    assert set(rows) == set(TOOL_NAMES)
