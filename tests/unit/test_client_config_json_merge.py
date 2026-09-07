"""P5-003: JSON deep merge — unknown shapes survive, order holds, gates close.

WorkBuddy's real registry mixes our shape with ``url``/``type: http``/
``headers``/``env`` entries the tool does not understand; the merge must
assign exactly one key and leave the rest verbatim (contract §4.2).
"""

import json

import pytest

from evoblue_video_mcp.application.client_config.errors import (
    EntryConflictError,
    UnsupportedConfigError,
)
from evoblue_video_mcp.application.client_config.merge_json import (
    merge_json,
    payload_entry,
    read_entry,
    remove_entry_json,
)
from evoblue_video_mcp.application.client_config.models import EntryPayload

PAYLOAD = EntryPayload(
    command=r"C:\venv\Scripts\python.exe", args=("-m", "evoblue_video_mcp.mcp")
)
KEY = "evoblue-video-mcp"
INDENT = 2

FOREIGN = {
    "mcpServers": {
        "remote-tool": {
            "url": "https://example.com/mcp",
            "type": "http",
            "headers": {"Authorization": "Bearer keep-secret"},
        },
        "local-tool": {"command": "node", "args": ["srv.js"], "env": {"A": "1"}},
    },
    "appSetting": {"theme": "dark"},
}


def _dump(doc: object) -> str:
    return json.dumps(doc, indent=INDENT, ensure_ascii=False) + "\n"


def test_merge_into_foreign_registry_keeps_unknown_shapes_verbatim() -> None:
    outcome = merge_json(_dump(FOREIGN), KEY, PAYLOAD, indent=INDENT)
    parsed = json.loads(outcome.text)
    assert outcome.changed is True
    assert parsed["mcpServers"]["remote-tool"] == FOREIGN["mcpServers"]["remote-tool"]
    assert parsed["mcpServers"]["local-tool"] == FOREIGN["mcpServers"]["local-tool"]
    assert parsed["appSetting"] == {"theme": "dark"}
    assert parsed["mcpServers"][KEY] == payload_entry(PAYLOAD)


def test_sibling_and_root_key_order_is_preserved() -> None:
    outcome = merge_json(_dump(FOREIGN), KEY, PAYLOAD, indent=INDENT)
    parsed = json.loads(outcome.text)
    assert list(parsed) == list(FOREIGN)
    assert list(parsed["mcpServers"]) == [
        *list(FOREIGN["mcpServers"]),
        KEY,
    ]


def test_missing_mcpServers_container_is_created() -> None:
    outcome = merge_json('{"appSetting": {}}', KEY, PAYLOAD, indent=INDENT)
    parsed = json.loads(outcome.text)
    assert parsed["mcpServers"][KEY] == payload_entry(PAYLOAD)
    assert parsed["appSetting"] == {}


def test_noop_keeps_original_bytes() -> None:
    with_entry = dict(FOREIGN)
    with_entry["mcpServers"] = {**FOREIGN["mcpServers"], KEY: payload_entry(PAYLOAD)}
    outcome = merge_json(_dump(with_entry), KEY, PAYLOAD, indent=INDENT)
    assert outcome.changed is False
    assert outcome.text == _dump(with_entry)


def test_managed_diff_replaces_in_place_keeping_position() -> None:
    doc = {
        "mcpServers": {
            "alpha": {"command": "a"},
            KEY: {"command": "old", "args": []},
            "zeta": {"command": "z"},
        }
    }
    outcome = merge_json(_dump(doc), KEY, PAYLOAD, indent=INDENT)
    parsed = json.loads(outcome.text)
    assert list(parsed["mcpServers"]) == ["alpha", KEY, "zeta"]
    assert parsed["mcpServers"][KEY] == payload_entry(PAYLOAD)


def test_workbuddy_real_shape_disabled_false_is_a_noop() -> None:
    """2026-09-07 real-machine shape: our entry plus disabled:false (contract §3)."""
    doc = {
        "mcpServers": {
            KEY: {**payload_entry(PAYLOAD), "disabled": False},
        }
    }
    original = _dump(doc)
    outcome = merge_json(original, KEY, PAYLOAD, indent=INDENT)
    assert outcome.changed is False
    assert outcome.text == original  # user bytes untouched


def test_disabled_true_is_a_deliberate_user_choice() -> None:
    doc = {"mcpServers": {KEY: {**payload_entry(PAYLOAD), "disabled": True}}}
    with pytest.raises(EntryConflictError) as excinfo:
        merge_json(_dump(doc), KEY, PAYLOAD, indent=INDENT)
    assert excinfo.value.extra_keys == ("disabled",)


def test_unmanaged_key_is_a_conflict() -> None:
    doc = {"mcpServers": {KEY: {"command": "x", "args": [], "custom": True}}}
    with pytest.raises(EntryConflictError) as excinfo:
        merge_json(_dump(doc), KEY, PAYLOAD, indent=INDENT)
    assert excinfo.value.extra_keys == ("custom",)


def test_remove_keeps_siblings_and_order() -> None:
    doc = {
        "mcpServers": {
            "alpha": {"command": "a"},
            KEY: payload_entry(PAYLOAD),
            "zeta": {"command": "z"},
        }
    }
    outcome = remove_entry_json(_dump(doc), KEY, indent=INDENT)
    parsed = json.loads(outcome.text)
    assert list(parsed["mcpServers"]) == ["alpha", "zeta"]
    assert KEY not in json.dumps(parsed)


def test_remove_when_absent_is_idempotent() -> None:
    original = _dump(FOREIGN)
    outcome = remove_entry_json(original, KEY, indent=INDENT)
    assert outcome.changed is False
    assert outcome.text == original


def test_broken_json_maps_to_unsupported_not_raw_error() -> None:
    """P2 regression: a JSON syntax error is CONFIG_UNSUPPORTED, never a
    raw JSONDecodeError escaping the stable-code contract."""
    with pytest.raises(UnsupportedConfigError, match="does not parse"):
        merge_json("{broken", KEY, PAYLOAD, indent=INDENT)


def test_non_object_entry_shape_is_unsupported() -> None:
    """P2 regression: an entry that exists but is not an object must fail
    closed instead of raising AttributeError inside the merge."""
    with pytest.raises(UnsupportedConfigError, match="not an object"):
        merge_json('{"mcpServers": {"k": [1, 2]}}', "k", PAYLOAD, indent=INDENT)


def test_gates_fail_closed() -> None:
    cases = [
        "﻿",  # type: ignore[list-item]
        '{"mcpServers": {"a": 1, "a": 2}}',  # duplicate key
        "[]",  # non-object root
        '{"mcpServers": [1]}',  # non-object registry
        "",  # empty file
    ]
    for original in cases:
        with pytest.raises(UnsupportedConfigError):
            merge_json(original, KEY, PAYLOAD, indent=INDENT)


def test_bom_json_fails_closed() -> None:
    original = "﻿" + '{"mcpServers": {}}'
    with pytest.raises(UnsupportedConfigError):
        merge_json(original, KEY, PAYLOAD, indent=INDENT)


def test_chinese_path_survives_unescaped() -> None:
    payload = EntryPayload(
        command=r"C:\用户\威尔逊\.venv\Scripts\python.exe",
        args=("-m", "evoblue_video_mcp.mcp"),
    )
    outcome = merge_json('{"mcpServers": {}}', KEY, payload, indent=INDENT)
    assert "\\u" not in outcome.text
    assert "威尔逊" in outcome.text
    parsed = json.loads(outcome.text)
    assert parsed["mcpServers"][KEY] == payload_entry(payload)


def test_read_entry_finds_or_misses() -> None:
    assert read_entry(_dump(FOREIGN), KEY) is None
    doc = {"mcpServers": {KEY: payload_entry(PAYLOAD)}}
    assert read_entry(_dump(doc), KEY) == payload_entry(PAYLOAD)
