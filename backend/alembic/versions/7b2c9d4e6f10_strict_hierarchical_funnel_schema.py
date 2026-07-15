"""Strict hierarchical funnel schema fixes

Revision ID: 7b2c9d4e6f10
Revises: e8a1c3d5f7b9
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b2c9d4e6f10"
down_revision: Union[str, Sequence[str], None] = "e8a1c3d5f7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_technical_metrics",
        sa.Column("final_technical_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "company_technical_metrics",
        sa.Column("technical_status", sa.String(), nullable=True),
    )
    op.add_column(
        "company_technical_metrics",
        sa.Column("technical_eligible_for_selection", sa.Boolean(), nullable=True),
    )

    op.add_column(
        "macro_entity_impacts",
        sa.Column("horizon", sa.String(), nullable=False, server_default=""),
    )
    op.drop_constraint(
        "uq_macro_entity_impact_hierarchy",
        "macro_entity_impacts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_macro_entity_impact_hierarchy",
        "macro_entity_impacts",
        [
            "run_id",
            "horizon",
            "entity_type",
            "entity_name",
            "parent_sector",
            "parent_industry",
        ],
    )

    op.execute("DELETE FROM group_scores WHERE horizon = '1Y'")


def downgrade() -> None:
    op.drop_constraint(
        "uq_macro_entity_impact_hierarchy",
        "macro_entity_impacts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_macro_entity_impact_hierarchy",
        "macro_entity_impacts",
        ["run_id", "entity_type", "entity_name", "parent_sector", "parent_industry"],
    )
    op.drop_column("macro_entity_impacts", "horizon")
    op.drop_column("company_technical_metrics", "technical_eligible_for_selection")
    op.drop_column("company_technical_metrics", "technical_status")
    op.drop_column("company_technical_metrics", "final_technical_score")
