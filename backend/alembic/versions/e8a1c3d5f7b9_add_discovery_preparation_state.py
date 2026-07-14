"""Add discovery upstream preparation state

Revision ID: e8a1c3d5f7b9
Revises: d4e6f8a9b1c2
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8a1c3d5f7b9'
down_revision: Union[str, Sequence[str], None] = 'd4e6f8a9b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discovery_runs', sa.Column('preparation_status', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_current_stage', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_last_completed_stage', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_stage_results', sa.JSON(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_warnings', sa.JSON(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_error_code', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_error_message', sa.String(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_started_at', sa.DateTime(), nullable=True))
    op.add_column('discovery_runs', sa.Column('preparation_completed_at', sa.DateTime(), nullable=True))
    op.add_column(
        'discovery_runs',
        sa.Column('preparation_resume_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column('eligible_universe_snapshots', sa.Column('market_cap', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('eligible_universe_snapshots', 'market_cap')
    op.drop_column('discovery_runs', 'preparation_resume_count')
    op.drop_column('discovery_runs', 'preparation_completed_at')
    op.drop_column('discovery_runs', 'preparation_started_at')
    op.drop_column('discovery_runs', 'preparation_error_message')
    op.drop_column('discovery_runs', 'preparation_error_code')
    op.drop_column('discovery_runs', 'preparation_warnings')
    op.drop_column('discovery_runs', 'preparation_stage_results')
    op.drop_column('discovery_runs', 'preparation_last_completed_stage')
    op.drop_column('discovery_runs', 'preparation_current_stage')
    op.drop_column('discovery_runs', 'preparation_status')
