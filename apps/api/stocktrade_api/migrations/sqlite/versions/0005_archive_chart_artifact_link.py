"""add archive row chart artifact link

Revision ID: 0005_archive_chart_artifact_link
Revises: 0004_archive_storage
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_archive_chart_artifact_link"
down_revision = "0004_archive_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("archive_rows") as batch_op:
        batch_op.add_column(sa.Column("chart_artifact_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_archive_rows_chart_artifact_id_artifacts",
            "artifacts",
            ["chart_artifact_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("archive_rows") as batch_op:
        batch_op.drop_constraint("fk_archive_rows_chart_artifact_id_artifacts", type_="foreignkey")
        batch_op.drop_column("chart_artifact_id")
