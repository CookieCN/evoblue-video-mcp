import pytest
from pydantic import ValidationError

from evoblue_video_mcp.mcp.schemas import SubmitVideoAnalysisInput


def test_submit_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitVideoAnalysisInput.model_validate(
            {"url": "https://www.youtube.com/watch?v=abc", "unexpected": True}
        )


def test_submit_schema_has_safe_defaults() -> None:
    request = SubmitVideoAnalysisInput(url="https://www.youtube.com/watch?v=abc")
    assert request.mode == "auto"
    assert request.asr == "auto"
    assert request.language is None

