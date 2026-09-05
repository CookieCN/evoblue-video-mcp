"""Frozen FTS5 text contract — docs/FTS5_SCHEMA.md §4.

Indexing and querying MUST go through the same functions: the index side
normalizes text with :func:`normalize_for_fts`, the query side compiles pure
user text with :func:`build_match_query`. Any behavior change starts with the
contract document, then lands here — never ad hoc in a call site.
"""

import re

# Han, Hiragana/Katakana, Hangul — see contract §4 (冻结范围).
_CJK = r"一-鿿぀-ヿ가-힯"
_TOKEN = re.compile(rf"[\w{_CJK}]")


class QueryInvalidError(ValueError):
    """The user query contains no searchable token (API code ``QUERY_INVALID``)."""

    code = "QUERY_INVALID"


def normalize_for_fts(text: str) -> str:
    """Insert spaces around every CJK character and collapse whitespace.

    unicode61 treats a contiguous CJK run as one token, which would make
    multi-character Chinese words unmatchable; char-splitting at index time
    preserves adjacency so query-side phrases can require it.
    """
    spaced = re.sub(rf"([{_CJK}])", r" \1 ", text)
    return re.sub(r"\s+", " ", spaced).strip()


def denormalize_for_display(text: str) -> str:
    """Inverse of :func:`normalize_for_fts`, for DISPLAY only (never matching).

    FTS5 ``snippet()`` returns text from the normalized storage form, which
    shows CJK text as ``[李 自 然 说]`` — internal retrieval detail leaking to
    users. This re-joins whitespace adjacent to a CJK character (restoring
    natural Chinese such as ``[李自然说]``) while leaving ``[ ]``/``…`` markers
    and non-CJK wording intact. Note it is an approximation: original line
    breaks inside CJK text were collapsed by normalization and are not
    recoverable from the stored form.
    """
    text = re.sub(rf"(?<=[{_CJK}])\s+", "", text)
    return re.sub(rf"\s+(?=[{_CJK}])", "", text)


def build_match_query(query: str) -> str:
    """Compile pure user text into an FTS5 MATCH expression.

    Frozen algorithm: whitespace splits words (the user's only grouping tool);
    each word becomes ONE double-quoted phrase with its inner ``"`` doubled
    (SQL-style escape), so CJK adjacency is a phrase requirement, not a
    character AND; words join with AND; a query with no tokens is invalid.
    """
    phrases: list[str] = []
    for word in query.split():
        tokens = [t for t in normalize_for_fts(word).split(" ") if _TOKEN.search(t)]
        if not tokens:
            continue
        phrases.append('"' + " ".join(tokens).replace('"', '""') + '"')
    if not phrases:
        raise QueryInvalidError("query contains no searchable token")
    return " AND ".join(phrases)
