"""SQLAlchemy ORM models for the local SQLite store."""

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by all storage models."""


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
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
