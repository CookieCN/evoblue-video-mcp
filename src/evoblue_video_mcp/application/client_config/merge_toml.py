"""Zero-dependency surgical TOML merge for Codex config.toml (contract §4.1).

No TOML writer exists in the dependency set (only read-only ``tomllib``), and
the file carries comments plus foreign server tables. The merge therefore
edits *text spans*: a bracket-depth scanner locates the
``[mcp_servers.<key>]`` header (and its whole subtree, env subtables
included), then the span is spliced. Everything outside the span survives
byte-for-byte — comments included — which is what makes 「注释与其他 server
未动」 true by construction. The ``tomllib``-based proof (:func:`prove_merge`)
is the second, independent line of defense, and it is what the post-commit
read-back re-runs.

Authority is always the ``tomllib`` parse, never the line scan: the scan only
finds the span; the append/replace/no-op/conflict decision reads parsed
values. Anything the scanner cannot map cleanly (duplicate tables, dotted-key
definitions of our entry) fails closed to ``CONFIG_UNSUPPORTED``.
"""

import tomllib
from dataclasses import dataclass
from typing import cast

from evoblue_video_mcp.application.client_config.errors import (
    ConfigWriteError,
    EntryConflictError,
    UnsupportedConfigError,
)
from evoblue_video_mcp.application.client_config.models import EntryPayload
from evoblue_video_mcp.application.client_config.render import render_toml_entry

#: The keys this tool owns inside its entry (contract §3). Anything else a
#: user added to our table is unmanaged — replacing silently would drop it.
MANAGED_KEYS: tuple[str, ...] = ("command", "args", "env")

_TABLE_ROOT = "mcp_servers"


@dataclass(frozen=True)
class MergeOutcome:
    text: str
    changed: bool


@dataclass(frozen=True)
class _Header:
    path: tuple[str, ...]
    line_start: int  # char offset of the line start
    line_end: int  # char offset just past the line (including its newline)


def payload_table(payload: EntryPayload) -> dict[str, object]:
    """The parsed-value shape this tool manages (contract §3)."""
    table: dict[str, object] = {"command": payload.command, "args": list(payload.args)}
    if payload.env:
        table["env"] = dict(payload.env)
    return table


def read_entry(original: str, key: str) -> dict[str, object] | None:
    """Our parsed entry, or ``None`` when absent (parse failure raises)."""
    servers = _parse(original).get(_TABLE_ROOT)
    if isinstance(servers, dict):
        entry = servers.get(key)
        if isinstance(entry, dict):
            return entry
    return None


def entry_is_addressable(original: str, key: str) -> bool:
    """Whether an existing entry has a standard ``[mcp_servers.<key>]`` span.

    Callers use this to fail closed on dotted-key definitions: an entry the
    parse can see but no span can locate supports no operation (install would
    claim no-op while remove fails) — contract §4.1. Only meaningful when
    :func:`read_entry` is not ``None``.
    """
    try:
        _find_span(original, key)
    except UnsupportedConfigError:
        return False
    return True


def merge_toml(
    original: str, key: str, payload: EntryPayload, *, force: bool = False
) -> MergeOutcome:
    """Merge our entry into a TOML document, touching only our own span.

    ``force=True`` accepts the loss of unmanaged keys inside our entry
    (contract §4.3: 备份 + 恢复兜底的显式替换) — the caller must have taken
    a backup before writing.
    """
    parsed = _parse(original)
    expected = payload_table(payload)
    servers = parsed.get(_TABLE_ROOT)
    current = servers.get(key) if isinstance(servers, dict) else None
    if current is None:
        merged = _append_block(original, render_toml_entry(key, payload))
        prove_merge(original, merged, key, expected)
        return MergeOutcome(text=merged, changed=True)
    # The entry exists in the parse — it must also be addressable as a
    # standard table span before ANY decision. A dotted-key definition equal
    # to the payload would otherwise sail through install/verify as a no-op
    # and then fail remove (contract §4.1: fail closed).
    _find_span(original, key)
    if current == expected:
        return MergeOutcome(text=original, changed=False)
    extra = tuple(sorted(str(k) for k in current if str(k) not in MANAGED_KEYS))
    if extra and not force:
        raise EntryConflictError(
            "existing entry carries unmanaged keys; restore or force to replace: "
            + ", ".join(extra),
            extra_keys=extra,
        )
    merged = _replace_span(original, key, render_toml_entry(key, payload))
    prove_merge(original, merged, key, expected)
    return MergeOutcome(text=merged, changed=True)


def remove_entry_toml(original: str, key: str) -> MergeOutcome:
    """Remove our entry (span splice), leaving every other byte in place."""
    _parse(original)
    span, end = _find_span(original, key)
    merged = original[: span.line_start] + original[end:]
    prove_removal(original, merged, key)
    return MergeOutcome(text=merged, changed=True)


def prove_merge(
    original_text: str, merged_text: str, key: str, expected: dict[str, object]
) -> None:
    """Contract §4.1 proof, run pre-write and again on post-commit bytes.

    ① merged parses; ② our entry equals the target payload; ③ deleting our
    entry from both documents leaves deep-equal rest documents.
    """
    merged = _parse(merged_text)
    servers = merged.get(_TABLE_ROOT)
    if not isinstance(servers, dict) or servers.get(key) != expected:
        raise ConfigWriteError(
            "post-merge proof failed: entry does not equal the target payload"
        )
    if _prune(_parse(original_text), key) != _prune(merged, key):
        raise ConfigWriteError(
            "post-merge proof failed: something outside the entry changed"
        )


def prove_removal(original_text: str, merged_text: str, key: str) -> None:
    """Proof for the remove path: entry gone, everything else untouched."""
    merged = _parse(merged_text)
    servers = merged.get(_TABLE_ROOT)
    if isinstance(servers, dict) and key in servers:
        raise ConfigWriteError("post-removal proof failed: entry still present")
    if _prune(_parse(original_text), key) != _prune(merged, key):
        raise ConfigWriteError(
            "post-removal proof failed: something outside the entry changed"
        )


def _parse(text: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UnsupportedConfigError(f"target toml does not parse: {exc}") from exc


def _prune(doc: dict[str, object], key: str) -> dict[str, object]:
    cloned = cast("dict[str, object]", _clone(doc))
    servers = cloned.get(_TABLE_ROOT)
    if isinstance(servers, dict):
        servers.pop(key, None)
        if not servers:
            # A container that only held our entry must not read as a user
            # change — "no table" and "table left empty by us" compare equal.
            cloned.pop(_TABLE_ROOT, None)
    return cloned


def _clone(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value


def _append_block(original: str, block: str) -> str:
    newline = _dominant_newline(original)
    adapted = block.rstrip("\n").replace("\n", newline)
    if original == "":
        return adapted + newline
    base = original if original.endswith("\n") else original + newline
    return base + newline + adapted + newline


def _replace_span(original: str, key: str, block: str) -> str:
    span, end = _find_span(original, key)
    adapted = block.replace("\n", _dominant_newline(original))
    return original[: span.line_start] + adapted + original[end:]


def _dominant_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _find_span(original: str, key: str) -> tuple[_Header, int]:
    """Locate our header and the char offset where our subtree ends.

    Headers inside our own subtree (``[mcp_servers.<key>.env]`` and deeper)
    do not end the span — forgetting that would orphan those subtables on
    remove and keep stale values on replace.
    """
    headers = _scan_headers(original)
    for index, header in enumerate(headers):
        if header.path[:2] == (_TABLE_ROOT, key) and len(header.path) == 2:
            end = len(original)
            for later in headers[index + 1 :]:
                if later.path[:2] != (_TABLE_ROOT, key):
                    end = later.line_start
                    break
            return header, end
    raise UnsupportedConfigError(
        "entry exists in parse but has no [mcp_servers.<key>] header "
        "(dotted-key definition is unsupported)"
    )


@dataclass
class _ScanState:
    depth: int = 0
    in_multiline: str | None = None  # the active '"""' or "'''" delimiter


def _scan_headers(original: str) -> list[_Header]:
    """All table headers with their char offsets, via a bracket-depth scan.

    Handles quoted strings (with ``"`` escapes), triple-quoted multi-line
    strings, comments and ``[[array-of-tables]]``. A header is only recorded
    when its ``[`` sits at depth 0 after only whitespace on the line, and the
    matching ``]`` returns to depth 0 with only whitespace/comment after it.
    """
    headers: list[_Header] = []
    state = _ScanState()
    offset = 0
    for raw_line in original.splitlines(keepends=True):
        line_start = offset
        offset += len(raw_line)
        path = _scan_line(raw_line.rstrip("\r\n"), state)
        if path is not None and state.depth == 0 and state.in_multiline is None:
            headers.append(_Header(path=path, line_start=line_start, line_end=offset))
    return headers


def _scan_line(line: str, state: _ScanState) -> tuple[str, ...] | None:
    """Advance the scanner over one line; return the header path it opened."""
    if state.in_multiline is not None:
        closer = line.find(state.in_multiline)
        if closer == -1:
            return None
        line = line[closer + 3 :]
        state.in_multiline = None

    n = len(line)
    i = 0
    seen_non_ws = False
    candidate_start: int | None = None
    candidate: tuple[str, ...] | None = None
    while i < n:
        ch = line[i]
        if line.startswith('"""', i) or line.startswith("'''", i):
            delimiter = line[i : i + 3]
            closer = line.find(delimiter, i + 3)
            if closer == -1:
                state.in_multiline = delimiter
                return None
            i = closer + 3
            seen_non_ws = True
            continue
        if ch in ('"', "'"):
            i = _string_end(line, i)
            seen_non_ws = True
            continue
        if ch == "#":
            break  # comment: nothing else matters on this line
        if ch == "[":
            if state.depth == 0 and not seen_non_ws and candidate_start is None:
                candidate_start = i + 1
            state.depth += 1
            seen_non_ws = True
            i += 1
            continue
        if ch == "]":
            state.depth = max(0, state.depth - 1)
            if candidate_start is not None and state.depth == 0 and candidate is None:
                candidate = _parse_key_path(line[candidate_start:i])
                if candidate is None:
                    candidate_start = None  # header-shaped but not a header
            seen_non_ws = True
            i += 1
            continue
        if candidate is not None and not ch.isspace():
            candidate = None  # junk after the closing bracket: not a header
            candidate_start = None
        if not ch.isspace():
            seen_non_ws = True
        i += 1
    if candidate is not None and state.depth == 0 and state.in_multiline is None:
        return candidate
    return None


def _string_end(text: str, start: int) -> int:
    """Index just past the single-line string opening at ``start``."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text)  # unterminated: the parse gate will catch it


def _parse_key_path(raw: str) -> tuple[str, ...] | None:
    """Parse ``mcp_servers."evoblue-video"`` into normalized segments.

    Returns ``None`` for header-shaped but invalid content (``[]``, empty
    segments, unterminated quoted keys) — the line is then treated as data.
    """
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    segments: list[str] = []
    i = 0
    n = len(text)
    while True:
        while i < n and (text[i] == "." or text[i].isspace()):
            i += 1
        if i >= n:
            return tuple(segments) if segments else None
        ch = text[i]
        if ch in ('"', "'"):
            close = text.find(ch, i + 1)
            if close == -1:
                return None
            segments.append(text[i + 1 : close])
            i = close + 1
            if i < n and text[i] != ".":
                return None
            continue
        start = i
        while i < n and (text[i].isalnum() or text[i] in "_-"):
            i += 1
        if i == start:
            return None
        segments.append(text[start:i])
