"""add review storage tables

Revision ID: 0003_review_storage
Revises: 0002_add_diagnostic_run_kind
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_review_storage"
down_revision = "0002_add_diagnostic_run_kind"
branch_labels = None
depends_on = None

RUN_STATUSES = "'queued', 'running', 'succeeded', 'failed', 'cancelling', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        "review_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "candidate_batch_id",
            sa.String(length=64),
            sa.ForeignKey("candidate_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pick_date", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(f"status in ({RUN_STATUSES})", name="ck_review_runs_status"),
    )
    op.create_index("ix_review_runs_pick_date", "review_runs", ["pick_date"])
    op.create_index("ix_review_runs_run_id", "review_runs", ["run_id"])
    op.create_index("ix_review_runs_candidate_batch", "review_runs", ["candidate_batch_id"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "review_run_id",
            sa.String(length=64),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("review_key", sa.String(length=120), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("reviewer", sa.String(length=80), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("review_run_id", "review_key", name="uq_reviews_run_key"),
    )
    op.create_index("ix_reviews_code_strategy", "reviews", ["code", "strategy"])
    op.create_index("ix_reviews_review_key", "reviews", ["review_key"])

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "review_run_id",
            sa.String(length=64),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("review_key", sa.String(length=120), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("rank > 0", name="ck_recommendations_rank_positive"),
        sa.UniqueConstraint("review_run_id", "rank", name="uq_recommendations_run_rank"),
        sa.UniqueConstraint("review_run_id", "review_key", name="uq_recommendations_run_key"),
    )
    op.create_index("ix_recommendations_run_rank", "recommendations", ["review_run_id", "rank"])


def downgrade() -> None:
    op.drop_index("ix_recommendations_run_rank", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index("ix_reviews_review_key", table_name="reviews")
    op.drop_index("ix_reviews_code_strategy", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ix_review_runs_candidate_batch", table_name="review_runs")
    op.drop_index("ix_review_runs_run_id", table_name="review_runs")
    op.drop_index("ix_review_runs_pick_date", table_name="review_runs")
    op.drop_table("review_runs")
