"""Structured JSON deep merge for ``mcpServers`` registries (contract §4.2).

Used by claude_desktop, workbuddy (and rendered for deepseek). The merge
assigns exactly one key — ours — inside ``mcpServers``; every other entry
survives verbatim, including shapes this tool does not understand
(``url``/``type: http``/``headers``/``env``). JSON has no comments, so the
known cost is whitespace normalization (``indent`` + trailing newline);
non-ASCII stays unescaped.

Fail-closed gates: BOM, duplicate keys at any depth (Python's last-wins
semantics would silently rewrite user data), non-object roots and non-object
``mcpServers`` containers are all ``CONFIG_UNSUPPORTED``.
"""

import json
from dataclasses import dataclass
from typing import Any, cast

from evoblue_video_mcp.application.client_config.errors import (
    ConfigWriteError,
    EntryConflictError,
    UnsupportedConfigError,
)
from evoblue_video_mcp.application.client_config.models import EntryPayload

MANAGED_KEYS: tuple[str, ...] = ("command", "args", "env")

_SERVERS_KEY = "mcpServers"


@dataclass(frozen=True)
class MergeOutcome:
    text: str
    changed: bool


def payload_entry(payload: EntryPayload) -> dict[str, object]:
    """The parsed-value shape this tool manages (contract §3)."""
    entry: dict[str, object] = {"command": payload.command, "args": list(payload.args)}
    if payload.env:
        entry["env"] = dict(payload.env)
    return entry


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise UnsupportedConfigError(f"target json has a duplicate key: {key}")
        seen[key] = value
    return seen


def read_entry(original: str, key: str) -> dict[str, object] | None:
    """Our parsed entry, or ``None`` when absent (any parse gate raises)."""
    doc = _parse(original)
    servers = doc.get(_SERVERS_KEY)
    if isinstance(servers, dict):
        entry = servers.get(key)
        if isinstance(entry, dict):
            return _effective_entry(entry)
    return None


def merge_json(
    original: str, key: str, payload: EntryPayload, *, indent: int, force: bool = False
) -> MergeOutcome:
    """Merge our entry into a JSON document, touching only our own key.

    ``force=True`` accepts the loss of unmanaged keys inside our entry
    (contract §4.3); the caller must have taken a backup before writing.
    """
    doc = _parse(original)
    servers = doc.get(_SERVERS_KEY)
    if servers is None:
        servers = {}
        doc[_SERVERS_KEY] = servers
    if not isinstance(servers, dict):
        raise UnsupportedConfigError(f"{_SERVERS_KEY} is not an object")
    expected = payload_entry(payload)
    current = servers.get(key)
    if current == expected:
        return MergeOutcome(text=original, changed=False)
    if current is not None:
        if not isinstance(current, dict):
            raise UnsupportedConfigError(
                f"existing entry {key!r} is not an object"
            )
        effective = _effective_entry(current)
        if effective == expected:
            # Nothing this tool manages differs — e.g. WorkBuddy's real-world
            # ``disabled: false`` (contract §3: 等价于不写). Keep user bytes.
            return MergeOutcome(text=original, changed=False)
        extra = tuple(
            sorted(str(k) for k in effective if str(k) not in MANAGED_KEYS)
        )
        if extra and not force:
            raise EntryConflictError(
                "existing entry carries unmanaged keys; restore or force to "
                "replace: " + ", ".join(extra),
                extra_keys=extra,
            )
    # Assignment (not replacement of the container) keeps every sibling entry
    # and — when the key already exists — its position in the document.
    servers[key] = expected
    merged_text = json.dumps(doc, indent=indent, ensure_ascii=False) + "\n"
    prove_merge(original, merged_text, key, expected)
    return MergeOutcome(text=merged_text, changed=True)


def remove_entry_json(
    original: str, key: str, *, indent: int
) -> MergeOutcome:
    """Remove our entry from the registry, keeping sibling entries verbatim."""
    doc = _parse(original)
    servers = doc.get(_SERVERS_KEY)
    if not isinstance(servers, dict) or key not in servers:
        return MergeOutcome(text=original, changed=False)
    del servers[key]
    merged_text = json.dumps(doc, indent=indent, ensure_ascii=False) + "\n"
    prove_removal(original, merged_text, key)
    return MergeOutcome(text=merged_text, changed=True)


def prove_merge(
    original_text: str, merged_text: str, key: str, expected: dict[str, object]
) -> None:
    """Contract §4.2 proof: entry equals target, siblings and order intact."""
    before = _parse(original_text)
    after = _parse(merged_text)
    after_servers = after.get(_SERVERS_KEY)
    if not isinstance(after_servers, dict) or after_servers.get(key) != expected:
        raise ConfigWriteError(
            "post-merge proof failed: entry does not equal the target payload"
        )
    _prove_intact(before, after, key, removing=False)


def prove_removal(original_text: str, merged_text: str, key: str) -> None:
    before = _parse(original_text)
    after = _parse(merged_text)
    after_servers = after.get(_SERVERS_KEY)
    if isinstance(after_servers, dict) and key in after_servers:
        raise ConfigWriteError("post-removal proof failed: entry still present")
    _prove_intact(before, after, key, removing=True)


def _prove_intact(
    before: dict[str, object],
    after: dict[str, object],
    key: str,
    *,
    removing: bool,
) -> None:
    if _prune(before, key) != _prune(after, key):
        raise ConfigWriteError(
            "post-merge proof failed: something outside the entry changed"
        )
    before_servers = before.get(_SERVERS_KEY)
    after_servers = after.get(_SERVERS_KEY)
    before_keys = list(before_servers) if isinstance(before_servers, dict) else []
    after_keys = list(after_servers) if isinstance(after_servers, dict) else []
    if removing:
        expected_keys = [name for name in before_keys if name != key]
    else:
        expected_keys = before_keys if key in before_keys else [*before_keys, key]
    if after_keys != expected_keys:
        raise ConfigWriteError("post-merge proof failed: registry order changed")


def _effective_entry(entry: dict[str, object]) -> dict[str, object]:
    """The entry as this tool sees it: an explicit ``disabled: false`` reads
    the same as an absent field (enabled is the default; contract §3)."""
    return {
        k: v
        for k, v in entry.items()
        if not (k == "disabled" and v is False)
    }


def _parse(original: str) -> dict[str, object]:
    if original.startswith("\ufeff"):
        raise UnsupportedConfigError("target json has a BOM; clients reject it too")
    if not original.strip():
        raise UnsupportedConfigError("target json is empty")
    try:
        doc = json.loads(original, object_pairs_hook=_reject_duplicates)
    except json.JSONDecodeError as exc:
        raise UnsupportedConfigError(f"target json does not parse: {exc}") from exc
    if not isinstance(doc, dict):
        raise UnsupportedConfigError("target json root is not an object")
    return doc


def _prune(doc: dict[str, object], key: str) -> dict[str, object]:
    cloned = cast("dict[str, object]", _clone(doc))
    servers = cloned.get(_SERVERS_KEY)
    if isinstance(servers, dict):
        servers.pop(key, None)
        if not servers:
            # A container that only held our entry must not read as a user
            # change — "no registry" and "registry left empty by us" compare
            # equal.
            cloned.pop(_SERVERS_KEY, None)
    return cloned


def _clone(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value
