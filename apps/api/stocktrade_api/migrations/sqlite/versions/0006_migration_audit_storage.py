"""add migration audit storage

Revision ID: 0006_migration_audit_storage
Revises: 0005_archive_chart_artifact_link
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_migration_audit_storage"
down_revision = "0005_archive_chart_artifact_link"
branch_labels = None
depends_on = None

RUN_STATUSES = "'queued', 'running', 'succeeded', 'failed', 'cancelling', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        "migration_runs",
        sa.Column("id", sa.String(length=64), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_root", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(f"status in ({RUN_STATUSES})", name="ck_migration_runs_status"),
    )
    op.create_index("ix_migration_runs_status", "migration_runs", ["status"])
    op.create_index("ix_migration_runs_source_root", "migration_runs", ["source_root"])

    op.create_table(
        "migration_quarantine",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "migration_run_id",
            sa.String(length=64),
            sa.ForeignKey("migration_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_path", sa.String(length=512), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_migration_quarantine_run", "migration_quarantine", ["migration_run_id"])
    op.create_index("ix_migration_quarantine_source_reason", "migration_quarantine", ["source_path", "reason"])


def downgrade() -> None:
    op.drop_index("ix_migration_quarantine_source_reason", table_name="migration_quarantine")
    op.drop_index("ix_migration_quarantine_run", table_name="migration_quarantine")
    op.drop_table("migration_quarantine")
    op.drop_index("ix_migration_runs_source_root", table_name="migration_runs")
    op.drop_index("ix_migration_runs_status", table_name="migration_runs")
    op.drop_table("migration_runs")
