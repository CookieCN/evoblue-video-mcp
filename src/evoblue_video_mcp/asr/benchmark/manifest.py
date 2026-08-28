"""Benchmark corpus manifest schema."""

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = ""
    language: str = Field(min_length=1)
    # An empty reference marks a silence/music-only sample: the provider must
    # emit nothing, and the sample is scored by hallucination, not CER/WER.
    reference_text: str = ""
    entities: tuple[str, ...] = ()
    audio_path: str | None = None
    # SHA-256 of the generated audio file; ties a gate report to the exact
    # bytes it measured (the audio itself is never committed to the repo).
    audio_sha256: str | None = None


class BenchmarkCorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    samples: list[BenchmarkSample] = Field(min_length=1)
