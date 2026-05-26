from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelling", "cancelled")
RUN_KINDS = ("preselect", "review", "archive", "legacy_import", "backup", "restore", "diagnostic")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(f"status in ({_sql_values(RUN_STATUSES)})", name="ck_runs_status"),
        CheckConstraint(f"kind in ({_sql_values(RUN_KINDS)})", name="ck_runs_kind"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    pick_date: Mapped[str | None] = mapped_column(String(10))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    steps: Mapped[list[JobStep]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list[JobEvent]] = relationship(back_populates="run", cascade="all, delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="run", cascade="all, delete-orphan")
    candidate_batches: Mapped[list[CandidateBatch]] = relationship(back_populates="run", cascade="all, delete-orphan")


class JobStep(Base):
    __tablename__ = "job_steps"
    __table_args__ = (
        CheckConstraint(f"status in ({_sql_values(RUN_STATUSES)})", name="ck_job_steps_status"),
        UniqueConstraint("run_id", "name", name="uq_job_steps_run_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    run: Mapped[Run] = relationship(back_populates="steps")
    events: Mapped[list[JobEvent]] = relationship(back_populates="step")


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_run_created", "run_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("job_steps.id", ondelete="SET NULL"))
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    run: Mapped[Run] = relationship(back_populates="events")
    step: Mapped[JobStep | None] = relationship(back_populates="events")


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("path", name="uq_artifacts_path"),
        Index("ix_artifacts_run_kind", "run_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    run: Mapped[Run] = relationship(back_populates="artifacts")


class CandidateBatch(Base):
    __tablename__ = "candidate_batches"
    __table_args__ = (
        Index("ix_candidate_batches_pick_date", "pick_date"),
        Index("ix_candidate_batches_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    pick_date: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="product")
    strategy_counts_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    run: Mapped[Run] = relationship(back_populates="candidate_batches")
    candidates: Mapped[list[Candidate]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("batch_id", "code", "strategy", name="uq_candidates_batch_code_strategy"),
        Index("ix_candidates_pick_strategy", "pick_date", "strategy"),
        Index("ix_candidates_code", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("candidate_batches.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    pick_date: Mapped[str] = mapped_column(String(10), nullable=False)
    close: Mapped[float | None] = mapped_column(Float)
    turnover_n: Mapped[float | None] = mapped_column(Float)
    brick_growth: Mapped[float | None] = mapped_column(Float)
    extra_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    batch: Mapped[CandidateBatch] = relationship(back_populates="candidates")
