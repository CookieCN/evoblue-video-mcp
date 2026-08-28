"""Benchmark corpus manifest schema."""

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = ""
    language: str = Field(min_length=1)
    reference_text: str = Field(min_length=1)
    audio_path: str | None = None


class BenchmarkCorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    samples: list[BenchmarkSample] = Field(min_length=1)
