"""P5-003: TOML surgical merge — span scan, decision table, proofs.

The merge must touch only our own span (comments and foreign tables survive
byte-for-byte, contract §4.1) and fail closed on shapes it cannot map.
Counter-examples are included deliberately (experience #6): inputs a naive
implementation would silently accept — dotted-key definitions, duplicate
tables, fake headers inside multi-line strings.
"""

import tomllib

import pytest

from evoblue_video_mcp.application.client_config.errors import (
    EntryConflictError,
    UnsupportedConfigError,
)
from evoblue_video_mcp.application.client_config.merge_toml import (
    merge_toml,
    payload_table,
    read_entry,
    remove_entry_toml,
)
from evoblue_video_mcp.application.client_config.models import EntryPayload

PAYLOAD = EntryPayload(
    command=r"C:\venv\Scripts\python.exe", args=("-m", "evoblue_video_mcp.mcp")
)
KEY = "evoblue-video"

FOREIGN = """\
# User's own notes stay here.
model = "gpt-5"

[mcp_servers.other]
command = "node"
args = ["server.js"]

[mcp_servers.other.env]
DEBUG = "1"
"""


def _merged(text: str) -> dict[str, object]:
    return tomllib.loads(text)  # type: ignore[no-any-return]


def test_append_into_empty_file() -> None:
    outcome = merge_toml("", KEY, PAYLOAD)
    assert outcome.changed is True
    assert outcome.text == (
        '[mcp_servers.evoblue-video]\ncommand = "C:\\\\venv\\\\Scripts\\\\python.exe"\n'
        'args = ["-m", "evoblue_video_mcp.mcp"]\n'
    )


def test_append_into_file_without_trailing_newline() -> None:
    original = FOREIGN.rstrip("\n")
    outcome = merge_toml(original, KEY, PAYLOAD)
    assert outcome.changed is True
    assert outcome.text.startswith(original + "\n")
    parsed = _merged(outcome.text)
    assert parsed["model"] == "gpt-5"  # type: ignore[typeddict-item]
    assert KEY in parsed["mcp_servers"]  # type: ignore[typeddict-item]


def test_append_preserves_comments_and_foreign_tables_byte_for_byte() -> None:
    outcome = merge_toml(FOREIGN, KEY, PAYLOAD)
    assert outcome.text.startswith(FOREIGN)
    assert outcome.text[len(FOREIGN) :].startswith("\n[")  # blank line then block


def test_append_into_crlf_file_uses_crlf_for_new_lines_only() -> None:
    original = FOREIGN.replace("\n", "\r\n")
    outcome = merge_toml(original, KEY, PAYLOAD)
    assert "\r\n[mcp_servers.evoblue-video]\r\n" in outcome.text
    # retained lines keep their own terminators: the prefix is untouched
    assert outcome.text.startswith(original)


def test_noop_when_entry_matches_keeps_bytes_untouched() -> None:
    with_entry = FOREIGN + (
        '[mcp_servers.evoblue-video]\ncommand = "C:\\\\venv\\\\Scripts\\\\python.exe"\n'
        'args = ["-m", "evoblue_video_mcp.mcp"]\n'
    )
    outcome = merge_toml(with_entry, KEY, PAYLOAD)
    assert outcome.changed is False
    assert outcome.text == with_entry


def test_managed_diff_replaces_only_our_span() -> None:
    original = (
        "# keep me\n"
        + FOREIGN
        + "[mcp_servers.evoblue-video]\n"
        + 'command = "C:\\\\old\\\\python.exe"\n'
        + 'args = ["-m", "stale.module"]\n'
    )
    outcome = merge_toml(original, KEY, PAYLOAD)
    assert outcome.changed is True
    assert outcome.text.startswith("# keep me\n" + FOREIGN)
    parsed = _merged(outcome.text)
    entry = parsed["mcp_servers"][KEY]  # type: ignore[typeddict-item]
    assert entry == payload_table(PAYLOAD)


def test_replace_covers_our_own_env_subtable() -> None:
    original = (
        FOREIGN
        + "[mcp_servers.evoblue-video]\n"
        + 'command = "C:\\\\old\\\\python.exe"\n'
        + "args = []\n"
        + "\n[mcp_servers.evoblue-video.env]\n"
        + 'EVOBLUE_ENGINE_PORT = "1"\n'
    )
    outcome = merge_toml(original, KEY, PAYLOAD)
    parsed = _merged(outcome.text)
    entry = parsed["mcp_servers"][KEY]  # type: ignore[typeddict-item]
    assert entry == payload_table(PAYLOAD)  # env subtable fully replaced


def test_replace_keeps_foreign_nested_subtable_intact() -> None:
    original = (
        "[mcp_servers.other]\n"
        'command = "node"\n'
        + "\n[mcp_servers.other.env]\n"
        + 'DEBUG = "1"\n'
        + "\n[mcp_servers.evoblue-video]\n"
        + 'command = "old"\n'
        + "args = []\n"
    )
    outcome = merge_toml(original, KEY, PAYLOAD)
    parsed = _merged(outcome.text)
    assert parsed["mcp_servers"]["other"] == {  # type: ignore[typeddict-item]
        "command": "node",
        "env": {"DEBUG": "1"},
    }
    assert parsed["mcp_servers"][KEY] == payload_table(PAYLOAD)  # type: ignore[typeddict-item]


def test_quoted_header_is_recognized_for_replacement() -> None:
    original = '[mcp_servers."evoblue-video"]\ncommand = "old"\nargs = []\n'
    outcome = merge_toml(original, KEY, PAYLOAD)
    assert outcome.changed is True
    assert "old" not in outcome.text
    assert _merged(outcome.text)["mcp_servers"][KEY] == payload_table(PAYLOAD)  # type: ignore[typeddict-item]


def test_header_with_inline_comment_and_leading_whitespace() -> None:
    original = '  [mcp_servers.evoblue-video]  # ours\ncommand = "old"\nargs = []\n'
    outcome = merge_toml(original, KEY, PAYLOAD)
    assert _merged(outcome.text)["mcp_servers"][KEY] == payload_table(PAYLOAD)  # type: ignore[typeddict-item]


def test_fake_header_inside_multiline_string_is_not_a_boundary() -> None:
    original = (
        '[notes]\n'
        'text = """\n'
        "[mcp_servers.evoblue-video]\n"
        'not = "a table"\n'
        '"""\n'
        "[other]\n"
        'x = 1\n'
    )
    outcome = merge_toml(original, KEY, PAYLOAD)
    parsed = _merged(outcome.text)
    # TOML trims the newline right after the opening delimiter
    assert parsed["notes"]["text"].startswith("[mcp_servers.")  # type: ignore[typeddict-item]
    assert KEY in parsed["mcp_servers"]  # type: ignore[typeddict-item]
    # appended at EOF, after [other]
    assert outcome.text.rstrip("\n").endswith('args = ["-m", "evoblue_video_mcp.mcp"]')


def test_parse_failure_fails_closed() -> None:
    with pytest.raises(UnsupportedConfigError):
        merge_toml("[mcp_servers.evoblue-video\nbroken", KEY, PAYLOAD)


def test_duplicate_table_fails_closed() -> None:
    duplicate = '[a]\nx = 1\n\n[a]\ny = 2\n'
    with pytest.raises(UnsupportedConfigError):
        merge_toml(duplicate, KEY, PAYLOAD)


def test_dotted_key_entry_fails_closed() -> None:
    dotted = '[mcp_servers]\nevoblue-video.command = "x"\n'
    with pytest.raises(UnsupportedConfigError):
        merge_toml(dotted, KEY, PAYLOAD)


def test_unmanaged_key_in_our_entry_is_a_conflict() -> None:
    original = (
        '[mcp_servers.evoblue-video]\ncommand = "x"\nargs = []\n'
        'startup_timeout_ms = 9000\n'
    )
    with pytest.raises(EntryConflictError) as excinfo:
        merge_toml(original, KEY, PAYLOAD)
    assert excinfo.value.extra_keys == ("startup_timeout_ms",)


def test_remove_leaves_everything_else_byte_for_byte() -> None:
    original = (
        "# header comment\n"
        + FOREIGN
        + "[mcp_servers.evoblue-video]\n"
        + 'command = "x"\n'
        + "args = []\n"
        + "\n[mcp_servers.evoblue-video.env]\n"
        + 'PORT = "1"\n'
    )
    outcome = remove_entry_toml(original, KEY)
    assert outcome.changed is True
    assert "evoblue-video" not in outcome.text
    assert outcome.text.startswith("# header comment\n" + FOREIGN)
    parsed = _merged(outcome.text)
    assert parsed["mcp_servers"] == {  # type: ignore[typeddict-item]
        "other": {"command": "node", "args": ["server.js"], "env": {"DEBUG": "1"}}
    }


def test_remove_when_entry_is_last_table() -> None:
    original = "[other]\nx = 1\n\n[mcp_servers.evoblue-video]\ncommand = 'x'\nargs = []\n"
    outcome = remove_entry_toml(original, KEY)
    assert outcome.text == "[other]\nx = 1\n\n"


def test_read_entry_finds_or_misses() -> None:
    assert read_entry(FOREIGN, KEY) is None
    original = "[mcp_servers.evoblue-video]\ncommand = 'x'\nargs = []\n"
    assert read_entry(original, KEY) == {"command": "x", "args": []}


def test_proof_rejects_a_tampered_merge() -> None:
    """Counter-example: the proof must catch a merge that leaked user data."""
    from evoblue_video_mcp.application.client_config.merge_toml import prove_merge
    from evoblue_video_mcp.application.client_config.render import render_toml_entry

    original = '[other]\nx = 1\n'
    # correct entry, but the foreign table's value was silently rewritten
    tampered = '[other]\nx = 999\n' + render_toml_entry(KEY, PAYLOAD)
    with pytest.raises(Exception, match="outside the entry"):
        prove_merge(original, tampered, KEY, payload_table(PAYLOAD))
