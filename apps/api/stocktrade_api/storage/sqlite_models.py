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
ARCHIVE_STATUSES = ("recommended", "reviewed", "unreviewed")


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
    review_runs: Mapped[list[ReviewRun]] = relationship(back_populates="run", cascade="all, delete-orphan")
    archive_snapshots: Mapped[list[ArchiveSnapshot]] = relationship(back_populates="run", cascade="all, delete-orphan")


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
    candidates: Mapped[list[Candidate]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="Candidate.id",
    )
    review_runs: Mapped[list[ReviewRun]] = relationship(back_populates="candidate_batch")
    archive_snapshots: Mapped[list[ArchiveSnapshot]] = relationship(back_populates="candidate_batch")


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
    reviews: Mapped[list[Review]] = relationship(back_populates="candidate")
    archive_rows: Mapped[list[ArchiveRow]] = relationship(back_populates="candidate")


class ReviewRun(Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        CheckConstraint(f"status in ({_sql_values(RUN_STATUSES)})", name="ck_review_runs_status"),
        Index("ix_review_runs_pick_date", "pick_date"),
        Index("ix_review_runs_run_id", "run_id"),
        Index("ix_review_runs_candidate_batch", "candidate_batch_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    candidate_batch_id: Mapped[str | None] = mapped_column(ForeignKey("candidate_batches.id", ondelete="SET NULL"))
    pick_date: Mapped[str] = mapped_column(String(10), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    run: Mapped[Run] = relationship(back_populates="review_runs")
    candidate_batch: Mapped[CandidateBatch | None] = relationship(back_populates="review_runs")
    reviews: Mapped[list[Review]] = relationship(
        back_populates="review_run",
        cascade="all, delete-orphan",
        order_by="Review.id",
    )
    recommendations: Mapped[list[Recommendation]] = relationship(
        back_populates="review_run",
        cascade="all, delete-orphan",
        order_by="Recommendation.rank",
    )
    archive_snapshots: Mapped[list[ArchiveSnapshot]] = relationship(back_populates="review_run")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("review_run_id", "review_key", name="uq_reviews_run_key"),
        Index("ix_reviews_code_strategy", "code", "strategy"),
        Index("ix_reviews_review_key", "review_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    review_key: Mapped[str] = mapped_column(String(120), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(32))
    total_score: Mapped[float | None] = mapped_column(Float)
    reviewer: Mapped[str | None] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    review_run: Mapped[ReviewRun] = relationship(back_populates="reviews")
    candidate: Mapped[Candidate | None] = relationship(back_populates="reviews")
    recommendation: Mapped[Recommendation | None] = relationship(back_populates="review")
    archive_rows: Mapped[list[ArchiveRow]] = relationship(back_populates="review")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("rank > 0", name="ck_recommendations_rank_positive"),
        UniqueConstraint("review_run_id", "rank", name="uq_recommendations_run_rank"),
        UniqueConstraint("review_run_id", "review_key", name="uq_recommendations_run_key"),
        Index("ix_recommendations_run_rank", "review_run_id", "rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id", ondelete="SET NULL"))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    review_key: Mapped[str] = mapped_column(String(120), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(32))
    total_score: Mapped[float | None] = mapped_column(Float)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    review_run: Mapped[ReviewRun] = relationship(back_populates="recommendations")
    review: Mapped[Review | None] = relationship(back_populates="recommendation")
    archive_rows: Mapped[list[ArchiveRow]] = relationship(back_populates="recommendation")


class ArchiveSnapshot(Base):
    __tablename__ = "archive_snapshots"
    __table_args__ = (
        UniqueConstraint("pick_date", "run_id", name="uq_archive_snapshots_date_run"),
        Index("ix_archive_snapshots_pick_date", "pick_date"),
        Index("ix_archive_snapshots_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    candidate_batch_id: Mapped[str | None] = mapped_column(ForeignKey("candidate_batches.id", ondelete="SET NULL"))
    review_run_id: Mapped[str | None] = mapped_column(ForeignKey("review_runs.id", ondelete="SET NULL"))
    pick_date: Mapped[str] = mapped_column(String(10), nullable=False)
    candidate_run_date: Mapped[str | None] = mapped_column(String(10))
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommended_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy_counts_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    executed_strategies_json: Mapped[list[str] | None] = mapped_column(JSON)
    min_score_threshold: Mapped[float | None] = mapped_column(Float)
    source_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    run: Mapped[Run] = relationship(back_populates="archive_snapshots")
    candidate_batch: Mapped[CandidateBatch | None] = relationship(back_populates="archive_snapshots")
    review_run: Mapped[ReviewRun | None] = relationship(back_populates="archive_snapshots")
    rows: Mapped[list[ArchiveRow]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="ArchiveRow.id",
    )


class ArchiveRow(Base):
    __tablename__ = "archive_rows"
    __table_args__ = (
        CheckConstraint(f"status in ({_sql_values(ARCHIVE_STATUSES)})", name="ck_archive_rows_status"),
        CheckConstraint("rank IS NULL OR rank > 0", name="ck_archive_rows_rank_positive"),
        UniqueConstraint("snapshot_id", "review_key", name="uq_archive_rows_snapshot_key"),
        Index("ix_archive_rows_pick_status", "pick_date", "status"),
        Index("ix_archive_rows_run_id", "run_id"),
        Index("ix_archive_rows_code_strategy", "code", "strategy"),
        Index("ix_archive_rows_review_key", "review_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("archive_snapshots.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"))
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id", ondelete="SET NULL"))
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id", ondelete="SET NULL"))
    pick_date: Mapped[str] = mapped_column(String(10), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    review_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    close: Mapped[float | None] = mapped_column(Float)
    turnover_n: Mapped[float | None] = mapped_column(Float)
    brick_growth: Mapped[float | None] = mapped_column(Float)
    extra_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    chart_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    snapshot: Mapped[ArchiveSnapshot] = relationship(back_populates="rows")
    candidate: Mapped[Candidate | None] = relationship(back_populates="archive_rows")
    review: Mapped[Review | None] = relationship(back_populates="archive_rows")
    recommendation: Mapped[Recommendation | None] = relationship(back_populates="archive_rows")
