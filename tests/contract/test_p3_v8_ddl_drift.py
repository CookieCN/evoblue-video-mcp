"""P3 contract drift gate: production migration v8 == frozen contract DDL.

docs/FTS5_SCHEMA.md §2 is the source of truth. If this test fails, the
contract and ``storage/migrations.py`` diverged — fix the contract first,
then transcribe its blocks back (AGENTS.md work rule: contract before code).
"""

import re
from pathlib import Path

from evoblue_video_mcp.storage import SCHEMA_VERSION
from evoblue_video_mcp.storage.migrations import _V8_STATEMENTS

_ROOT = Path(__file__).resolve().parents[2]
_SECTION_START = "## 2. 迁移 v8 DDL(冻结,逐字符转录)"
_SECTION_END = "## 3."


def _contract_ddl_blocks() -> list[str]:
    doc = (_ROOT / "docs" / "FTS5_SCHEMA.md").read_text(encoding="utf-8")
    section = doc.split(_SECTION_START)[1].split(_SECTION_END)[0]
    return [block.strip() for block in re.findall(r"```sql\n(.*?)```", section, re.S)]


def test_contract_has_ten_ddl_blocks() -> None:
    assert len(_contract_ddl_blocks()) == 10


def test_v8_statements_match_contract_character_for_character() -> None:
    assert [statement.strip() for statement in _V8_STATEMENTS] == _contract_ddl_blocks()


def test_schema_version_is_8() -> None:
    assert SCHEMA_VERSION == 8
