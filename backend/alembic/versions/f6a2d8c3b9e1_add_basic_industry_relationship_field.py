"""Add basic industry parent relationship field

Revision ID: f6a2d8c3b9e1
Revises: e5d7a9c1b2f4
Create Date: 2026-07-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a2d8c3b9e1'
down_revision: Union[str, Sequence[str], None] = 'e5d7a9c1b2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('macro_entity_impacts', sa.Column('relationship_to_parent_industry', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('macro_entity_impacts', 'relationship_to_parent_industry')
