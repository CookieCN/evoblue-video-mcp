"""Frozen FTS5 text contract helpers (docs/FTS5_SCHEMA.md §4)."""

import pytest

from evoblue_video_mcp.storage.fts import (
    QueryInvalidError,
    build_match_query,
    denormalize_for_display,
    normalize_for_fts,
)


def test_denormalize_for_display_restores_natural_cjk() -> None:
    """Display-side inverse of normalization: re-join CJK, keep markers/non-CJK.

    Whitespace between two markers (``] [``) is not CJK-adjacent and stays —
    the documented approximation boundary of the display denormalization.
    """
    assert denormalize_for_display("[李 自 然 说]") == "[李自然说]"
    assert (
        denormalize_for_display("…电 商 的 [选] [品] 方 法 论…")
        == "…电商的[选] [品]方法论…"
    )
    assert denormalize_for_display("mixed 中 text 文") == "mixed中text文"
    assert denormalize_for_display("plain english words") == "plain english words"
    # round-trip: CJK re-joins fully — the original space between two CJK runs
    # was collapsed by normalization and is intentionally not recoverable.
    assert denormalize_for_display(normalize_for_fts("跨境电商 选品")) == "跨境电商选品"


def test_normalize_splits_cjk_and_collapses_whitespace() -> None:
    assert normalize_for_fts("跨境电商选品") == "跨 境 电 商 选 品"
    assert normalize_for_fts("  a  b\tc ") == "a b c"
    assert normalize_for_fts("mixed中text文") == "mixed 中 text 文"
    assert normalize_for_fts("") == ""


def test_build_match_query_keeps_cjk_word_as_one_phrase() -> None:
    # Adjacency is the Chinese word boundary — a character AND would match 选择产品.
    assert build_match_query("选品") == '"选 品"'
    assert build_match_query("跨境电商 选品") == '"跨 境 电 商" AND "选 品"'


def test_build_match_query_escapes_inner_quotes() -> None:
    assert build_match_query('foo"bar') == '"foo""bar"'


def test_build_match_query_drops_punctuation_tokens() -> None:
    assert build_match_query("选品, 策略!") == '"选 品" AND "策 略"'


def test_build_match_query_rejects_punctuation_only() -> None:
    with pytest.raises(QueryInvalidError) as excinfo:
        build_match_query("!!! ...")
    assert excinfo.value.code == "QUERY_INVALID"
