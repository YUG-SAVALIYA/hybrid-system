"""Add stock candidate score fields

Revision ID: b8e4f6a1c2d3
Revises: a7c9e2d4f1b8
Create Date: 2026-07-13 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e4f6a1c2d3'
down_revision: Union[str, Sequence[str], None] = 'a7c9e2d4f1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_candidate_snapshots', sa.Column('technical_score', sa.Float(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('fundamental_score', sa.Float(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('inherited_macro_score', sa.Float(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('final_score', sa.Float(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('score_coverage_pct', sa.Float(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('score_status', sa.String(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('score_eligible', sa.Boolean(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('score_warnings', sa.JSON(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('score_details', sa.JSON(), nullable=True))
    op.add_column('stock_candidate_snapshots', sa.Column('scored_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('stock_candidate_snapshots', 'scored_at')
    op.drop_column('stock_candidate_snapshots', 'score_details')
    op.drop_column('stock_candidate_snapshots', 'score_warnings')
    op.drop_column('stock_candidate_snapshots', 'score_eligible')
    op.drop_column('stock_candidate_snapshots', 'score_status')
    op.drop_column('stock_candidate_snapshots', 'score_coverage_pct')
    op.drop_column('stock_candidate_snapshots', 'final_score')
    op.drop_column('stock_candidate_snapshots', 'inherited_macro_score')
    op.drop_column('stock_candidate_snapshots', 'fundamental_score')
    op.drop_column('stock_candidate_snapshots', 'technical_score')
