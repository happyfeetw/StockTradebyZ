"""add archive storage tables

Revision ID: 0004_archive_storage
Revises: 0003_review_storage
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_archive_storage"
down_revision = "0003_review_storage"
branch_labels = None
depends_on = None

ARCHIVE_STATUSES = "'recommended', 'reviewed', 'unreviewed'"


def upgrade() -> None:
    op.create_table(
        "archive_snapshots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "candidate_batch_id",
            sa.String(length=64),
            sa.ForeignKey("candidate_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "review_run_id",
            sa.String(length=64),
            sa.ForeignKey("review_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pick_date", sa.String(length=10), nullable=False),
        sa.Column("candidate_run_date", sa.String(length=10), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("reviewed_count", sa.Integer(), nullable=False),
        sa.Column("recommended_count", sa.Integer(), nullable=False),
        sa.Column("strategy_counts_json", sa.JSON(), nullable=True),
        sa.Column("executed_strategies_json", sa.JSON(), nullable=True),
        sa.Column("min_score_threshold", sa.Float(), nullable=True),
        sa.Column("source_json", sa.JSON(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("pick_date", "run_id", name="uq_archive_snapshots_date_run"),
    )
    op.create_index("ix_archive_snapshots_pick_date", "archive_snapshots", ["pick_date"])
    op.create_index("ix_archive_snapshots_run_id", "archive_snapshots", ["run_id"])

    op.create_table(
        "archive_rows",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.String(length=64),
            sa.ForeignKey("archive_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "recommendation_id",
            sa.Integer(),
            sa.ForeignKey("recommendations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pick_date", sa.String(length=10), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("review_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("turnover_n", sa.Float(), nullable=True),
        sa.Column("brick_growth", sa.Float(), nullable=True),
        sa.Column("extra_json", sa.JSON(), nullable=True),
        sa.Column("review_payload_json", sa.JSON(), nullable=True),
        sa.Column("chart_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(f"status in ({ARCHIVE_STATUSES})", name="ck_archive_rows_status"),
        sa.CheckConstraint("rank IS NULL OR rank > 0", name="ck_archive_rows_rank_positive"),
        sa.UniqueConstraint("snapshot_id", "review_key", name="uq_archive_rows_snapshot_key"),
    )
    op.create_index("ix_archive_rows_pick_status", "archive_rows", ["pick_date", "status"])
    op.create_index("ix_archive_rows_run_id", "archive_rows", ["run_id"])
    op.create_index("ix_archive_rows_code_strategy", "archive_rows", ["code", "strategy"])
    op.create_index("ix_archive_rows_review_key", "archive_rows", ["review_key"])


def downgrade() -> None:
    op.drop_index("ix_archive_rows_review_key", table_name="archive_rows")
    op.drop_index("ix_archive_rows_code_strategy", table_name="archive_rows")
    op.drop_index("ix_archive_rows_run_id", table_name="archive_rows")
    op.drop_index("ix_archive_rows_pick_status", table_name="archive_rows")
    op.drop_table("archive_rows")
    op.drop_index("ix_archive_snapshots_run_id", table_name="archive_snapshots")
    op.drop_index("ix_archive_snapshots_pick_date", table_name="archive_snapshots")
    op.drop_table("archive_snapshots")
