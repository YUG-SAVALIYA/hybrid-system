"""Add industry impact fields

Revision ID: c4b918d6a7f2
Revises: ab3f7c2d91e4
Create Date: 2026-07-13 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4b918d6a7f2'
down_revision: Union[str, Sequence[str], None] = 'ab3f7c2d91e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('macro_entity_impacts', sa.Column('source_parent_impact_id', sa.String(), nullable=True))
    op.add_column('macro_entity_impacts', sa.Column('relationship_to_parent_sector', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('macro_entity_impacts', 'relationship_to_parent_sector')
    op.drop_column('macro_entity_impacts', 'source_parent_impact_id')
