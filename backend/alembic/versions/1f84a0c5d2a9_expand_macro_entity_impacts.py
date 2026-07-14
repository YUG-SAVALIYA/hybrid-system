"""Expand macro entity impacts

Revision ID: 1f84a0c5d2a9
Revises: a3cee42bf7ec
Create Date: 2026-07-13 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1f84a0c5d2a9'
down_revision: Union[str, Sequence[str], None] = 'a3cee42bf7ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('macro_entity_impacts', sa.Column('run_id', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('source_summary_id', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('entity_type', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('entity_name', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('parent_sector', sa.String(), nullable=False, server_default=''))
    op.add_column('macro_entity_impacts', sa.Column('parent_industry', sa.String(), nullable=False, server_default=''))
    op.add_column('macro_entity_impacts', sa.Column('category_impacts', sa.JSON(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('overall_impact', sa.JSON(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('impact', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('confidence', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('reason', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('evidence_refs', sa.JSON(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('warnings', sa.JSON(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('status', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('model_name', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('prompt_version', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('created_at', sa.DateTime(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.create_unique_constraint(
        'uq_macro_entity_impact_hierarchy',
        'macro_entity_impacts',
        ['run_id', 'entity_type', 'entity_name', 'parent_sector', 'parent_industry'],
    )
    op.alter_column('macro_entity_impacts', 'run_id', nullable=False)
    op.alter_column('macro_entity_impacts', 'entity_type', nullable=False)
    op.alter_column('macro_entity_impacts', 'entity_name', nullable=False)
    op.alter_column('macro_entity_impacts', 'status', nullable=False)


def downgrade() -> None:
    op.drop_constraint('uq_macro_entity_impact_hierarchy', 'macro_entity_impacts', type_='unique')
    op.drop_column('macro_entity_impacts', 'updated_at')
    op.drop_column('macro_entity_impacts', 'created_at')
    op.drop_column('macro_entity_impacts', 'prompt_version')
    op.drop_column('macro_entity_impacts', 'model_name')
    op.drop_column('macro_entity_impacts', 'status')
    op.drop_column('macro_entity_impacts', 'warnings')
    op.drop_column('macro_entity_impacts', 'evidence_refs')
    op.drop_column('macro_entity_impacts', 'reason')
    op.drop_column('macro_entity_impacts', 'confidence')
    op.drop_column('macro_entity_impacts', 'impact')
    op.drop_column('macro_entity_impacts', 'overall_impact')
    op.drop_column('macro_entity_impacts', 'category_impacts')
    op.drop_column('macro_entity_impacts', 'parent_industry')
    op.drop_column('macro_entity_impacts', 'parent_sector')
    op.drop_column('macro_entity_impacts', 'entity_name')
    op.drop_column('macro_entity_impacts', 'entity_type')
    op.drop_column('macro_entity_impacts', 'source_summary_id')
    op.drop_column('macro_entity_impacts', 'run_id')
