"""workout exercise amrap

Revision ID: 9a1f5b3c7d82
Revises: 6c2d8a4e9f13
Create Date: 2026-09-28 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1f5b3c7d82'
down_revision: str | Sequence[str] | None = '6c2d8a4e9f13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('workout_exercises', schema=None) as batch_op:
        batch_op.add_column(sa.Column('amrap', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.alter_column('amrap', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('workout_exercises', schema=None) as batch_op:
        batch_op.drop_column('amrap')
