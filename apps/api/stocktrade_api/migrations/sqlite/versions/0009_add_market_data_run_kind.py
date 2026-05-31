"""add market data run kind

Revision ID: 0009_add_market_data_run_kind
Revises: 0008_app_settings
Create Date: 2026-06-01
"""
from __future__ import annotations

from alembic import op

revision = "0009_add_market_data_run_kind"
down_revision = "0008_app_settings"
branch_labels = None
depends_on = None

RUN_KINDS_WITH_MARKET_DATA = (
    "'preselect', 'market_data', 'review', 'archive', 'chart_export', "
    "'legacy_import', 'backup', 'restore', 'diagnostic'"
)
RUN_KINDS_WITHOUT_MARKET_DATA = (
    "'preselect', 'review', 'archive', 'chart_export', "
    "'legacy_import', 'backup', 'restore', 'diagnostic'"
)


def upgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_runs_kind", type_="check")
        batch_op.create_check_constraint("ck_runs_kind", f"kind in ({RUN_KINDS_WITH_MARKET_DATA})")


def downgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_runs_kind", type_="check")
        batch_op.create_check_constraint("ck_runs_kind", f"kind in ({RUN_KINDS_WITHOUT_MARKET_DATA})")
