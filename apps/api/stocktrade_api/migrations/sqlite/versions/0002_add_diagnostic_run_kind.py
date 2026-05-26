"""add diagnostic run kind

Revision ID: 0002_add_diagnostic_run_kind
Revises: 0001_product_state
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op

revision = "0002_add_diagnostic_run_kind"
down_revision = "0001_product_state"
branch_labels = None
depends_on = None

RUN_KINDS_WITH_DIAGNOSTIC = "'preselect', 'review', 'archive', 'legacy_import', 'backup', 'restore', 'diagnostic'"
RUN_KINDS_WITHOUT_DIAGNOSTIC = "'preselect', 'review', 'archive', 'legacy_import', 'backup', 'restore'"


def upgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_runs_kind", type_="check")
        batch_op.create_check_constraint("ck_runs_kind", f"kind in ({RUN_KINDS_WITH_DIAGNOSTIC})")


def downgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_runs_kind", type_="check")
        batch_op.create_check_constraint("ck_runs_kind", f"kind in ({RUN_KINDS_WITHOUT_DIAGNOSTIC})")
