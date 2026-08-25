"""Subtitle parsers convert VTT/SRT into timed segments (no network)."""

from evoblue_video_mcp.transcript.parser import parse_srt, parse_vtt

VTT_SAMPLE = """WEBVTT

00:00.000 --> 00:02.000
Hello world

00:02.000 --> 00:04.500
Second line here
"""

SRT_SAMPLE = """1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:01:03,500
Second line
"""


def test_parse_vtt_basic() -> None:
    segments = parse_vtt(VTT_SAMPLE)
    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert segments[0].end == 2.0
    assert segments[0].text == "Hello world"
    assert segments[1].start == 2.0
    assert segments[1].end == 4.5
    assert segments[1].text == "Second line here"


def test_parse_vtt_ignores_header_and_cue_settings() -> None:
    text = "WEBVTT\n\n00:01.000 --> 00:03.000 align:start position:0%\nOne cue\n"
    segments = parse_vtt(text)
    assert len(segments) == 1
    assert segments[0].start == 1.0
    assert segments[0].end == 3.0
    assert segments[0].text == "One cue"


def test_parse_srt_basic() -> None:
    segments = parse_srt(SRT_SAMPLE)
    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert segments[0].end == 2.0
    assert segments[1].start == 2.0
    assert segments[1].end == 63.5
    assert segments[1].text == "Second line"
