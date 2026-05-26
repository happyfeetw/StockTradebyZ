"""create product state tables

Revision ID: 0001_product_state
Revises:
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_product_state"
down_revision = None
branch_labels = None
depends_on = None

RUN_STATUSES = "'queued', 'running', 'succeeded', 'failed', 'cancelling', 'cancelled'"
RUN_KINDS = "'preselect', 'review', 'archive', 'legacy_import', 'backup', 'restore'"


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pick_date", sa.String(length=10), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(f"status in ({RUN_STATUSES})", name="ck_runs_status"),
        sa.CheckConstraint(f"kind in ({RUN_KINDS})", name="ck_runs_kind"),
    )

    op.create_table(
        "job_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(f"status in ({RUN_STATUSES})", name="ck_job_steps_status"),
        sa.UniqueConstraint("run_id", "name", name="uq_job_steps_run_name"),
    )

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.Integer(), sa.ForeignKey("job_steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_job_events_run_created", "job_events", ["run_id", "created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("path", name="uq_artifacts_path"),
    )
    op.create_index("ix_artifacts_run_kind", "artifacts", ["run_id", "kind"])

    op.create_table(
        "candidate_batches",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pick_date", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("strategy_counts_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_candidate_batches_pick_date", "candidate_batches", ["pick_date"])
    op.create_index("ix_candidate_batches_run_id", "candidate_batches", ["run_id"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(length=64), sa.ForeignKey("candidate_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("pick_date", sa.String(length=10), nullable=False),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("turnover_n", sa.Float(), nullable=True),
        sa.Column("brick_growth", sa.Float(), nullable=True),
        sa.Column("extra_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("batch_id", "code", "strategy", name="uq_candidates_batch_code_strategy"),
    )
    op.create_index("ix_candidates_pick_strategy", "candidates", ["pick_date", "strategy"])
    op.create_index("ix_candidates_code", "candidates", ["code"])


def downgrade() -> None:
    op.drop_index("ix_candidates_code", table_name="candidates")
    op.drop_index("ix_candidates_pick_strategy", table_name="candidates")
    op.drop_table("candidates")
    op.drop_index("ix_candidate_batches_run_id", table_name="candidate_batches")
    op.drop_index("ix_candidate_batches_pick_date", table_name="candidate_batches")
    op.drop_table("candidate_batches")
    op.drop_index("ix_artifacts_run_kind", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_job_events_run_created", table_name="job_events")
    op.drop_table("job_events")
    op.drop_table("job_steps")
    op.drop_table("runs")
