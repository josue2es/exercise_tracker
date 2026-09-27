"""exercise spanish overlay

Revision ID: 5d1c7e2a9b40
Revises: 238576a4eb17
Create Date: 2026-09-27 09:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d1c7e2a9b40'
down_revision: str | Sequence[str] | None = '238576a4eb17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('exercises', schema=None) as batch_op:
        batch_op.add_column(sa.Column('name_es', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('instructions_es', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('exercises', schema=None) as batch_op:
        batch_op.drop_column('instructions_es')
        batch_op.drop_column('name_es')
