"""Add stock candidate snapshots

Revision ID: a7c9e2d4f1b8
Revises: f6a2d8c3b9e1
Create Date: 2026-07-13 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c9e2d4f1b8'
down_revision: Union[str, Sequence[str], None] = 'f6a2d8c3b9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'stock_candidate_snapshots',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=False),
        sa.Column('horizon', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), nullable=False),
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('sector', sa.String(), nullable=False),
        sa.Column('industry', sa.String(), nullable=False),
        sa.Column('basic_industry', sa.String(), nullable=False),
        sa.Column('technical_metric_id', sa.String(), nullable=True),
        sa.Column('fundamental_metric_id', sa.String(), nullable=True),
        sa.Column('technical_available', sa.Boolean(), nullable=False),
        sa.Column('fundamental_available', sa.Boolean(), nullable=False),
        sa.Column('eligible', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('warnings', sa.JSON(), nullable=True),
        sa.Column('calculation_details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'run_id', 'horizon', 'company_id',
            name='uq_stock_candidate_run_horizon_company',
        ),
    )


def downgrade() -> None:
    op.drop_table('stock_candidate_snapshots')
