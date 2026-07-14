"""Add discovery pipeline orchestration state

Revision ID: d4e6f8a9b1c2
Revises: c9f0a3b7d4e5
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e6f8a9b1c2'
down_revision: Union[str, Sequence[str], None] = 'c9f0a3b7d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discovery_runs', sa.Column('current_stage', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('last_completed_stage', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('stage_results', sa.JSON(), nullable=True))
    op.add_column('discovery_runs', sa.Column('warnings', sa.JSON(), nullable=True))
    op.add_column('discovery_runs', sa.Column('error_code', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('resume_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('discovery_runs', 'resume_count')
    op.drop_column('discovery_runs', 'error_code')
    op.drop_column('discovery_runs', 'warnings')
    op.drop_column('discovery_runs', 'stage_results')
    op.drop_column('discovery_runs', 'last_completed_stage')
    op.drop_column('discovery_runs', 'current_stage')
