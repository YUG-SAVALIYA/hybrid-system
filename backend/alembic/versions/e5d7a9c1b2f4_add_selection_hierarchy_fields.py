"""Add hierarchy fields to discovery selections

Revision ID: e5d7a9c1b2f4
Revises: c4b918d6a7f2
Create Date: 2026-07-13 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5d7a9c1b2f4'
down_revision: Union[str, Sequence[str], None] = 'c4b918d6a7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('discovery_selections') as batch_op:
        batch_op.add_column(sa.Column('parent_sector', sa.String(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column('parent_industry', sa.String(), nullable=False, server_default=""))
        batch_op.drop_constraint('uq_discovery_selection_run_horizon_entity', type_='unique')
        batch_op.create_unique_constraint(
            'uq_discovery_selection_hierarchy',
            ['run_id', 'horizon', 'entity_type', 'entity_name', 'parent_sector', 'parent_industry'],
        )


def downgrade() -> None:
    with op.batch_alter_table('discovery_selections') as batch_op:
        batch_op.drop_constraint('uq_discovery_selection_hierarchy', type_='unique')
        batch_op.create_unique_constraint(
            'uq_discovery_selection_run_horizon_entity',
            ['run_id', 'horizon', 'entity_type', 'entity_name'],
        )
        batch_op.drop_column('parent_industry')
        batch_op.drop_column('parent_sector')
