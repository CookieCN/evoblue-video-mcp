"""SQLAlchemy ORM models for the local SQLite store."""

from sqlalchemy import Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by all storage models."""


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    asr: Mapped[str] = mapped_column(String(32), default="auto")
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_fingerprint: Mapped[str] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)

    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancel_requested_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_retry_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class AppSettings(Base):
    """Single-row application settings; secrets live in the OS credential store, not here."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    setup_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    report_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[float] = mapped_column(Float)


class JobArtifact(Base):
    """Registry of stage artifacts, either inline JSON or a file under the artifact root."""

    __tablename__ = "job_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "artifact_type", "input_fingerprint", name="uq_job_artifact"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    artifact_type: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    storage_kind: Mapped[str] = mapped_column(String(16))  # "inline_json" | "file"
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    relative_path: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
