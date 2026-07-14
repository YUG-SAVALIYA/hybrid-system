"""Expand discovery selections

Revision ID: ab3f7c2d91e4
Revises: 1f84a0c5d2a9
Create Date: 2026-07-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ab3f7c2d91e4'
down_revision: Union[str, Sequence[str], None] = '1f84a0c5d2a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discovery_selections', sa.Column('run_id', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('horizon', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('entity_type', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('entity_name', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('rank', sa.Integer(), nullable=True))
    op.add_column('discovery_selections', sa.Column('final_score', sa.Float(), nullable=True))
    op.add_column('discovery_selections', sa.Column('technical_score', sa.Float(), nullable=True))
    op.add_column('discovery_selections', sa.Column('fundamental_score', sa.Float(), nullable=True))
    op.add_column('discovery_selections', sa.Column('macro_score', sa.Float(), nullable=True))
    op.add_column('discovery_selections', sa.Column('selected', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('discovery_selections', sa.Column('selection_reason', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('calculation_details', sa.JSON(), nullable=True))
    op.add_column('discovery_selections', sa.Column('created_at', sa.DateTime(), nullable=True))
    op.add_column('discovery_selections', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.create_unique_constraint(
        'uq_discovery_selection_run_horizon_entity',
        'discovery_selections',
        ['run_id', 'horizon', 'entity_type', 'entity_name'],
    )
    op.alter_column('discovery_selections', 'run_id', nullable=False)
    op.alter_column('discovery_selections', 'horizon', nullable=False)
    op.alter_column('discovery_selections', 'entity_type', nullable=False)
    op.alter_column('discovery_selections', 'entity_name', nullable=False)


def downgrade() -> None:
    op.drop_constraint('uq_discovery_selection_run_horizon_entity', 'discovery_selections', type_='unique')
    op.drop_column('discovery_selections', 'updated_at')
    op.drop_column('discovery_selections', 'created_at')
    op.drop_column('discovery_selections', 'calculation_details')
    op.drop_column('discovery_selections', 'selection_reason')
    op.drop_column('discovery_selections', 'selected')
    op.drop_column('discovery_selections', 'macro_score')
    op.drop_column('discovery_selections', 'fundamental_score')
    op.drop_column('discovery_selections', 'technical_score')
    op.drop_column('discovery_selections', 'final_score')
    op.drop_column('discovery_selections', 'rank')
    op.drop_column('discovery_selections', 'entity_name')
    op.drop_column('discovery_selections', 'entity_type')
    op.drop_column('discovery_selections', 'horizon')
    op.drop_column('discovery_selections', 'run_id')
