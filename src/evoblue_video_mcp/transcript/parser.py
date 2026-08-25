"""Parse subtitle formats (VTT, SRT) into timed transcript segments."""

from evoblue_video_mcp.platforms.models import TranscriptSegment


def parse_vtt(text: str) -> list[TranscriptSegment]:
    """Parse WebVTT text into segments, skipping headers and NOTE blocks."""
    lines = text.splitlines()
    segments: list[TranscriptSegment] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            start_str, end_str = line.split("-->", 1)
            start = _parse_timestamp(start_str)
            end = _parse_timestamp(end_str.strip().split()[0])
            i += 1
            text_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            segments.append(TranscriptSegment(start=start, end=end, text=" ".join(text_lines)))
        else:
            i += 1
    return segments


def parse_srt(text: str) -> list[TranscriptSegment]:
    """Parse SubRip text into segments, skipping numeric index lines."""
    lines = text.splitlines()
    segments: list[TranscriptSegment] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            start_str, end_str = line.split("-->", 1)
            start = _parse_timestamp(start_str)
            end = _parse_timestamp(end_str.strip())
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            segments.append(TranscriptSegment(start=start, end=end, text=" ".join(text_lines)))
        else:
            i += 1
    return segments


def _parse_timestamp(value: str) -> float:
    """Parse ``HH:MM:SS.mmm``, ``MM:SS.mmm``, or comma-millisecond variants into seconds."""
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    raise ValueError(f"invalid timestamp: {value!r}")
