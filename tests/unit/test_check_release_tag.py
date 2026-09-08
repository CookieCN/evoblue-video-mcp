"""P7 review fix: the release tag gate must compare npm-normalized forms.

Regression guard for the reported P1: comparing ``v0.9.0-beta.1``'s stripped
form (``0.9.0-beta.1``) against the PEP 440 pyproject version (``0.9.0b1``)
with plain string equality always fails and would kill the release workflow
on its first job.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import check_release_tag  # noqa: E402


def test_current_tag_matches(monkeypatch, capsys) -> None:
    assert check_release_tag.main(["v0.9.0-beta.4"]) == 0
    assert "matches" in capsys.readouterr().out


def test_wrong_tag_rejected() -> None:
    assert check_release_tag.main(["v0.9.0-beta.5"]) == 1


def test_raw_pep440_form_also_matches() -> None:
    # accepting the PEP 440 spelling keeps local tooling ergonomic
    assert check_release_tag.main(["v0.9.0b4"]) == 0
