"""Add stock ranking and selection fields

Revision ID: c9f0a3b7d4e5
Revises: b8e4f6a1c2d3
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9f0a3b7d4e5'
down_revision: Union[str, Sequence[str], None] = 'b8e4f6a1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_candidate_snapshots', sa.Column('rank', sa.Integer(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('selected', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('stock_candidate_snapshots', sa.Column('selection_reason', sa.String(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('selected_at', sa.DateTime(), nullable=True))
    op.add_column('discovery_selections', sa.Column('company_id', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('symbol', sa.String(), nullable=True))
    op.add_column('discovery_selections', sa.Column('basic_industry', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('discovery_selections', 'basic_industry')
    op.drop_column('discovery_selections', 'symbol')
    op.drop_column('discovery_selections', 'company_id')
    op.drop_column('stock_candidate_snapshots', 'selected_at')
    op.drop_column('stock_candidate_snapshots', 'selection_reason')
    op.drop_column('stock_candidate_snapshots', 'selected')
    op.drop_column('stock_candidate_snapshots', 'rank')
