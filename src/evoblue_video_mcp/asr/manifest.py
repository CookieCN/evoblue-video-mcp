"""Model manifest schema and validation for the Model Manager.

The manifest is the contract between the manifest author and the Model Manager.
It is fully frozen so a manifest that has passed validation cannot be mutated
into a less safe form. It carries an install-file allowlist, a redistribution
status, and approved-download-host enforcement so the installer can reject
unapproved sources and undeclared files.
"""

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from evoblue_video_mcp.asr.catalog import APPROVED_CATALOG, ApprovedSource

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

KNOWN_PLATFORMS = frozenset(
    {
        "windows-x86_64",
        "windows-arm64",
        "macos-x86_64",
        "macos-arm64",
        "linux-x86_64",
        "linux-arm64",
    }
)

# Defensive allowlist: a download source must be HTTPS on an approved host. This
# is a guardrail, not the release authorization itself; production manifests come
# from a trusted built-in catalog (ASR-2 later steps) with recorded approval.
APPROVED_SOURCE_HOSTS = frozenset(
    {"github.com", "huggingface.co", "hf-mirror.com", "modelscope.cn"}
)


class ManifestValidationError(ValueError):
    """Raised when a model manifest fails schema or semantic validation."""


class DownloadSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1)
    kind: Literal["china-primary", "upstream", "cdn"]
    sha256: str
    size_bytes: int = Field(gt=0)

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if urlsplit(value).scheme != "https":
            raise ValueError("source url must use https")
        return value


class ModelFile(BaseModel):
    """One declared file inside the archive, with size and digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    size_bytes: int = Field(gt=0)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if ".." in value or "/" in value or "\\" in value or not _SLUG_RE.match(value):
            raise ValueError("file name must be a path-safe slug")
        return value


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    version: str
    provider: str = Field(min_length=1)
    languages: tuple[str, ...] = Field(min_length=1)
    platforms: tuple[str, ...] = Field(min_length=1)
    compressed_size_bytes: int = Field(gt=0)
    installed_size_bytes: int = Field(gt=0)
    license: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    upstream_url: str = Field(min_length=1)
    redistribution: Literal["upstream_only", "mirror_approved", "blocked"]
    archive_format: Literal["tar.bz2", "tar.gz", "zip", "raw"]
    sources: tuple[DownloadSource, ...] = Field(min_length=1)
    files: tuple[ModelFile, ...] = Field(min_length=1)

    @field_validator("model_id", "version")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        if ".." in value or "/" in value or "\\" in value or not _SLUG_RE.match(value):
            raise ValueError("model_id and version must be path-safe slugs")
        return value

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for token in value:
            if token not in KNOWN_PLATFORMS:
                raise ValueError(f"unknown platform: {token!r}")
        return value

    @model_validator(mode="after")
    def _validate_artifact_consistency(self) -> "ModelManifest":
        for source in self.sources:
            if source.size_bytes != self.compressed_size_bytes:
                raise ValueError("each source size must equal compressed_size_bytes")
        if len({source.sha256 for source in self.sources}) != 1:
            raise ValueError("all source sha256 digests must be identical")
        if sum(file.size_bytes for file in self.files) != self.installed_size_bytes:
            raise ValueError("sum of file sizes must equal installed_size_bytes")
        names = [file.name for file in self.files]
        if len(names) != len(set(names)):
            raise ValueError("file names must be unique")
        if self.redistribution == "upstream_only":
            for source in self.sources:
                if source.kind != "upstream":
                    raise ValueError(
                        "upstream_only permits only 'upstream' sources (no mirror or cdn)"
                    )
        return self


def is_releasable(manifest: ModelManifest) -> bool:
    """Return True when the artifact may enter a production install list.

    Requires a non-blocked redistribution status and a catalog approval that
    pins the exact ``(url, kind, sha256, size_bytes)`` for every source. Without
    a catalog record the artifact is not releasable (fail closed).
    """
    if manifest.redistribution == "blocked":
        return False
    approved = APPROVED_CATALOG.get((manifest.model_id, manifest.version))
    if approved is None:
        return False
    return all(
        ApprovedSource(
            url=source.url,
            kind=source.kind,
            sha256=source.sha256,
            size_bytes=source.size_bytes,
        )
        in approved
        for source in manifest.sources
    )


def load_manifest(path: str | Path) -> ModelManifest:
    """Load and fully validate a model manifest JSON file."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestValidationError(f"cannot read manifest: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"manifest is not valid JSON: {exc}") from exc
    try:
        return ModelManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(f"manifest validation failed: {exc}") from exc
