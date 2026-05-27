"""add chart export run kind

Revision ID: 0007_add_chart_export_run_kind
Revises: 0006_migration_audit_storage
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op

revision = "0007_add_chart_export_run_kind"
down_revision = "0006_migration_audit_storage"
branch_labels = None
depends_on = None

RUN_KINDS_WITH_CHART_EXPORT = "'preselect', 'review', 'archive', 'chart_export', 'legacy_import', 'backup', 'restore', 'diagnostic'"
RUN_KINDS_WITHOUT_CHART_EXPORT = "'preselect', 'review', 'archive', 'legacy_import', 'backup', 'restore', 'diagnostic'"


def upgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_runs_kind", type_="check")
        batch_op.create_check_constraint("ck_runs_kind", f"kind in ({RUN_KINDS_WITH_CHART_EXPORT})")


def downgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_runs_kind", type_="check")
        batch_op.create_check_constraint("ck_runs_kind", f"kind in ({RUN_KINDS_WITHOUT_CHART_EXPORT})")
